"""Cloud-only website and authenticated Decart token API.
Run one process/replica: quotas are process-local, not video-minute limits.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from pathlib import Path

import uvicorn
from decart import DecartClient
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from reference_library import register_reference_routes
from usage_history import register_usage_routes, small_json, usage_metadata
from starlette.concurrency import run_in_threadpool
from processing_config import load_processing_config

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
logger = logging.getLogger("virtualcam")


class OpenRouterError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def shirt_edit_prompt(color: str, mode: str, wrinkles: str, framing: str, aspect_ratio: str) -> str:
    wrinkle_instruction = {
        "natural": "Preserve or create subtle natural fabric wrinkles and soft folds that match the pose.",
        "visible": "Add clearly visible but natural fabric wrinkles, creases and folds around the chest, waist, underarms, elbows, sleeves and button placket, with physically consistent shadows and highlights.",
        "strong": "Add strong, prominent fabric wrinkles, creases and layered folds across the chest, waist, underarms, elbows, sleeves and button placket. Keep them realistic, pose-aware and clearly visible at normal viewing size without making the shirt damaged.",
    }[wrinkles]
    garment = (
        f"Change only the existing shirt colour to {color}. Preserve its exact collar, "
        f"placket, buttons, pockets, sleeves, seams and fit. {wrinkle_instruction} Preserve realistic drape."
        if mode == "recolor" else
        f"Inspect the upper garment. If it is already a collared button-front shirt, change "
        f"its colour to {color} while preserving its construction and details. If it is a "
        f"kurta, kurti, T-shirt, blouse, dress top or another garment, replace only that upper "
        f"garment with a plain {color} collared button-front shirt. Fit the new shirt naturally "
        f"to the existing body and pose with realistic collar, placket, buttons, seams and sleeves. "
        f"{wrinkle_instruction} Preserve realistic drape and keep the neckline modestly covered."
    )
    frame = (
        f"Create a professional chest-up {aspect_ratio} reference portrait matching this exact composition: "
        "the person faces forward and is centred; the entire hair and head remain visible with about 8 percent "
        "clear space above the hair; both shoulders and both upper arms are visible; show the shirt down to the "
        "lower torso or waist; the person occupies roughly 75 to 85 percent of the frame height. Keep the camera "
        "level and do not crop the hair, chin, shoulders, sleeves or shirt front."
        if framing == "auto" else
        "Preserve the supplied image's exact framing, crop, composition and aspect ratio."
    )
    return f"""Edit the supplied reference portrait with strict garment-only inpainting.

{garment}

Keep the shirt plain without copied patterns, embroidery, logos or text. The wrinkles must be visible at normal viewing size while remaining realistic and naturally worn. Do not make the shirt smooth, ironed, plastic-looking, excessively crumpled or damaged.

Keep every non-garment detail unchanged: identity, gender, face, facial features, expression, hair, beard, skin tone, neck, jewellery, hands, body shape, pose, proportions, background, camera angle, focus, lighting and image quality. Do not retouch the person.

{frame}

