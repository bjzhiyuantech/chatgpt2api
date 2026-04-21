from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event, Thread

from fastapi import APIRouter, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from services.account_service import account_service
from services.config import config
from services.backend_service import BackendService
from services.cpa_service import cpa_service, cpa_config, fetch_tokens_for_pool, fetch_pool_status
from services.proxy_service import proxy_config
from services.image_service import ImageGenerationError
from services.task_service import task_service
from services.version import get_app_version


BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gpt-4o"
    n: int = Field(default=1, ge=1, le=4)
    response_format: str = "b64_json"
    history_disabled: bool = True


class AccountCreateRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list)


class AccountDeleteRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list)


class AccountRefreshRequest(BaseModel):
    access_tokens: list[str] = Field(default_factory=list)


class AccountUpdateRequest(BaseModel):
    access_token: str = Field(default="")
    type: str | None = None
    status: str | None = None
    quota: int | None = None


class CPAConfigUpdateRequest(BaseModel):
    base_url: str | None = None
    secret_key: str | None = None


class CPAPoolCreateRequest(BaseModel):
    name: str = ""
    base_url: str = ""
    secret_key: str = ""
    enabled: bool = True


class CPAPoolUpdateRequest(BaseModel):
    name: str | None = None
    base_url: str | None = None
    secret_key: str | None = None
    enabled: bool | None = None


class ProxyConfigUpdateRequest(BaseModel):
    proxy_url: str | None = None


def build_model_item(model_id: str) -> dict[str, object]:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "chatgpt2api",
    }


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def require_auth_key(authorization: str | None) -> None:
    if extract_bearer_token(authorization) != str(config.auth_key or "").strip():
        raise HTTPException(status_code=401, detail={"error": "authorization is invalid"})


def start_limited_account_watcher(stop_event: Event) -> Thread:
    def worker() -> None:
        while not stop_event.is_set():
            try:
                limited_tokens = account_service.list_limited_tokens()
                if limited_tokens:
                    print(f"[account-limited-watcher] checking {len(limited_tokens)} limited accounts")
                    account_service.refresh_accounts(limited_tokens)
            except Exception as exc:
                print(f"[account-limited-watcher] fail {exc}")
            stop_event.wait(300)

    thread = Thread(target=worker, name="limited-account-watcher", daemon=True)
    thread.start()
    return thread


class SPAStaticFiles(StaticFiles):
    """StaticFiles subclass that falls back to index.html for SPA routing."""

    async def get_response(self, path: str, scope) -> FileResponse:
        try:
            return await super().get_response(path, scope)
        except Exception:
            # For paths with file extensions (JS/CSS/etc), don't fallback — re-raise 404
            last_segment = path.strip("/").split("/")[-1] if path.strip("/") else ""
            if "." in last_segment and not last_segment.endswith(".html") and not last_segment.endswith(".txt"):
                raise
            # SPA fallback to index.html
            return await super().get_response("index.html", scope)


