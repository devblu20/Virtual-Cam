"""Cloud-only website and authenticated Decart token API.
Run one process/replica: quotas are process-local, not video-minute limits.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

import uvicorn
from decart import DecartClient
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from reference_library import register_reference_routes
from usage_history import register_usage_routes, small_json, usage_metadata
from starlette.concurrency import run_in_threadpool

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
logger = logging.getLogger("virtualcam")


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

        usage = usage_metadata(await small_json(request))

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
