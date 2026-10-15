import hashlib
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import server
from website_login import COOKIE, SESSION_MARKER, WebsiteLogin, password_hash

TEST_PASSWORD = "local-test-password-only"
TEST_HASH = password_hash(TEST_PASSWORD)
ORIGIN = {"Origin": "https://testserver"}
WEB = {**ORIGIN, "Authorization": "Bearer " + SESSION_MARKER}


class WebsiteLoginTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = patch.dict(os.environ, {
            "VCAM_LOGIN_ID": "test-studio", "VCAM_LOGIN_PASSWORD_HASH": TEST_HASH,
            "VCAM_DATA_DIR": temp.name, "RAILWAY_ENVIRONMENT_ID": "",
            "RAILWAY_PUBLIC_DOMAIN": "", "VCAM_WEBSITE_ORIGIN": "",
            "DECART_API_KEY": "fake-provider-key", "VCAM_ADMIN_KEY_SHA256": "",
            "VCAM_ACCESS_KEYS": json.dumps({"extension": hashlib.sha256(b"test-extension-access-key-123456789012345").hexdigest()}),
        })
        env.start(); self.addCleanup(env.stop)
        self.client = TestClient(server.create_app(), base_url="https://testserver")
        self.addCleanup(self.client.close)

    def login(self, password=TEST_PASSWORD, headers=ORIGIN):
        return self.client.post("/api/website-login", headers=headers, json={"username": "test-studio", "password": password})

    def test_studio_requires_login_both_paths(self):
        for path in ("/", "/index.html"):
            result = self.client.get(path, follow_redirects=False)
            self.assertEqual(result.status_code, 303)
            self.assertEqual(result.headers["location"], "/login.html")
            self.assertEqual(result.headers["cache-control"], "no-store")
        page = self.client.get("/login.html")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(TEST_PASSWORD, page.text)
        self.assertNotIn(TEST_HASH, page.text)

    def test_login_studio_cookie_and_library(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        for flag in ("HttpOnly", "Secure", "SameSite=strict", "Max-Age=28800"):
            self.assertIn(flag, response.headers["set-cookie"])
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="personalAccessField" hidden', page.text)
        self.assertIn(SESSION_MARKER, page.text)
        self.assertIn("Signed in as test-studio", page.text)
        self.assertNotIn(TEST_HASH, page.text)
        self.assertNotIn(TEST_PASSWORD, page.text)
        self.assertEqual(self.client.get("/api/references", headers=WEB).status_code, 200)
        self.assertEqual(self.client.get("/api/client-config?version=3", headers=WEB).status_code, 200)

    def test_marker_alone_is_not_a_credential_and_admin_is_separate(self):
        self.assertEqual(self.client.get("/api/references", headers=WEB).status_code, 401)
        self.login()
        self.assertIn(self.client.get("/api/admin/users", headers=WEB).status_code, (403, 503))
        self.assertEqual(self.client.get("/api/interviews", headers=WEB).status_code, 401)

    def test_login_and_cookie_writes_reject_cross_origin(self):
        self.assertEqual(self.login(headers={"Origin": "https://elsewhere.invalid"}).status_code, 403)
        self.assertEqual(self.login(headers={}).status_code, 403)
        self.login()
        response = self.client.post("/api/realtime-token", headers={**WEB, "Origin": "https://elsewhere.invalid"}, json={})
        self.assertEqual(response.status_code, 403)

    def test_logout_revokes_copied_cookie_and_blocks_studio(self):
        self.login()
        cookie = self.client.cookies.get(COOKIE)
        response = self.client.post("/api/website-logout", headers=ORIGIN, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.client.cookies.set(COOKIE, cookie)
        self.assertEqual(self.client.get("/api/references", headers=WEB).status_code, 401)
        self.assertEqual(self.client.get("/", follow_redirects=False).status_code, 303)

    def test_failed_attempts_are_limited(self):
        for _ in range(5):
            self.assertEqual(self.login("incorrect").status_code, 401)
        response = self.login("incorrect")
        self.assertEqual(response.status_code, 429)
        self.assertIn("retry-after", response.headers)

    def test_session_expiration_and_tampering(self):
        self.login()
        with patch("website_login.time.time", return_value=time.time() + 28801):
            self.assertEqual(self.client.get("/api/references", headers=WEB).status_code, 401)
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE, "forged")
        self.assertEqual(self.client.get("/api/references", headers=WEB).status_code, 401)

    def test_website_and_existing_extension_can_request_tokens(self):
        self.login()
        with patch("server.DecartClient") as sdk:
            create = AsyncMock(return_value=SimpleNamespace(api_key="fake-temporary-token"))
            sdk.return_value.__aenter__ = AsyncMock(return_value=SimpleNamespace(tokens=SimpleNamespace(create=create)))
            sdk.return_value.__aexit__ = AsyncMock(return_value=False)
            response = self.client.post("/api/realtime-token", headers=WEB, json={"processingConfigVersion": 3})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["processingConfig"]["userId"].startswith("website-"))
            self.client.cookies.clear()
            response = self.client.post("/api/realtime-token", headers={"Authorization": "Bearer test-extension-access-key-123456789012345"}, json={})
            self.assertEqual(response.status_code, 200)

    def test_partial_config_fails_closed(self):
        with patch.dict(os.environ, {"VCAM_LOGIN_PASSWORD_HASH": ""}):
            with TestClient(server.create_app()) as client:
                self.assertEqual(client.get("/").status_code, 503)
                self.assertEqual(client.get("/api/health").status_code, 503)

    def test_website_identity_survives_restart(self):
        one, two = WebsiteLogin(), WebsiteLogin()
        self.assertEqual(one.key, two.key)
        self.assertEqual(one.owner, two.owner)

    def test_railway_tls_proxy_uses_public_origin_and_secure_cookie(self):
        with patch.dict(os.environ, {"RAILWAY_PUBLIC_DOMAIN": "studio.up.railway.app"}):
            with TestClient(server.create_app(), base_url="http://internal-service") as client:
                response = client.post("/api/website-login", headers={"Origin": "https://studio.up.railway.app"},
                                       json={"username": "test-studio", "password": TEST_PASSWORD})
                self.assertEqual(response.status_code, 200)
                self.assertIn("Secure", response.headers["set-cookie"])
                response = client.post("/api/website-login", headers={"Origin": "http://internal-service"},
                                       json={"username": "test-studio", "password": TEST_PASSWORD})
                self.assertEqual(response.status_code, 403)
