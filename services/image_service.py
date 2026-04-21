from __future__ import annotations

import base64
import hashlib
import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from curl_cffi.requests import Session

from services.account_service import account_service
from services.config import config
from services import proof_of_work


BASE_URL = "https://chatgpt.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_MODEL = "gpt-4o"
MAX_POW_ATTEMPTS = 500000

_CORES = [16, 24, 32]
_SCREENS = [3000, 4000, 6000]
_NAV_KEYS = [
    "webdriver−false",
    "vendor−Google Inc.",
    "cookieEnabled−true",
    "pdfViewerEnabled−true",
    "hardwareConcurrency−32",
    "language−zh-CN",
    "mimeTypes−[object MimeTypeArray]",
    "userAgentData−[object NavigatorUAData]",
]
_WIN_KEYS = [
    "innerWidth",
    "innerHeight",
    "devicePixelRatio",
    "screen",
    "chrome",
    "location",
    "history",
    "navigator",
]


class ImageGenerationError(Exception):
    pass


@dataclass
class GeneratedImage:
    b64_json: str
    revised_prompt: str
    url: str = ""


def _build_fp(access_token: str) -> dict:
    account = account_service.get_account(access_token) or {}
    fp = {}
    raw_fp = account.get("fp")
    if isinstance(raw_fp, dict):
        fp.update({str(k).lower(): v for k, v in raw_fp.items()})
    for key in (
        "user-agent",
        "impersonate",
        "oai-device-id",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
    ):
        if key in account:
            fp[key] = account[key]
    if "user-agent" not in fp:
        fp["user-agent"] = USER_AGENT
    if "impersonate" not in fp:
        fp["impersonate"] = "edge101"
    if "oai-device-id" not in fp:
        fp["oai-device-id"] = str(uuid.uuid4())
    return fp


def _new_session(access_token: str) -> tuple[Session, dict]:
    fp = _build_fp(access_token)
    session = Session(
        impersonate=fp.get("impersonate") or "edge101",
        verify=config.tls_verify,
    )
    session.headers.update(
        {
            "user-agent": fp.get("user-agent") or USER_AGENT,
            "accept-language": "en-US,en;q=0.9",
            "origin": BASE_URL,
            "referer": BASE_URL + "/",
            "accept": "*/*",
            "sec-ch-ua": fp.get("sec-ch-ua") or '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            "sec-ch-ua-mobile": fp.get("sec-ch-ua-mobile") or "?0",
            "sec-ch-ua-platform": fp.get("sec-ch-ua-platform") or '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "oai-device-id": fp.get("oai-device-id"),
        }
    )
    return session, fp


def _retry(fn, retries: int = 4, delay: float = 2.0, retry_on_status: tuple[int, ...] = ()) -> object:
    last_error = None
    last_response = None
    for attempt in range(retries):
        try:
            response = fn()
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
            continue
        if retry_on_status and getattr(response, "status_code", 0) in retry_on_status:
            last_response = response
            time.sleep(delay * (attempt + 1))
            continue
        return response
    if last_response is not None:
        return last_response
    if last_error is not None:
        raise last_error
    raise ImageGenerationError("request failed")


def _pow_config(user_agent: str) -> list:
    return proof_of_work.get_config(user_agent)


def _generate_requirements_answer(seed: str, difficulty: str, config: list) -> tuple[str, bool]:
    diff_len = len(difficulty)
    seed_bytes = seed.encode()
    prefix1 = (json.dumps(config[:3], separators=(",", ":"), ensure_ascii=False)[:-1] + ",").encode()
    prefix2 = ("," + json.dumps(config[4:9], separators=(",", ":"), ensure_ascii=False)[1:-1] + ",").encode()
    prefix3 = ("," + json.dumps(config[10:], separators=(",", ":"), ensure_ascii=False)[1:]).encode()
    target = bytes.fromhex(difficulty)
    for attempt in range(MAX_POW_ATTEMPTS):
        left = str(attempt).encode()
        right = str(attempt >> 1).encode()
        encoded = base64.b64encode(prefix1 + left + prefix2 + right + prefix3)
        digest = hashlib.sha3_512(seed_bytes + encoded).digest()
        if digest[:diff_len] <= target:
            return encoded.decode(), True
    fallback = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D" + base64.b64encode(f'"{seed}"'.encode()).decode()
    return fallback, False


