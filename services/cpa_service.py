"""CLIProxyAPI integration — fetch access_tokens from multiple remote CPA instances."""

from __future__ import annotations

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any

from curl_cffi.requests import Session

from services.config import config, DATA_DIR


CPA_CONFIG_FILE = DATA_DIR / "cpa_config.json"


# ── Pool entry type ──────────────────────────────────────────────────

def _new_pool_id() -> str:
    return uuid.uuid4().hex[:12]


def _normalize_pool(raw: dict) -> dict:
    return {
        "id": str(raw.get("id") or _new_pool_id()).strip(),
        "name": str(raw.get("name") or "").strip(),
        "base_url": str(raw.get("base_url") or "").strip(),
        "secret_key": str(raw.get("secret_key") or "").strip(),
        "enabled": bool(raw.get("enabled", True)),
    }


def _is_pool_usable(pool: dict) -> bool:
    return bool(pool.get("enabled") and pool.get("base_url") and pool.get("secret_key"))


# ── Persisted multi-pool config ─────────────────────────────────────

class CPAConfig:
    """Manages a list of CPA pool entries persisted to ``cpa_config.json``."""

    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = Lock()
        self._pools: list[dict] = self._load()

    # -- persistence ---------------------------------------------------

    def _load(self) -> list[dict]:
        if not self._store_file.exists():
            return []
        try:
            raw = json.loads(self._store_file.read_text(encoding="utf-8"))
            # Migrate from old single-pool format
            if isinstance(raw, dict) and "base_url" in raw:
                pool = _normalize_pool(raw)
                if pool["base_url"]:
                    return [pool]
                return []
            if isinstance(raw, list):
                return [_normalize_pool(item) for item in raw if isinstance(item, dict)]
        except Exception:
            pass
        return []

    def _save(self) -> None:
        self._store_file.parent.mkdir(parents=True, exist_ok=True)
        self._store_file.write_text(
            json.dumps(self._pools, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # -- public API ----------------------------------------------------

    def list_pools(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._pools]

    def get_pool(self, pool_id: str) -> dict | None:
        with self._lock:
            for p in self._pools:
                if p["id"] == pool_id:
                    return dict(p)
        return None

    def add_pool(self, name: str, base_url: str, secret_key: str, enabled: bool = True) -> dict:
        pool = _normalize_pool({
            "id": _new_pool_id(),
            "name": name,
            "base_url": base_url,
            "secret_key": secret_key,
            "enabled": enabled,
        })
        with self._lock:
            self._pools.append(pool)
            self._save()
        return dict(pool)

    def update_pool(self, pool_id: str, updates: dict) -> dict | None:
        with self._lock:
            for i, p in enumerate(self._pools):
                if p["id"] == pool_id:
                    merged = {**p, **{k: v for k, v in updates.items() if v is not None}, "id": pool_id}
                    self._pools[i] = _normalize_pool(merged)
                    self._save()
                    return dict(self._pools[i])
        return None

    def delete_pool(self, pool_id: str) -> bool:
        with self._lock:
            before = len(self._pools)
            self._pools = [p for p in self._pools if p["id"] != pool_id]
            if len(self._pools) < before:
                self._save()
                return True
        return False

    def usable_pools(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._pools if _is_pool_usable(p)]

    @property
    def has_usable(self) -> bool:
        with self._lock:
            return any(_is_pool_usable(p) for p in self._pools)


# ── CPA fetcher (per-pool) ──────────────────────────────────────────

def _management_headers(secret_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {secret_key}",
        "Accept": "application/json",
    }


def _extract_access_token(auth_file: dict[str, Any]) -> str | None:
    for key in ("access_token", "token", "accessToken", "access-token"):
        value = str(auth_file.get(key) or "").strip()
        if value:
            return value
    for wrapper_key in ("data", "content", "credential", "auth", "credentials"):
        nested = auth_file.get(wrapper_key)
        if isinstance(nested, dict):
            for key in ("access_token", "token", "accessToken", "access-token"):
                value = str(nested.get(key) or "").strip()
                if value:
                    return value
    for value in auth_file.values():
        if isinstance(value, str) and value.strip().startswith("eyJ") and len(value.strip()) > 100:
            return value.strip()
    return None


def _resolve_file_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("files", "auth_files", "auth-files", "data", "items"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
        return [payload]
    return []


def _fetch_file_detail(session: Session, base_url: str, secret_key: str, file_name: str) -> dict | None:
    url = f"{base_url.rstrip('/')}/v0/management/auth-files/download"
    try:
        response = session.get(url, headers=_management_headers(secret_key), params={"name": file_name}, timeout=15)
        if not response.ok:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def fetch_tokens_for_pool(pool: dict) -> list[str]:
    """Fetch all access_tokens from a single CPA pool."""
    base_url = pool.get("base_url") or ""
    secret_key = pool.get("secret_key") or ""
    if not base_url or not secret_key:
        return []

    pool_name = pool.get("name") or pool.get("id") or "?"
    url = f"{base_url.rstrip('/')}/v0/management/auth-files"
    print(f"[cpa-service] [{pool_name}] fetching from {url}")

    session = Session(verify=config.tls_verify)
    try:
        response = session.get(url, headers=_management_headers(secret_key), timeout=30)
        if not response.ok:
            print(f"[cpa-service] [{pool_name}] HTTP {response.status_code}")
            return []
        payload = response.json()
    except Exception as exc:
        print(f"[cpa-service] [{pool_name}] error: {exc}")
        return []

    file_entries = _resolve_file_list(payload)
    active_entries = [
        e for e in file_entries
        if not e.get("disabled") and not e.get("unavailable")
        and e.get("status") in (None, "", "active")
        and e.get("type") in (None, "", "codex")
    ]
    print(f"[cpa-service] [{pool_name}] {len(active_entries)} active codex entries")

    tokens: list[str] = []
    seen: set[str] = set()
    need_download: list[dict] = []

    for entry in active_entries:
        token = _extract_access_token(entry)
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
        else:
            need_download.append(entry)

    if need_download:
        max_workers = min(50, len(need_download))
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {}
                for entry in need_download:
                    file_name = str(entry.get("name") or entry.get("id") or "").strip()
                    if not file_name:
                        continue
                    future = executor.submit(_fetch_file_detail, session, base_url, secret_key, file_name)
                    future_map[future] = file_name

                for future in as_completed(future_map):
                    detail = future.result()
                    if detail is None:
                        continue
                    content = detail.get("content")
                    if isinstance(content, str):
                        try:
                            parsed = json.loads(content)
                            if isinstance(parsed, dict):
                                detail = {**detail, **parsed}
                        except Exception:
                            pass
                    token = _extract_access_token(detail)
                    if token and token not in seen:
                        seen.add(token)
                        tokens.append(token)
        except Exception as exc:
            print(f"[cpa-service] [{pool_name}] concurrent fetch error: {exc}")

    session.close()
    print(f"[cpa-service] [{pool_name}] extracted {len(tokens)} token(s)")
    return tokens


def fetch_pool_status(pool: dict) -> dict:
    """Test a single pool and return status info."""
    tokens = fetch_tokens_for_pool(pool)
    return {"pool_id": pool["id"], "tokens": len(tokens)}


# ── Aggregated CPA service ──────────────────────────────────────────

class CPAService:
    """Aggregates tokens from all usable CPA pools with caching."""

    def __init__(self, cpa_config: CPAConfig):
        self._config = cpa_config
        self._lock = Lock()
        self._tokens: list[str] = []
        self._index = 0
        self._last_refresh: float = 0
        self._cache_ttl = 1800  # 30 minutes

    @property
    def enabled(self) -> bool:
        return self._config.has_usable

    def fetch_all_tokens(self) -> list[str]:
        """Fetch tokens from all usable pools concurrently."""
        pools = self._config.usable_pools()
        if not pools:
            return []

        all_tokens: list[str] = []
        seen: set[str] = set()

        max_workers = min(5, len(pools))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(fetch_tokens_for_pool, pool): pool for pool in pools}
            for future in as_completed(future_map):
                try:
                    tokens = future.result()
                    for token in tokens:
                        if token not in seen:
                            seen.add(token)
                            all_tokens.append(token)
                except Exception as exc:
                    pool = future_map[future]
                    print(f"[cpa-service] pool {pool.get('name', pool.get('id'))} error: {exc}")

        print(f"[cpa-service] total {len(all_tokens)} token(s) from {len(pools)} pool(s)")
        return all_tokens

    def get_token(self, excluded_tokens: set[str] | None = None) -> str | None:
        with self._lock:
            now = time.time()
            if not self._tokens or (now - self._last_refresh) > self._cache_ttl:
                self._tokens = self.fetch_all_tokens()
                self._last_refresh = now
                if self._tokens:
                    self._index = 0

            excluded = {t for t in (excluded_tokens or set()) if t}
            available = [t for t in self._tokens if t not in excluded]
            if not available:
                return None

            token = available[self._index % len(available)]
            self._index += 1
            return token

    def invalidate_cache(self) -> None:
        with self._lock:
            self._last_refresh = 0


# ── Singletons ──────────────────────────────────────────────────────

cpa_config = CPAConfig(CPA_CONFIG_FILE)
cpa_service = CPAService(cpa_config)

if cpa_config.has_usable:
    pools = cpa_config.usable_pools()
    print(f"[cpa-service] {len(pools)} usable pool(s) configured")
