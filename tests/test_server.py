import hashlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import server

TEST_KEY = "test-only-not-a-real-credential-1234567890"
TEST_KEY_2 = "test-only-second-credential-123456789012"
AUTH = {"Authorization": "Bearer " + TEST_KEY}


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "DECART_API_KEY": "fake-upstream-key-never-use",
            "VCAM_ACCESS_KEYS": json.dumps({
                "tester": hashlib.sha256(TEST_KEY.encode()).hexdigest(),
                "tester2": hashlib.sha256(TEST_KEY_2.encode()).hexdigest(),
            }),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.sdk_patch = patch("server.DecartClient")
        self.sdk = self.sdk_patch.start()
        self.addCleanup(self.sdk_patch.stop)
        self.create_token = AsyncMock(return_value=SimpleNamespace(api_key="temporary-test-token"))
        self.sdk.return_value.__aenter__ = AsyncMock(
            return_value=SimpleNamespace(tokens=SimpleNamespace(create=self.create_token))
        )
        self.sdk.return_value.__aexit__ = AsyncMock(return_value=False)
        self.client = TestClient(server.create_app())
        self.addCleanup(self.client.close)

    def test_health_and_headers(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["configured"])
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["x-frame-options"], "DENY")
        self.assertNotIn("fake-upstream", r.text)

    def test_missing_auth_does_not_call_decart(self):
        self.assertEqual(self.client.post("/api/realtime-token").status_code, 401)
        self.sdk.assert_not_called()

    def test_wrong_auth_does_not_call_decart(self):
        r = self.client.post("/api/realtime-token", headers={"Authorization": "Bearer " + "x" * 40})
        self.assertEqual(r.status_code, 401)
        self.sdk.assert_not_called()

    def test_success_only_returns_temporary_key(self):
        r = self.client.post("/api/realtime-token", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"apiKey": "temporary-test-token"})
        self.create_token.assert_awaited_once_with(
            expires_in=120, allowed_models=["lucy-2.1", "lucy-2.5"],
            metadata={"app": "virtualcam-cloud"},
        )

    def test_user_rate_limit_and_isolation(self):
        for _ in range(5):
            self.assertEqual(self.client.post("/api/realtime-token", headers=AUTH).status_code, 200)
        r = self.client.post("/api/realtime-token", headers=AUTH)
        self.assertEqual(r.status_code, 429)
        self.assertIn("retry-after", r.headers)
        other = self.client.post("/api/realtime-token", headers={"Authorization": "Bearer " + TEST_KEY_2})
        self.assertEqual(other.status_code, 200)

    def test_minute_limit_expires(self):
        with patch("server.time.monotonic", return_value=1000):
            for _ in range(5):
                self.client.post("/api/realtime-token", headers=AUTH)
        with patch("server.time.monotonic", return_value=1061):
            self.assertEqual(self.client.post("/api/realtime-token", headers=AUTH).status_code, 200)

    def test_daily_limit(self):
        for minute in range(10):
            with patch("server.time.monotonic", return_value=1000 + minute * 61):
                for _ in range(5):
                    self.assertEqual(self.client.post("/api/realtime-token", headers=AUTH).status_code, 200)
        with patch("server.time.monotonic", return_value=2000):
            self.assertEqual(self.client.post("/api/realtime-token", headers=AUTH).status_code, 429)

    def test_bad_config_fails_closed(self):
        for config in ["{}", "bad-json", "[]", '{"a":"not-a-digest"}']:
            with self.subTest(config=config), patch.dict(os.environ, {"VCAM_ACCESS_KEYS": config}):
                with TestClient(server.create_app()) as client:
                    self.assertEqual(client.get("/api/health").status_code, 503)
                    self.assertEqual(client.post("/api/realtime-token", headers=AUTH).status_code, 503)

    def test_missing_decart_key(self):
        with patch.dict(os.environ, {"DECART_API_KEY": ""}):
            with TestClient(server.create_app()) as client:
                self.assertEqual(client.get("/api/health").status_code, 503)

    def test_revoked_key(self):
        with patch.dict(os.environ, {"VCAM_ACCESS_KEYS": json.dumps({
            "tester2": hashlib.sha256(TEST_KEY_2.encode()).hexdigest()
        })}):
            with TestClient(server.create_app()) as client:
                self.assertEqual(client.post("/api/realtime-token", headers=AUTH).status_code, 401)

    def test_upstream_error_is_sanitized(self):
        self.create_token.side_effect = RuntimeError("secret-sensitive-upstream-details")
        r = self.client.post("/api/realtime-token", headers=AUTH)
        self.assertEqual(r.status_code, 502)
        self.assertNotIn("secret-sensitive", r.text)

    def test_upstream_timeout(self):
        self.create_token.side_effect = TimeoutError()
        self.assertEqual(self.client.post("/api/realtime-token", headers=AUTH).status_code, 504)

    def test_static_frontend_and_removed_desktop_routes(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Personal access key", r.text)
        self.assertNotIn("Start meeting camera", r.text)
        for path in ["/api/obs/start", "/api/vcam/offer", "/api/vcam/stop"]:
            self.assertIn(self.client.post(path).status_code, (404, 405))
        self.assertEqual(self.client.get("/server.py").status_code, 404)
        self.assertEqual(self.client.get("/.env").status_code, 404)


if __name__ == "__main__":
    unittest.main()