def _get_requirements_token(config: list) -> str:
    seed = format(random.random())
    answer, _ = _generate_requirements_answer(seed, "0fffff", config)
    return "gAAAAAC" + answer


def _generate_proof_token(seed: str, difficulty: str, user_agent: str, proof_config: Optional[list] = None) -> str:
    answer, _ = proof_of_work.get_answer_token(seed, difficulty, proof_config or _pow_config(user_agent))
    return answer


def _bootstrap(session: Session, fp: dict) -> str:
    response = _retry(lambda: session.get(BASE_URL + "/", timeout=30))
    try:
        proof_of_work.get_data_build_from_html(response.text)
    except Exception:
        pass
    device_id = response.cookies.get("oai-did")
    if device_id:
        return device_id
    for cookie in session.cookies.jar if hasattr(session.cookies, "jar") else []:
        name = getattr(cookie, "name", getattr(cookie, "key", ""))
        if name == "oai-did":
            return cookie.value
    return str(fp.get("oai-device-id") or uuid.uuid4())


def _chat_requirements(session: Session, access_token: str, device_id: str) -> tuple[str, Optional[dict]]:
    config = _pow_config(USER_AGENT)
    response = _retry(
        lambda: session.post(
            BASE_URL + "/backend-api/sentinel/chat-requirements",
            headers={
                "Authorization": f"Bearer {access_token}",
                "oai-device-id": device_id,
                "content-type": "application/json",
            },
            json={"p": _get_requirements_token(config)},
            timeout=30,
        ),
        retries=4,
    )
    if not response.ok:
        raise ImageGenerationError(response.text[:400] or f"chat-requirements failed: {response.status_code}")
    payload = response.json()
    return payload["token"], payload.get("proofofwork") or {}


def is_token_invalid_error(message: str) -> bool:
    text = str(message or "").lower()
    return (
        "token_invalidated" in text
        or "token_revoked" in text
        or "authentication token has been invalidated" in text
        or "invalidated oauth token" in text
    )


