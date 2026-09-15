import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
import server

KEY_A = "test-reference-user-a-12345678901234567890"
KEY_B = "test-reference-user-b-12345678901234567890"
AUTH_A = {"Authorization": "Bearer " + KEY_A}
AUTH_B = {"Authorization": "Bearer " + KEY_B}


def photo(size=(640, 640), color="red", fmt="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=fmt)
    return output.getvalue()


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.env = patch.dict(os.environ, {
            "VCAM_DATA_DIR": self.folder.name, "RAILWAY_ENVIRONMENT_ID": "",
            "VCAM_ACCESS_KEYS": json.dumps({
                "alice": hashlib.sha256(KEY_A.encode()).hexdigest(),
                "bob": hashlib.sha256(KEY_B.encode()).hexdigest(),
            }),
        })
        self.env.start(); self.addCleanup(self.env.stop)
        self.client = TestClient(server.create_app()); self.addCleanup(self.client.close)

    def upload(self, data=None, headers=None):
        return self.client.post("/api/references", content=photo() if data is None else data,
                                headers={**AUTH_A, "Content-Type": "image/png", "X-Reference-Consent": "true", **(headers or {})})

    def test_crud_preserves_original_and_private_headers(self):
        original = photo()
        response = self.upload(original, {"X-Reference-Name": "My%20portrait"})
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        self.assertEqual(saved["name"], "My portrait")
        self.assertTrue(saved["thumbnail"].startswith("data:image/jpeg;base64,"))
        listing = self.client.get("/api/references", headers=AUTH_A)
        self.assertEqual(len(listing.json()["references"]), 1)
        self.assertNotIn(KEY_A, listing.text)
        image = self.client.get("/api/references/" + saved["id"], headers=AUTH_A)
        self.assertEqual(image.content, original)
        self.assertEqual(image.headers["cache-control"], "no-store")
        self.assertEqual(image.headers["x-content-type-options"], "nosniff")
        self.assertEqual(image.headers["content-type"], "image/png")
        self.assertEqual(self.client.delete("/api/references/" + saved["id"], headers=AUTH_A).status_code, 204)
        self.assertEqual(self.client.get("/api/references/" + saved["id"], headers=AUTH_A).status_code, 404)

    def test_cross_user_list_read_delete_are_blocked(self):
        saved = self.upload().json()
        self.assertEqual(self.client.get("/api/references", headers=AUTH_B).json()["references"], [])
        path = "/api/references/" + saved["id"]
        self.assertEqual(self.client.get(path, headers=AUTH_B).status_code, 404)
        self.assertEqual(self.client.delete(path, headers=AUTH_B).status_code, 404)
        self.assertEqual(self.client.get(path, headers=AUTH_A).status_code, 200)

    def test_auth_required_on_every_route(self):
        for method, path in [("get", "/api/references"), ("post", "/api/references"),
                             ("get", "/api/references/unknown"), ("delete", "/api/references/unknown")]:
            self.assertEqual(getattr(self.client, method)(path).status_code, 401)
        self.assertEqual(self.client.get("/api/references", headers={"Authorization": "Bearer " + "x" * 40}).status_code, 401)
        self.assertFalse((Path(self.folder.name) / "references.sqlite3").exists())

    def test_requires_storage_consent(self):
        self.assertEqual(self.upload(headers={"X-Reference-Consent": "false"}).status_code, 400)

    def test_duplicates_are_idempotent_and_still_isolated(self):
        first = self.upload().json()
        self.assertEqual(self.upload().json()["id"], first["id"])
        second = self.upload(headers=AUTH_B).json()
        self.assertNotEqual(first["id"], second["id"])

    def test_persists_across_application_restart(self):
        saved = self.upload().json()
        with TestClient(server.create_app()) as restarted:
            self.assertEqual(restarted.get("/api/references", headers=AUTH_A).json()["references"][0]["id"], saved["id"])

    def test_key_rotation_same_user_keeps_library_revoked_key_fails(self):
        self.upload()
        with patch.dict(os.environ, {"VCAM_ACCESS_KEYS": json.dumps({"alice": hashlib.sha256(KEY_B.encode()).hexdigest()})}):
            with TestClient(server.create_app()) as rotated:
                self.assertEqual(rotated.get("/api/references", headers=AUTH_A).status_code, 401)
                self.assertEqual(len(rotated.get("/api/references", headers=AUTH_B).json()["references"]), 1)

    def test_missing_storage_does_not_break_app(self):
        with patch.dict(os.environ, {"VCAM_DATA_DIR": ""}):
            with TestClient(server.create_app()) as disabled:
                self.assertEqual(disabled.get("/api/references", headers=AUTH_A).status_code, 503)
                self.assertNotEqual(disabled.get("/").status_code, 500)

    def test_railway_requires_configured_directory_inside_volume(self):
        with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT_ID": "test", "RAILWAY_VOLUME_MOUNT_PATH": self.folder.name + "/other"}):
            with TestClient(server.create_app()) as invalid:
                self.assertEqual(invalid.get("/api/references", headers=AUTH_A).status_code, 503)

    def test_bad_storage_path_returns_sanitized_error(self):
        with patch.dict(os.environ, {"VCAM_DATA_DIR": str(Path(__file__).resolve())}):
            with TestClient(server.create_app()) as invalid:
                result = invalid.get("/api/references", headers=AUTH_A)
                self.assertEqual(result.status_code, 503)
                self.assertNotIn("test_reference_library.py", result.text)

    def test_invalid_images_and_dimensions(self):
        self.assertEqual(self.upload(b"").status_code, 400)
        self.assertEqual(self.upload(b"not an image").status_code, 400)
        self.assertEqual(self.upload(photo((128, 128))).status_code, 400)
        self.assertEqual(self.upload(photo((4096, 4096))).status_code, 400)
        self.assertEqual(self.upload(photo(fmt="JPEG")).status_code, 415)
        self.assertEqual(self.upload(headers={"Content-Type": "image/svg+xml"}).status_code, 415)

    def test_animation_rejected(self):
        out = io.BytesIO()
        Image.new("RGB", (640, 640), "red").save(out, format="PNG", save_all=True, append_images=[Image.new("RGB", (640, 640), "blue")])
        self.assertEqual(self.upload(out.getvalue()).status_code, 400)

    def test_oversize_stream_rejected(self):
        self.assertEqual(self.upload(b"x" * (2 * 1024 * 1024 + 1)).status_code, 413)

    def test_bad_name_and_id(self):
        self.assertEqual(self.upload(headers={"X-Reference-Name": "x" * 81}).status_code, 400)
        self.assertEqual(self.upload(headers={"X-Reference-Name": "%0A"}).status_code, 400)
        self.assertEqual(self.client.get("/api/references/not-a-uuid", headers=AUTH_A).status_code, 404)

    def test_photo_count_limit(self):
        with patch("reference_library.MAX_PHOTOS", 1):
            self.assertEqual(self.upload().status_code, 201)
            self.assertEqual(self.upload(photo(color="blue")).status_code, 409)

    def test_upload_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(self.upload().status_code, 429)