Return one photorealistic edited image only."""


def request_openrouter_image(api_key: str, payload: dict) -> tuple[bytes, str]:
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/images",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://virtual-cam-production-9734.up.railway.app/",
            "X-OpenRouter-Title": "Bluqq Virtual CAM",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=95) as upstream:
            body = upstream.read(30 * 1024 * 1024)
    except urllib.error.HTTPError as error:
        raw = error.read(64 * 1024)
        try:
            detail = json.loads(raw).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            detail = ""
        safe = detail if isinstance(detail, str) and len(detail) <= 500 else ""
        raise OpenRouterError(error.code, safe or "Image-editing provider rejected the request.") from None
    except (urllib.error.URLError, TimeoutError):
        raise OpenRouterError(504, "Image editing timed out. Try again.") from None

    try:
        result = json.loads(body)
        output = result["data"][0]
        encoded = output["b64_json"]
        media_type = output.get("media_type") or "image/jpeg"
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, KeyError, IndexError):
        raise OpenRouterError(502, "Image-editing provider returned an invalid image.") from None
    if not image or len(image) > 20 * 1024 * 1024 or media_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise OpenRouterError(502, "Image-editing provider returned an unsupported image.")
    return image, media_type


def load_access_keys() -> dict[str, str]:
    try:
        keys = json.loads(os.getenv("VCAM_ACCESS_KEYS", "{}"))
        if not isinstance(keys, dict) or not 1 <= len(keys) <= 1000:
            return {}
        if not all(
            isinstance(user, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user)
            and isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)
            for user, digest in keys.items()
        ):
            return {}
        if len(set(keys.values())) != len(keys):
            return {}
        return keys
    except (ValueError, TypeError):
        return {}


def create_app() -> FastAPI:
    app = FastAPI(title="Virtual CAM Cloud", docs_url=None, redoc_url=None, openapi_url=None)
    api_key = os.getenv("DECART_API_KEY", "").strip()
    access_keys = load_access_keys()
    dist = BASE_DIR / "dist"
    ready = bool(api_key and access_keys and (dist / "index.html").is_file())
    minute_requests: dict[str, deque[float]] = defaultdict(deque)
    daily_requests: dict[str, deque[float]] = defaultdict(deque)
    global_requests: deque[float] = deque()
    quota_lock = asyncio.Lock()
    edit_minute_requests: dict[str, deque[float]] = defaultdict(deque)
    edit_daily_requests: dict[str, deque[float]] = defaultdict(deque)
    edit_global_requests: deque[float] = deque()
    edit_quota_lock = asyncio.Lock()
    usage_store = register_usage_routes(app, access_keys)

    async def record_usage(method, *args):
        # An analytics outage must not stop a camera session. Never log input data.
        try:
            return await run_in_threadpool(method, *args)
        except HTTPException:
            logger.warning("Usage history unavailable; video authorization continues")
            return None

    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health():
        return JSONResponse(
            {"app": "virtualcam-cloud", "configured": ready},
            status_code=200 if ready else 503,
        )

    @app.get("/api/processing-config")
    async def processing_configuration(request: Request):
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not 32 <= len(supplied) <= 256:
            raise HTTPException(401, "Enter a valid personal access key.")
        candidate = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        if not any(hmac.compare_digest(candidate, digest) for digest in access_keys.values()):
            raise HTTPException(401, "Access key is invalid or revoked.")
        configuration = await run_in_threadpool(load_processing_config)
        return JSONResponse(configuration)

    @app.post("/api/realtime-token")
    async def realtime_token(request: Request):
        if not ready:
            raise HTTPException(503, "Server is not configured. Contact the app owner.")
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not 32 <= len(supplied) <= 256:
            raise HTTPException(401, "Enter a valid personal access key.")
        candidate = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        user = None
        for user_id, digest in access_keys.items():
            if hmac.compare_digest(candidate, digest):
                user = user_id
        if user is None:
            raise HTTPException(401, "Access key is invalid or revoked.")

        body = await small_json(request)
        usage = usage_metadata(body)
        configuration = None
        if isinstance(body, dict) and "processingConfigVersion" in body:
            if type(body["processingConfigVersion"]) is not int or body["processingConfigVersion"] != 1:
                raise HTTPException(400, "Unsupported processing configuration version.")
            configuration = await run_in_threadpool(load_processing_config)

        async with quota_lock:
            now = time.monotonic()
            limits = [(minute_requests[user], 60, 5), (daily_requests[user], 86400, 50),
                      (global_requests, 60, 30)]
            for bucket, window, maximum in limits:
                while bucket and now - bucket[0] >= window:
                    bucket.popleft()
                if len(bucket) >= maximum:
                    retry = max(1, int(window - (now - bucket[0])) + 1)
                    raise HTTPException(429, "Connection request limit reached. Try later.",
                                        headers={"Retry-After": str(retry)})
            for bucket, _, _ in limits:
                bucket.append(now)
        usage_id = await record_usage(usage_store.start, user, usage) if usage else None
        try:
            async with asyncio.timeout(25):
                async with DecartClient(api_key=api_key) as client:
                    token = await client.tokens.create(
                        expires_in=120,
                        allowed_models=["lucy-2.1", "lucy-2.5"],
                        metadata={"app": "virtualcam-cloud"},
                    )
            result = {"apiKey": token.api_key}
            if configuration is not None:
                result["processingConfig"] = configuration
            if usage:
                result.update(usageSessionId=usage_id, usageRecorded=bool(usage_id))
            return result
        except TimeoutError:
            if usage_id:
                await record_usage(usage_store.fail, usage_id, "provider_timeout")
            raise HTTPException(504, "AI processing timed out. Try again.") from None
        except Exception:
            if usage_id:
                await record_usage(usage_store.fail, usage_id, "provider_error")
            # Upstream exception text may contain credentials. Do not log it.
            logger.warning("Decart token request failed")
            raise HTTPException(502, "AI processing connection failed. Ask Bluqq support to check service access and credits.") from None

    @app.post("/api/edit-reference")
    async def edit_reference(request: Request):
        openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not openrouter_key:
            raise HTTPException(503, "Reference image editing is not configured yet.")

        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not 32 <= len(supplied) <= 256:
            raise HTTPException(401, "Enter a valid personal access key.")
        candidate = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        user = None
        for user_id, digest in access_keys.items():
            if hmac.compare_digest(candidate, digest):
                user = user_id
        if user is None:
            raise HTTPException(401, "Access key is invalid or revoked.")

        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(415, "Choose a JPEG, PNG or WebP reference image.")
        content_length = request.headers.get("content-length", "")
        if content_length.isdigit() and int(content_length) > 10 * 1024 * 1024:
            raise HTTPException(413, "Reference image must be 10 MB or smaller.")
        image = await request.body()
        if not image or len(image) > 10 * 1024 * 1024:
            raise HTTPException(413, "Reference image must be between 1 byte and 10 MB.")

        color = request.query_params.get("color", "soft blush pink").strip()
        mode = request.query_params.get("mode", "auto")
        wrinkles = request.query_params.get("wrinkles", "visible")
        framing = request.query_params.get("framing", "auto")
        aspect_ratio = request.query_params.get("aspect_ratio", "4:5")
        if not 1 <= len(color) <= 80 or any(ord(character) < 32 for character in color):
            raise HTTPException(400, "Enter a valid shirt colour.")
        if mode not in {"auto", "recolor"} or framing not in {"auto", "original"}:
            raise HTTPException(400, "Invalid shirt edit option.")
        if wrinkles not in {"natural", "visible", "strong"}:
            raise HTTPException(400, "Invalid shirt wrinkle option.")
        if aspect_ratio not in {"4:5", "3:4", "1:1"}:
            raise HTTPException(400, "Invalid frame shape.")

        async with edit_quota_lock:
            now = time.monotonic()
            limits = [(edit_minute_requests[user], 60, 3),
                      (edit_daily_requests[user], 86400, 30),
                      (edit_global_requests, 60, 12)]
            for bucket, window, maximum in limits:
                while bucket and now - bucket[0] >= window:
                    bucket.popleft()
                if len(bucket) >= maximum:
                    retry = max(1, int(window - (now - bucket[0])) + 1)
                    raise HTTPException(429, "Image edit limit reached. Try later.",
                                        headers={"Retry-After": str(retry)})
            for bucket, _, _ in limits:
                bucket.append(now)

        source = f"data:{content_type};base64,{base64.b64encode(image).decode('ascii')}"
        payload = {
            "model": "google/gemini-3.1-flash-lite-image",
            "prompt": shirt_edit_prompt(color, mode, wrinkles, framing, aspect_ratio),
            "input_references": [{"type": "image_url", "image_url": {"url": source}}],
            "resolution": "1K",
            "output_format": "jpeg",
            "n": 1,
        }
        if framing == "auto":
            payload["aspect_ratio"] = aspect_ratio
        try:
            edited, media_type = await run_in_threadpool(request_openrouter_image, openrouter_key, payload)
            return Response(edited, media_type=media_type, headers={"Content-Disposition": "inline"})
        except OpenRouterError as error:
            if error.status == 402:
                raise HTTPException(402, "OpenRouter credits are insufficient.") from None
            if error.status == 429:
                raise HTTPException(429, "Image-editing provider rate limit reached. Try again shortly.") from None
            if error.status == 504:
                raise HTTPException(504, str(error)) from None
            logger.warning("OpenRouter image edit failed with status %s", error.status)
            raise HTTPException(502, "Reference image editing failed. Try again.") from None

    register_reference_routes(app, access_keys)

    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    else:
        @app.get("/")
        async def missing_build():
            return JSONResponse({"detail": "Run npm ci and npm run build first."}, status_code=503)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "5173")))