def _send_conversation(
    session: Session,
    access_token: str,
    device_id: str,
    chat_token: str,
    proof_token: Optional[str],
    parent_message_id: str,
    prompt: str,
    model: str,
    image_file_ids: Optional[list[str]] = None,
):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "accept": "text/event-stream",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "content-type": "application/json",
        "oai-device-id": device_id,
        "oai-language": "zh-CN",
        "oai-client-build-number": "5955942",
        "oai-client-version": "prod-be885abbfcfe7b1f511e88b3003d9ee44757fbad",
        "origin": BASE_URL,
        "referer": BASE_URL + "/",
        "openai-sentinel-chat-requirements-token": chat_token,
    }
    if proof_token:
        headers["openai-sentinel-proof-token"] = proof_token

    # Build message content — text only or multipart (text + images)
    if image_file_ids:
        parts: list = [prompt]
        for file_id in image_file_ids:
            parts.append({
                "asset_pointer": f"file-service://{file_id}",
                "size_bytes": 0,
                "width": 0,
                "height": 0,
            })
        content = {"content_type": "multipart_image", "parts": parts}
        attachments = [
            {
                "id": file_id,
                "name": f"image_{i}.png",
                "size": 0,
                "mimeType": "image/png",
            }
            for i, file_id in enumerate(image_file_ids)
        ]
    else:
        content = {"content_type": "text", "parts": [prompt]}
        attachments = []

    response = _retry(
        lambda: session.post(
            BASE_URL + "/backend-api/conversation",
            headers=headers,
            json={
                "action": "next",
                "messages": [
                    {
                        "id": str(uuid.uuid4()),
                        "author": {"role": "user"},
                        "content": content,
                        "metadata": {
                            "attachments": attachments,
                        },
                    }
                ],
                "parent_message_id": parent_message_id,
                "model": model,
                "history_and_training_disabled": False,
                "timezone_offset_min": -480,
                "timezone": "America/Los_Angeles",
                "conversation_mode": {"kind": "primary_assistant"},
                "conversation_origin": None,
                "force_paragen": False,
                "force_paragen_model_slug": "",
                "force_rate_limit": False,
                "force_use_sse": True,
                "paragen_cot_summary_display_override": "allow",
                "paragen_stream_type_override": None,
                "reset_rate_limits": False,
                "suggestions": [],
                "supported_encodings": [],
                "system_hints": ["picture_v2"],
                "variant_purpose": "comparison_implicit",
                "websocket_request_id": str(uuid.uuid4()),
                "client_contextual_info": {
                    "is_dark_mode": False,
                    "time_since_loaded": random.randint(50, 500),
                    "page_height": random.randint(500, 1000),
                    "page_width": random.randint(1000, 2000),
                    "pixel_ratio": 1.2,
                    "screen_height": random.randint(800, 1200),
                    "screen_width": random.randint(1200, 2200),
                },
            },
            stream=True,
            timeout=180,
        ),
        retries=3,
    )
    if not response.ok:
        raise ImageGenerationError(response.text[:400] or f"conversation failed: {response.status_code}")
    return response


def _parse_sse(response) -> dict:
    file_ids: list[str] = []
    conversation_id = ""
    text_parts: list[str] = []
    for raw_line in response.iter_lines():
        if not raw_line:
            continue
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8", errors="replace")
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload in ("", "[DONE]"):
            break
        for prefix, stored_prefix in (("file-service://", ""), ("sediment://", "sed:")):
            start = 0
            while True:
                index = payload.find(prefix, start)
                if index < 0:
                    break
                start = index + len(prefix)
                tail = payload[start:]
                file_id = []
                for char in tail:
                    if char.isalnum() or char in "_-":
                        file_id.append(char)
                    else:
                        break
                if file_id:
                    value = stored_prefix + "".join(file_id)
                    if value not in file_ids:
                        file_ids.append(value)
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        conversation_id = str(obj.get("conversation_id") or conversation_id)
        if obj.get("type") in {"resume_conversation_token", "message_marker", "message_stream_complete"}:
            conversation_id = str(obj.get("conversation_id") or conversation_id)
        data = obj.get("v")
        if isinstance(data, dict):
            conversation_id = str(data.get("conversation_id") or conversation_id)
        message = obj.get("message") or {}
        content = message.get("content") or {}
        if content.get("content_type") == "text":
            parts = content.get("parts") or []
            if parts:
                text_parts.append(str(parts[0]))
    return {"conversation_id": conversation_id, "file_ids": file_ids, "text": "".join(text_parts)}


def _extract_image_ids(mapping: dict) -> list[str]:
    file_ids: list[str] = []
    for node in mapping.values():
        message = (node or {}).get("message") or {}
        author = message.get("author") or {}
        metadata = message.get("metadata") or {}
        content = message.get("content") or {}
        if author.get("role") != "tool":
            continue
        if metadata.get("async_task_type") != "image_gen":
            continue
        if content.get("content_type") != "multimodal_text":
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict):
                pointer = str(part.get("asset_pointer") or "")
                if pointer.startswith("file-service://"):
                    file_id = pointer.removeprefix("file-service://")
                    if file_id not in file_ids:
                        file_ids.append(file_id)
                elif pointer.startswith("sediment://"):
                    file_id = "sed:" + pointer.removeprefix("sediment://")
                    if file_id not in file_ids:
                        file_ids.append(file_id)
    return file_ids


