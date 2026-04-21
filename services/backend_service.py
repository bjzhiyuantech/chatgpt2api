from __future__ import annotations

from fastapi import HTTPException

from services.account_service import AccountService
from services.cpa_service import cpa_service
from services.image_service import ImageGenerationError, generate_image_result, is_token_invalid_error


class BackendService:
    def __init__(self, account_service: AccountService):
        self.account_service = account_service

    @staticmethod
    def _is_account_ready_for_image(account: dict | None) -> bool:
        if not isinstance(account, dict):
            return False
        if account.get("status") in {"禁用", "异常"}:
            return False
        return int(account.get("quota") or 0) > 0

    def _refresh_request_token(self, access_token: str) -> dict | None:
        try:
            remote_info = self.account_service.fetch_remote_info(access_token)
        except Exception as exc:
            message = str(exc)
            print(f"[image-generate] refresh token={access_token[:12]}... fail {message}")
            if "/backend-api/me failed: HTTP 401" in message:
                return self.account_service.update_account(
                    access_token,
                    {
                        "status": "异常",
                        "quota": 0,
                    },
                )
            return None
        return self.account_service.update_account(access_token, remote_info)

    def resolve_request_token(self, excluded_tokens: set[str] | None = None) -> str:
        try:
            return self.account_service.next_token(excluded_tokens=excluded_tokens)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail={"error": str(exc)}) from exc

    def generate_with_pool(self, prompt: str, model: str, n: int, image_data: bytes | None = None):
        if cpa_service.enabled:
            return self._generate_with_cpa(prompt, model, n, image_data)
        return self._generate_with_local_pool(prompt, model, n, image_data)

    def _generate_with_cpa(self, prompt: str, model: str, n: int, image_data: bytes | None = None):
        """Fetch token from CLIProxyAPI on-the-fly and generate images."""
        attempted_tokens: set[str] = set()
        max_attempts = 5

        for attempt in range(max_attempts):
            request_token = cpa_service.get_token(excluded_tokens=attempted_tokens)
            if not request_token:
                if attempt == 0:
                    raise HTTPException(
                        status_code=503,
                        detail={"error": "No access_token available from CPA"},
                    )
                break
            attempted_tokens.add(request_token)
            print(f"[image-generate] cpa token={request_token[:12]}... model={model} n={n}")

            try:
                result = generate_image_result(request_token, prompt, model, n, image_data=image_data)
                print(f"[image-generate] cpa success token={request_token[:12]}...")
                return result
            except ImageGenerationError as exc:
                print(f"[image-generate] cpa fail token={request_token[:12]}... error={exc}")
                if is_token_invalid_error(str(exc)):
                    cpa_service.invalidate_cache()
                    continue
                raise

        raise HTTPException(
            status_code=503,
            detail={"error": "All CPA tokens exhausted or failed"},
        )

    def _generate_with_local_pool(self, prompt: str, model: str, n: int, image_data: bytes | None = None):
        """Original local account pool logic."""
        attempted_tokens: set[str] = set()

        while True:
            try:
                request_token = self.resolve_request_token(excluded_tokens=attempted_tokens)
            except HTTPException:
                raise

            attempted_tokens.add(request_token)
            refreshed_account = self._refresh_request_token(request_token)
            if not self._is_account_ready_for_image(refreshed_account):
                print(
                    f"[image-generate] skip token={request_token[:12]}... "
                    f"quota={refreshed_account.get('quota') if refreshed_account else 'unknown'} "
                    f"status={refreshed_account.get('status') if refreshed_account else 'unknown'}"
                )
                continue

            print(f"[image-generate] start pooled token={request_token[:12]}... model={model} n={n}")
            try:
                result = generate_image_result(request_token, prompt, model, n, image_data=image_data)
                account = self.account_service.mark_image_result(request_token, success=True)
                print(
                    f"[image-generate] success pooled token={request_token[:12]}... "
                    f"quota={account.get('quota') if account else 'unknown'} status={account.get('status') if account else 'unknown'}"
                )
                return result
            except ImageGenerationError as exc:
                account = self.account_service.mark_image_result(request_token, success=False)
                print(
                    f"[image-generate] fail pooled token={request_token[:12]}... "
                    f"error={exc} quota={account.get('quota') if account else 'unknown'} status={account.get('status') if account else 'unknown'}"
                )
                if is_token_invalid_error(str(exc)):
                    self.account_service.remove_token(request_token)
                    print(f"[image-generate] remove invalid token={request_token[:12]}...")
                    continue
                raise
