"""Optional website password login; extension bearer credentials stay independent."""
import asyncio
import hashlib
import hmac
import html
import os
import secrets
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

COOKIE = "vcam_session"
# Public transport marker, never a credential. Requires a valid HttpOnly cookie.
SESSION_MARKER = "website-cookie-session-not-an-access-key"
SESSION_SECONDS = 8 * 60 * 60


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


class WebsiteLogin:
    def __init__(self):
        self.username = os.getenv("VCAM_LOGIN_ID", "").strip()
        self.password = os.getenv("VCAM_LOGIN_PASSWORD_HASH", "").strip()
        public_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
        # Railway terminates TLS before forwarding HTTP to the container.
        # Use its configured domain, never untrusted forwarded request headers.
        self.origin = os.getenv("VCAM_WEBSITE_ORIGIN", "").strip().rstrip("/") or (
            "https://" + public_domain if public_domain else "")
        self.enabled = bool(self.username or self.password)
        self.configured = False
        try:
            algorithm, iterations, salt, digest = self.password.split("$")
            self.configured = bool(self.username and len(self.username) <= 80 and
                algorithm == "pbkdf2_sha256" and iterations == "600000" and
                len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
        except ValueError:
            pass
        self.owner = "website-" + hashlib.sha256(self.username.encode()).hexdigest()[:24]
        self.key = hmac.new(self.password.encode(), ("website-api:" + self.username).encode(), hashlib.sha256).hexdigest()
        self.sessions = {}
        self.attempts = defaultdict(deque)
        self.global_attempts = deque()
        self.lock = asyncio.Lock()

    def valid(self, request):
        token = request.cookies.get(COOKIE, "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        return self.configured and self.sessions.get(digest, 0) > time.time()

    def same_origin(self, request):
        origin = request.headers.get("origin", "")
        expected = urlsplit(self.origin or str(request.base_url))
        actual = urlsplit(origin)
        return bool(origin and actual.scheme == expected.scheme and actual.netloc == expected.netloc)

    def register(self, app, dist):
        @app.middleware("http")
        async def website_session(request: Request, call_next):
            path = request.url.path
            eligible = path in {"/api/client-config", "/api/processing-config", "/api/realtime-token", "/api/edit-reference", "/api/references"} or path.startswith(("/api/references/", "/api/usage/"))
            if eligible and request.headers.get("authorization") == "Bearer " + SESSION_MARKER:
                if not self.valid(request):
                    return JSONResponse({"detail": "Your session expired. Sign in again."}, status_code=401)
                if request.method not in {"GET", "HEAD"} and not self.same_origin(request):
                    return JSONResponse({"detail": "Request origin rejected."}, status_code=403)
                request.scope["headers"] = [(k, v) for k, v in request.scope["headers"] if k.lower() != b"authorization"] + [(b"authorization", ("Bearer " + self.key).encode())]
            response = await call_next(request)
            if path in {"/", "/index.html", "/login.html"} or path.startswith("/api/website-"):
                response.headers["Cache-Control"] = "no-store"
            return response

        @app.get("/")
        @app.get("/index.html")
        async def studio(request: Request):
            if self.enabled:
                if not self.configured:
                    return HTMLResponse("Website login is not configured. Contact Bluqq support.", status_code=503)
                if not self.valid(request):
                    return RedirectResponse("/login.html", status_code=303)
            if not (dist / "index.html").is_file():
                return JSONResponse({"detail": "Run npm ci and npm run build first."}, status_code=503)
            page = (dist / "index.html").read_text(encoding="utf-8")
            if self.enabled:
                page = page.replace('id="personalAccessField"', 'id="personalAccessField" hidden')
                page = page.replace('id="accessKey"', f'id="accessKey" value="{SESSION_MARKER}"')
                menu = '<form id="websiteLogout" action="/api/website-logout" method="post"><span>Signed in as ' + html.escape(self.username) + '</span> <button type="submit">Sign out</button></form>'
                page = page.replace("<!-- ACCOUNT_MENU -->", menu)
                page = page.replace("Enter your personal key above, then refresh to see your saved photos.", "Press Refresh photos to see this account’s saved photos.")
                for old, new in {
                    "Your extension photo library": "Your photo library",
                    "Upload once. Choose it in your extension whenever you need it.": "Save reference photos in your signed-in account.",
                    "It will appear in the extension using the same personal access key.": "It will be saved to this website account’s private library.",
                    "everyone using this personal access key": "everyone using this website login",
                    "Save to extension": "Save photo",
                    "Load this key's library": "Load this account’s library",
                    "No new extension ZIP is needed for photo changes.": "Saved photos stay with this website account.",
                    "Anyone sharing the same key": "Anyone sharing this website login",
                }.items():
                    page = page.replace(old, new)
            return HTMLResponse(page)

        @app.post("/api/website-login")
        async def login(request: Request):
            if not self.configured:
                return JSONResponse({"detail": "Website login is not configured."}, status_code=503)
            if not self.same_origin(request):
                return JSONResponse({"detail": "Request origin rejected."}, status_code=403)
            now = time.monotonic()
            ip = request.client.host if request.client else "unknown"
            async with self.lock:
                self.attempts = defaultdict(deque, {k: deque(t for t in v if now-t < 300) for k,v in self.attempts.items() if v and now-v[-1] < 300})
                while self.global_attempts and now-self.global_attempts[0] >= 60:
                    self.global_attempts.popleft()
                if len(self.global_attempts) >= 30 or len(self.attempts[ip]) >= 5:
                    return JSONResponse({"detail": "Too many sign-in attempts. Try again in five minutes."}, status_code=429, headers={"Retry-After": "300"})
                self.attempts[ip].append(now)
                self.global_attempts.append(now)
            try:
                async with asyncio.timeout(10):
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > 2048:
                            raise ValueError()
                    import json
                    payload = json.loads(body)
                username, password = payload.get("username"), payload.get("password")
                if not isinstance(username, str) or not isinstance(password, str) or len(password) > 256:
                    raise ValueError()
            except (ValueError, AttributeError, TimeoutError):
                return JSONResponse({"detail": "Enter your ID and password."}, status_code=400)
            calculated = await run_in_threadpool(password_hash, password, self.password.split("$")[2])
            if not (hmac.compare_digest(calculated, self.password) and hmac.compare_digest(username.encode(), self.username.encode())):
                return JSONResponse({"detail": "ID or password is incorrect."}, status_code=401)
            self.attempts.pop(ip, None)
            self.sessions = {k:v for k,v in self.sessions.items() if v > time.time()}
            if len(self.sessions) >= 500:
                return JSONResponse({"detail": "Too many active sessions. Try again later."}, status_code=429)
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).hexdigest()] = time.time() + SESSION_SECONDS
            response = JSONResponse({"ok": True})
            response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True,
                                secure=self.origin.startswith("https://") or request.url.scheme == "https",
                                samesite="strict", path="/")
            return response

        @app.post("/api/website-logout")
        async def logout(request: Request):
            if not self.same_origin(request):
                return JSONResponse({"detail": "Request origin rejected."}, status_code=403)
            self.sessions.pop(hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest(), None)
            response = JSONResponse({"ok": True}) if request.headers.get("accept") == "application/json" else RedirectResponse("/login.html", status_code=303)
            response.delete_cookie(COOKIE, path="/")
            return response