def _poll_image_ids(session: Session, access_token: str, device_id: str, conversation_id: str) -> list[str]:
    started = time.time()
    while time.time() - started < 180:
        response = _retry(
            lambda: session.get(
                f"{BASE_URL}/backend-api/conversation/{conversation_id}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "oai-device-id": device_id,
                    "accept": "*/*",
                },
                timeout=30,
            ),
            retries=2,
            retry_on_status=(429, 502, 503, 504),
        )
        if response.status_code != 200:
            time.sleep(3)
            continue
        try:
            payload = response.json()
        except Exception:
            time.sleep(3)
            continue
        file_ids = _extract_image_ids(payload.get("mapping") or {})
        if file_ids:
            return file_ids
        time.sleep(3)
    return []


def _fetch_download_url(session: Session, access_token: str, device_id: str, conversation_id: str, file_id: str) -> str:
    is_sediment = file_id.startswith("sed:")
    raw_id = file_id[4:] if is_sediment else file_id
    if is_sediment:
        endpoint = f"{BASE_URL}/backend-api/conversation/{conversation_id}/attachment/{raw_id}/download"
    else:
        endpoint = f"{BASE_URL}/backend-api/files/{raw_id}/download"
    response = session.get(
        endpoint,
        headers={
            "Authorization": f"Bearer {access_token}",
            "oai-device-id": device_id,
        },
        timeout=30,
    )
    if not response.ok:
        return ""
    return str((response.json() or {}).get("download_url") or "")


def _download_as_base64(session: Session, download_url: str) -> str:
    response = session.get(download_url, timeout=60)
    if not response.ok or not response.content:
        raise ImageGenerationError("download image failed")
    return base64.b64encode(response.content).decode("ascii")


def _resolve_upstream_model(access_token: str, requested_model: str) -> str:
    requested_model = str(requested_model or "").strip() or "gpt-image-1"
    account = account_service.get_account(access_token) or {}
    is_free_account = str(account.get("type") or "Free").strip() == "Free"

    if requested_model == "gpt-image-1":
        return "auto"
    if requested_model == "gpt-image-2":
        return "auto" if is_free_account else "gpt-5-3"
    return str(requested_model or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _upload_image(session: Session, access_token: str, device_id: str, image_data: bytes, filename: str = "image.png", mime_type: str = "image/png") -> str:
    """Upload an image to ChatGPT backend-api and return the file_id."""
    # Step 1: Create upload
    response = _retry(
        lambda: session.post(
            BASE_URL + "/backend-api/files",
            headers={
                "Authorization": f"Bearer {access_token}",
                "oai-device-id": device_id,
                "content-type": "application/json",
            },
            json={
                "file_name": filename,
                "file_size": len(image_data),
                "use_case": "multimodal",
            },
            timeout=30,
        ),
        retries=3,
    )
    if not response.ok:
        raise ImageGenerationError(f"file create failed: HTTP {response.status_code} {response.text[:200]}")

    file_info = response.json()
    file_id = file_info.get("file_id") or ""
    upload_url = file_info.get("upload_url") or ""
    if not file_id or not upload_url:
        raise ImageGenerationError("file create returned no file_id or upload_url")

    # Step 2: Upload the actual file content
    response = _retry(
        lambda: session.put(
            upload_url,
            headers={
                "Content-Type": mime_type,
                "x-ms-blob-type": "BlockBlob",
                "x-ms-version": "2020-04-08",
            },
            data=image_data,
            timeout=60,
        ),
        retries=3,
    )
    if not response.ok:
        raise ImageGenerationError(f"file upload failed: HTTP {response.status_code}")

    # Step 3: Mark upload as complete
    response = _retry(
        lambda: session.post(
            BASE_URL + f"/backend-api/files/{file_id}/uploaded",
            headers={
                "Authorization": f"Bearer {access_token}",
                "oai-device-id": device_id,
                "content-type": "application/json",
            },
            json={},
            timeout=30,
        ),
        retries=3,
    )
    if not response.ok:
        raise ImageGenerationError(f"file upload confirm failed: HTTP {response.status_code}")

    # Step 4: Poll until file is ready
    for _ in range(30):
        response = session.get(
            BASE_URL + f"/backend-api/files/{file_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "oai-device-id": device_id,
            },
            timeout=15,
        )
        if response.ok:
            status = response.json().get("status")
            if status == "success":
                print(f"[image-upload] file {file_id} ready")
                return file_id
            if status in ("error", "failed"):
                raise ImageGenerationError(f"file processing failed: {status}")
        time.sleep(1)

    raise ImageGenerationError("file upload timed out waiting for processing")