def create_app() -> FastAPI:
    service = BackendService(account_service)
    app_version = get_app_version()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stop_event = Event()
        thread = start_limited_account_watcher(stop_event)
        try:
            yield
        finally:
            stop_event.set()
            thread.join(timeout=1)

    app = FastAPI(title="chatgpt2api", version=app_version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    router = APIRouter()

    @router.get("/v1/models")
    async def list_models():
        return {
            "object": "list",
            "data": [
                build_model_item("gpt-image-1"),
                build_model_item("gpt-image-2"),
            ],
        }

    @router.post("/auth/login")
    async def login(authorization: str | None = Header(default=None)):
        require_auth_key(authorization)
        return {"ok": True, "version": app_version}

    @router.get("/version")
    async def get_version():
        return {"version": app_version}

    @router.get("/api/accounts")
    async def get_accounts(authorization: str | None = Header(default=None)):
        require_auth_key(authorization)
        return {"items": account_service.list_accounts()}

    @router.post("/api/accounts")
    async def create_accounts(
            body: AccountCreateRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        tokens = [str(token or "").strip() for token in body.tokens if str(token or "").strip()]
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "tokens is required"})
        result = account_service.add_accounts(tokens)
        refresh_result = account_service.refresh_accounts(tokens)
        return {
            **result,
            "refreshed": refresh_result.get("refreshed", 0),
            "errors": refresh_result.get("errors", []),
            "items": refresh_result.get("items", result.get("items", [])),
        }

    @router.delete("/api/accounts")
    async def delete_accounts(
            body: AccountDeleteRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        tokens = [str(token or "").strip() for token in body.tokens if str(token or "").strip()]
        if not tokens:
            raise HTTPException(status_code=400, detail={"error": "tokens is required"})
        return account_service.delete_accounts(tokens)

    @router.post("/api/accounts/refresh")
    async def refresh_accounts(
            body: AccountRefreshRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        access_tokens = [str(token or "").strip() for token in body.access_tokens if str(token or "").strip()]
        if not access_tokens:
            access_tokens = account_service.list_tokens()
        if not access_tokens:
            raise HTTPException(status_code=400, detail={"error": "access_tokens is required"})
        return account_service.refresh_accounts(access_tokens)

    @router.post("/api/accounts/update")
    async def update_account(
            body: AccountUpdateRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        access_token = str(body.access_token or "").strip()
        if not access_token:
            raise HTTPException(status_code=400, detail={"error": "access_token is required"})

        updates = {
            key: value
            for key, value in {
                "type": body.type,
                "status": body.status,
                "quota": body.quota,
            }.items()
            if value is not None
        }
        if not updates:
            raise HTTPException(status_code=400, detail={"error": "no updates provided"})

        account = account_service.update_account(access_token, updates)
        if account is None:
            raise HTTPException(status_code=404, detail={"error": "account not found"})
        return {"item": account, "items": account_service.list_accounts()}

    @router.post("/v1/images/generations")
    async def generate_images(
            body: ImageGenerationRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        try:
            return await run_in_threadpool(
                service.generate_with_pool,
                body.prompt,
                body.model,
                body.n,
            )
        except ImageGenerationError as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @router.post("/v1/images/edits")
    async def edit_images(
            image: UploadFile = File(...),
            prompt: str = Form(...),
            model: str = Form(default="gpt-image-1"),
            n: int = Form(default=1),
            response_format: str = Form(default="b64_json"),
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        if not prompt.strip():
            raise HTTPException(status_code=400, detail={"error": "prompt is required"})
        if n < 1 or n > 4:
            raise HTTPException(status_code=400, detail={"error": "n must be between 1 and 4"})

        image_data = await image.read()
        if not image_data:
            raise HTTPException(status_code=400, detail={"error": "image file is empty"})
        if len(image_data) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail={"error": "image file too large (max 20MB)"})

        try:
            return await run_in_threadpool(
                service.generate_with_pool,
                prompt.strip(),
                model,
                n,
                image_data,
            )
        except ImageGenerationError as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    # ── Task query endpoints ────────────────────────────────────────

    @router.get("/v1/images/tasks/{task_id}")
    async def get_image_task(
            task_id: str,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        task = task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"error": "task not found"})
        response: dict = {
            "task_id": task.id,
            "status": task.status,
            "prompt": task.prompt,
            "model": task.model,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
        }
        if task.status == "completed" and task.result:
            response["result"] = task.result
        if task.status == "failed" and task.error:
            response["error"] = task.error
        return response

    @router.get("/v1/images/tasks")
    async def list_image_tasks(
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        tasks = task_service.list_tasks()
        return {
            "tasks": [
                {
                    "task_id": t.id,
                    "status": t.status,
                    "prompt": t.prompt,
                    "model": t.model,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                    "error": t.error if t.status == "failed" else None,
                }
                for t in tasks
            ]
        }

    # ── CPA multi-pool endpoints ────────────────────────────────────

    @router.get("/api/cpa/pools")
    async def list_cpa_pools(authorization: str | None = Header(default=None)):
        require_auth_key(authorization)
        return {"pools": cpa_config.list_pools()}

    @router.post("/api/cpa/pools")
    async def create_cpa_pool(
            body: CPAPoolCreateRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        if not body.base_url.strip():
            raise HTTPException(status_code=400, detail={"error": "base_url is required"})
        if not body.secret_key.strip():
            raise HTTPException(status_code=400, detail={"error": "secret_key is required"})
        pool = cpa_config.add_pool(
            name=body.name,
            base_url=body.base_url,
            secret_key=body.secret_key,
            enabled=body.enabled,
        )
        cpa_service.invalidate_cache()
        return {"pool": pool, "pools": cpa_config.list_pools()}

    @router.post("/api/cpa/pools/{pool_id}")
    async def update_cpa_pool(
            pool_id: str,
            body: CPAPoolUpdateRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        pool = cpa_config.update_pool(pool_id, body.model_dump(exclude_none=True))
        if pool is None:
            raise HTTPException(status_code=404, detail={"error": "pool not found"})
        cpa_service.invalidate_cache()
        return {"pool": pool, "pools": cpa_config.list_pools()}

    @router.delete("/api/cpa/pools/{pool_id}")
    async def delete_cpa_pool(
            pool_id: str,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        if not cpa_config.delete_pool(pool_id):
            raise HTTPException(status_code=404, detail={"error": "pool not found"})
        cpa_service.invalidate_cache()
        return {"pools": cpa_config.list_pools()}

    @router.get("/api/cpa/pools/{pool_id}/status")
    async def cpa_pool_status(
            pool_id: str,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        pool = cpa_config.get_pool(pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail={"error": "pool not found"})
        result = await run_in_threadpool(fetch_pool_status, pool)
        return result

    @router.post("/api/cpa/pools/{pool_id}/sync")
    async def cpa_pool_sync(
            pool_id: str,
            authorization: str | None = Header(default=None),
    ):
        """Pull tokens from a specific CPA pool and import into local account pool."""
        require_auth_key(authorization)
        pool = cpa_config.get_pool(pool_id)
        if pool is None:
            raise HTTPException(status_code=404, detail={"error": "pool not found"})
        tokens = await run_in_threadpool(fetch_tokens_for_pool, pool)
        if not tokens:
            raise HTTPException(status_code=502, detail={"error": "No tokens returned from CPA"})
        result = account_service.add_accounts(tokens)
        refresh_result = account_service.refresh_accounts(tokens)
        return {
            **result,
            "refreshed": refresh_result.get("refreshed", 0),
            "errors": refresh_result.get("errors", []),
            "items": refresh_result.get("items", result.get("items", [])),
        }

    @router.get("/api/cpa/status")
    async def cpa_global_status(authorization: str | None = Header(default=None)):
        require_auth_key(authorization)
        if not cpa_config.has_usable:
            return {"enabled": False, "pools": 0, "tokens": 0}
        tokens = await run_in_threadpool(cpa_service.fetch_all_tokens)
        return {"enabled": True, "pools": len(cpa_config.usable_pools()), "tokens": len(tokens)}

    # ── Proxy config endpoints ──────────────────────────────────────

    @router.get("/api/proxy/config")
    async def get_proxy_config(authorization: str | None = Header(default=None)):
        require_auth_key(authorization)
        cfg = proxy_config.get()
        return {
            "proxy_url": cfg.get("proxy_url") or "",
            "enabled": bool(cfg.get("proxy_url")),
        }

    @router.post("/api/proxy/config")
    async def update_proxy_config(
            body: ProxyConfigUpdateRequest,
            authorization: str | None = Header(default=None),
    ):
        require_auth_key(authorization)
        cfg = proxy_config.update(proxy_url=body.proxy_url)
        return {
            "proxy_url": cfg.get("proxy_url") or "",
            "enabled": bool(cfg.get("proxy_url")),
        }

    app.include_router(router)

    # Mount SPA static files — catches all non-API paths
    if WEB_DIST_DIR.exists():
        app.mount("/", SPAStaticFiles(directory=str(WEB_DIST_DIR), html=True), name="spa")

    return app
