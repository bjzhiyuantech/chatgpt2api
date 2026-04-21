"""Proxy configuration for outbound requests to ChatGPT."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from services.config import DATA_DIR


PROXY_CONFIG_FILE = DATA_DIR / "proxy_config.json"


class ProxyConfig:
    """Persisted proxy configuration that can be updated at runtime."""

    def __init__(self, store_file: Path):
        self._store_file = store_file
        self._lock = Lock()
        self._data = self._load()

    def _load(self) -> dict[str, str]:
        if not self._store_file.exists():
            return {"proxy_url": ""}
        try:
            raw = json.loads(self._store_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {
                    "proxy_url": str(raw.get("proxy_url") or "").strip(),
                }
        except Exception:
            pass
        return {"proxy_url": ""}

    def _save(self) -> None:
        self._store_file.parent.mkdir(parents=True, exist_ok=True)
        self._store_file.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def get(self) -> dict[str, str]:
        with self._lock:
            return dict(self._data)

    def update(self, proxy_url: str | None = None) -> dict[str, str]:
        with self._lock:
            if proxy_url is not None:
                self._data["proxy_url"] = proxy_url.strip()
            self._save()
            return dict(self._data)

    @property
    def proxy_url(self) -> str:
        """Return the proxy URL or empty string if not configured."""
        with self._lock:
            return self._data.get("proxy_url") or ""

    @property
    def proxy_dict(self) -> dict[str, str] | None:
        """Return proxy dict for curl_cffi Session, or None if not configured.

        Supports: socks5://host:port, http://host:port, https://host:port
        Also supports auth: socks5://user:pass@host:port
        """
        url = self.proxy_url
        if not url:
            return None
        return {"http": url, "https": url}


proxy_config = ProxyConfig(PROXY_CONFIG_FILE)

if proxy_config.proxy_url:
    print(f"[proxy] configured: {proxy_config.proxy_url}")