def generate_image_result(
    access_token: str,
    prompt: str,
    model: str = DEFAULT_MODEL,
    n: int = 1,
    image_data: bytes | None = None,
) -> dict:
    prompt = str(prompt or "").strip()
    access_token = str(access_token or "").strip()
    if not prompt:
        raise ImageGenerationError("prompt is required")
    if not access_token:
        raise ImageGenerationError("token is required")
    if n < 1:
        raise ImageGenerationError("n must be >= 1")

    session, fp = _new_session(access_token)
    try:
        upstream_model = _resolve_upstream_model(access_token, model)
        has_image = image_data is not None and len(image_data) > 0
        print(
            f"[image-upstream] start token={access_token[:12]}... "
            f"requested_model={model} upstream_model={upstream_model} n={n} has_ref_image={has_image}"
        )
        results: list[GeneratedImage] = []
        for _ in range(n):
            device_id = _bootstrap(session, fp)
            chat_token, pow_info = _chat_requirements(session, access_token, device_id)
            proof_token = None
            if pow_info.get("required"):
                proof_token = _generate_proof_token(
                    seed=str(pow_info["seed"]),
                    difficulty=str(pow_info["difficulty"]),
                    user_agent=USER_AGENT,
                    proof_config=_pow_config(USER_AGENT),
                )

            # Upload reference image if provided
            image_file_ids = None
            if has_image:
                file_id = _upload_image(session, access_token, device_id, image_data)
                image_file_ids = [file_id]

            parent_message_id = str(uuid.uuid4())
            response = _send_conversation(
                session,
                access_token,
                device_id,
                chat_token,
                proof_token,
                parent_message_id,
                prompt,
                upstream_model,
                image_file_ids=image_file_ids,
            )
            parsed = _parse_sse(response)
            actual_conversation_id = parsed.get("conversation_id") or ""
            file_ids = parsed.get("file_ids") or []
            response_text = str(parsed.get("text") or "").strip()
            if actual_conversation_id and not file_ids:
                file_ids = _poll_image_ids(session, access_token, device_id, actual_conversation_id)
            if not file_ids:
                if response_text:
                    raise ImageGenerationError(response_text)
                raise ImageGenerationError("no image returned from upstream")
            first_file_id = str(file_ids[0])
            download_url = _fetch_download_url(session, access_token, device_id, actual_conversation_id, first_file_id)
            if not download_url:
                raise ImageGenerationError("failed to get download url")
            results.append(
                GeneratedImage(
                    b64_json=_download_as_base64(session, download_url),
                    revised_prompt=prompt,
                    url=download_url,
                )
            )
        print(f"[image-upstream] success token={access_token[:12]}... images={len(results)}")
        return {
            "created": time.time_ns() // 1_000_000_000,
            "data": [{"b64_json": item.b64_json, "revised_prompt": item.revised_prompt} for item in results],
        }
    except Exception as exc:
        print(f"[image-upstream] fail token={access_token[:12]}... error={exc}")
        raise
    finally:
        session.close()
