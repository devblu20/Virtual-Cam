import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import server
from usage_history import UsageStore, usage_metadata

KEY = "test-only-personal-credential-1234567890"
OTHER = "test-only-other-credential-123456789012"
ADMIN = "test-only-admin-credential-123456789012"
AUTH = {"Authorization": "Bearer " + KEY}
ADMIN_AUTH = {"Authorization": "Bearer " + ADMIN}
META = {"source": "extension", "platform": "zoom", "version": "0.3.12",
        "avatar": {"kind": "bundled", "id": "portrait-01", "name": "Portrait One"}}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {
            "VCAM_DATA_DIR": self.directory.name, "RAILWAY_ENVIRONMENT_ID": "",
            "DECART_API_KEY": "test-only-provider", "VCAM_ADMIN_KEY_SHA256": hashlib.sha256(ADMIN.encode()).hexdigest(),
            "VCAM_ACCESS_KEYS": json.dumps({"alice": hashlib.sha256(KEY.encode()).hexdigest(), "bob": hashlib.sha256(OTHER.encode()).hexdigest()}),
        })
        self.env.start(); self.addCleanup(self.env.stop)
        sdk_patch = patch("server.DecartClient"); sdk = sdk_patch.start(); self.addCleanup(sdk_patch.stop)
        self.token = AsyncMock(return_value=SimpleNamespace(api_key="temporary-test-token"))
        sdk.return_value.__aenter__ = AsyncMock(return_value=SimpleNamespace(tokens=SimpleNamespace(create=self.token)))
        sdk.return_value.__aexit__ = AsyncMock(return_value=False)
        self.client = TestClient(server.create_app()); self.addCleanup(self.client.close)

    def start(self, at=1000, metadata=None):
        with patch("usage_history.time.time", return_value=at):
            response = self.client.post("/api/realtime-token", headers=AUTH, json={"usage": metadata or META})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["usageRecorded"])
        return response.json()["usageSessionId"]

    def event(self, identifier, action, sequence, at, **values):
        with patch("usage_history.time.time", return_value=at):
            return self.client.post(f"/api/usage/{identifier}/events", headers=AUTH,
                                    json={"action": action, "sequence": sequence, **values})

    def history(self, at=1040, query=""):
        with patch("usage_history.time.time", return_value=at):
            response = self.client.get("/api/admin/usage" + query, headers=ADMIN_AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def test_administrator_only(self):
        for headers in [{}, AUTH, {"Authorization": "Bearer " + OTHER}]:
            self.assertIn(self.client.get("/api/admin/usage", headers=headers).status_code, [401, 403])
        self.assertEqual(self.history()["total"], 0)

    def test_name_is_trimmed_snapshotted_and_never_changes_ownership(self):
        first = self.start(metadata={**META, "participantName": "  देव शर्मा  ", "owner": "bob"})
        self.start(2000, {**META, "participantName": "Asha"})
        rows = self.history(2001)["items"]
        self.assertEqual([r["participant_name"] for r in rows], ["Asha", "देव शर्मा"])
        self.assertEqual({r["owner"] for r in rows}, {"alice"})
        self.event(first, "end", 1, 2010, participantName="Someone else")
        self.assertEqual(self.history(2011)["items"][1]["participant_name"], "देव शर्मा")

    def test_name_validation_and_old_client_compatibility(self):
        for value in ["", "   ", 12, None, "a" * 81, "Name\n", "Name\x00", "Name\u202e"]:
            response = self.client.post("/api/realtime-token", headers=AUTH, json={"usage": {**META, "participantName": value}})
            self.assertEqual(response.status_code, 400, repr(value))
        self.start()
        self.assertIsNone(self.history()["items"][0]["participant_name"])
        self.assertEqual(usage_metadata({"usage": {**META, "participantName": "Jose\u0301"}})["participant_name"], "José")

    def test_legacy_database_migration_preserves_rows_and_is_repeatable(self):
        identifier = self.start()
        self.event(identifier, "pulse", 1, 1010)
        self.event(identifier, "end", 2, 1030)
        with UsageStore().connection() as db:
            db.execute("ALTER TABLE usage_sessions DROP COLUMN participant_name")
        before = self.history()["items"][0]
        self.assertEqual(before["id"], identifier)
        self.assertIsNone(before["participant_name"])
        self.assertEqual(before["seconds"], 20)
        self.assertEqual(len(before["avatars"]), 1)
        self.start(2000, {**META, "participantName": "New user"})
        with TestClient(server.create_app()) as fresh:
            records = fresh.get("/api/admin/usage", headers=ADMIN_AUTH).json()["items"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["participant_name"], "New user")
        self.assertEqual(records[1]["id"], identifier)

    def test_admin_must_not_share_personal_key(self):
        for value in ["", "bad", hashlib.sha256(KEY.encode()).hexdigest()]:
            with patch.dict(os.environ, {"VCAM_ADMIN_KEY_SHA256": value}), TestClient(server.create_app()) as client:
                self.assertEqual(client.get("/api/admin/usage", headers=ADMIN_AUTH).status_code, 503)

    def test_duration_starts_with_activity_not_token_and_segments_add_up(self):
        identifier = self.start()
        self.event(identifier, "pulse", 1, 1010)
        self.event(identifier, "pulse", 2, 1030)
        self.event(identifier, "avatar", 3, 1040, avatar={"kind": "bundled", "id": "portrait-02", "name": "Portrait Two"})
        self.event(identifier, "pulse", 4, 1050)
        self.event(identifier, "end", 5, 1060, reason="stopped")
        item = self.history(1060)["items"][0]
        self.assertEqual(item["seconds"], 50)
        self.assertEqual([a["seconds"] for a in item["avatars"]], [30, 20])
        self.assertEqual(item["started"], 1010)
        self.assertEqual(item["ended"], 1060)
        self.assertEqual(item["state"], "ended")
        self.assertTrue(item["estimated"])

    def test_no_activity_is_not_counted(self):
        identifier = self.start()
        self.event(identifier, "end", 1, 1020)
        item = self.history()["items"][0]
        self.assertEqual(item["seconds"], 0)
        self.assertIsNone(item["started"])

    def test_crash_does_not_extrapolate_duration(self):
        identifier = self.start()
        self.event(identifier, "pulse", 1, 1010)
        self.event(identifier, "pulse", 2, 1030)
        item = self.history(5000)["items"][0]
        self.assertEqual(item["state"], "interrupted")
        self.assertEqual(item["seconds"], 20)
        self.assertIsNone(item["ended"])

    def test_unconfirmed_connection_and_missing_intervals(self):
        identifier = self.start()
        self.assertEqual(self.history(1100)["items"][0]["state"], "unconfirmed")
        self.event(identifier, "pulse", 1, 1100)
        self.event(identifier, "pulse", 2, 1200)
        self.event(identifier, "end", 3, 1210)
        item = self.history(1210)["items"][0]
        self.assertEqual(item["seconds"], 10)
        self.assertEqual(item["has_gaps"], 1)

    def test_event_idempotency_and_cannot_reopen(self):
        identifier = self.start()
        for action, seq, at in [("pulse", 1, 1010), ("pulse", 2, 1030), ("pulse", 2, 1040),
                                ("pulse", 1, 1041), ("end", 3, 1045), ("pulse", 4, 1050)]:
            self.assertEqual(self.event(identifier, action, seq, at).status_code, 200)
        item = self.history(1050)["items"][0]
        self.assertEqual(item["seconds"], 35)
        self.assertEqual(item["sequence"], 3)

    def test_cross_user_events_and_admin_as_user_rejected(self):
        identifier = self.start()
        for headers, expected in [({"Authorization": "Bearer " + OTHER}, 404), (ADMIN_AUTH, 401)]:
            response = self.client.post(f"/api/usage/{identifier}/events", headers=headers, json={"action": "pulse", "sequence": 1})
            self.assertEqual(response.status_code, expected)

    def test_provider_failures_record_no_video_time(self):
        self.token.side_effect = RuntimeError("do-not-log-provider-secret")
        response = self.client.post("/api/realtime-token", headers=AUTH, json={"usage": META})
        self.assertEqual(response.status_code, 502)
        item = self.history()["items"][0]
        self.assertEqual(item["state"], "failed")
        self.assertEqual(item["seconds"], 0)
        self.assertNotIn("secret", str(item))

    def test_old_clients_remain_compatible(self):
        response = self.client.post("/api/realtime-token", headers=AUTH)
        self.assertEqual(response.json(), {"apiKey": "temporary-test-token"})
        self.assertEqual(self.history()["total"], 0)

    def test_storage_failure_does_not_break_video(self):
        with patch.dict(os.environ, {"VCAM_DATA_DIR": ""}), TestClient(server.create_app()) as client:
            response = client.post("/api/realtime-token", headers=AUTH, json={"usage": META})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["usageRecorded"])
            self.assertEqual(client.get("/api/admin/usage", headers=ADMIN_AUTH).status_code, 503)

    def test_persistence_and_filters(self):
        self.start(1000)
        self.start(2000)
        with TestClient(server.create_app()) as fresh:
            self.assertEqual(fresh.get("/api/admin/usage", headers=ADMIN_AUTH).json()["total"], 2)
        result = self.history(2000, "?owner=alice&platform=zoom&after=1500&limit=1")
        self.assertEqual(result["total"], 1)
        self.assertEqual(self.history(query="?owner=bob")["items"], [])
        self.assertEqual(len(self.history(query="?offset=1&limit=1")["items"]), 1)

    def test_metadata_is_bounded_and_invalid_events_rejected(self):
        self.assertEqual(self.client.post("/api/realtime-token", headers=AUTH, content="x" * 4097).status_code, 413)
        self.assertEqual(self.client.post("/api/realtime-token", headers=AUTH, json={"usage": {**META, "platform": "https://zoom.us/private"}}).status_code, 400)
        identifier = self.start()
        for data in [{"action": "pulse", "sequence": True}, {"action": "pulse", "sequence": -1},
                     {"action": "record-video", "sequence": 1}, {"action": "end", "sequence": 1, "reason": "secret meeting title"}]:
            self.assertEqual(self.client.post(f"/api/usage/{identifier}/events", headers=AUTH, json=data).status_code, 400)
        for query in ["?limit=10000", "?offset=-1", "?after=nan", "?platform=invalid"]:
            self.assertEqual(self.client.get("/api/admin/usage" + query, headers=ADMIN_AUTH).status_code, 400)

    def test_no_credentials_or_images_in_usage_records(self):
        self.start()
        with closing(sqlite3.connect(Path(self.directory.name) / "references.sqlite3")) as db:
            serialized = repr(db.execute("SELECT * FROM usage_sessions").fetchall()) + repr(db.execute("SELECT * FROM usage_avatars").fetchall())
        for forbidden in [KEY, OTHER, ADMIN, "temporary-test-token", "data:image", "https://"]:
            self.assertNotIn(forbidden, serialized)

    def test_library_lookup_cannot_leak_other_users_photo_names(self):
        store = UsageStore()
        with store.connection() as db:
            db.execute("INSERT INTO photos(id,owner,name,mime,width,height,created,digest,image,thumbnail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("12345678-1234-4123-8123-123456789abc", "bob", "PRIVATE BOB NAME", "image/png", 512, 512, 1, "a" * 64, b"test", b"test"))
        meta = {**META, "avatar": {"kind": "library", "id": "12345678-1234-4123-8123-123456789abc"}}
        self.start(metadata=meta)
        item = self.history()["items"][0]
        self.assertNotIn("PRIVATE BOB NAME", str(item))
        self.assertEqual(item["avatars"][0]["name"], "Unavailable library photo")

    def test_upload_resolves_own_library_name_and_survives_photo_deletion(self):
        store = UsageStore()
        with store.connection() as db:
            db.execute("INSERT INTO photos(id,owner,name,mime,width,height,created,digest,image,thumbnail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       ("12345678-1234-4123-8123-123456789abc", "alice", "My Avatar", "image/png", 512, 512, 1, "a" * 64, b"test", b"test"))
        self.start(metadata={"source": "website", "platform": "website", "version": "test", "avatar": {"kind": "upload", "name": "local.jpg", "digest": "a" * 64}})
        with store.connection() as db:
            db.execute("DELETE FROM photos")
        item = self.history()["items"][0]
        self.assertEqual(item["avatars"][0]["kind"], "library")
        self.assertEqual(item["avatars"][0]["name"], "My Avatar")

    def test_request_limit_and_history_capacity(self):
        identifier = self.start()
        with patch("usage_history.time.monotonic", return_value=1000):
            for _ in range(180):
                response = self.client.post(f"/api/usage/{identifier}/events", headers=AUTH, json={"action": "pulse", "sequence": 1})
                self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post(f"/api/usage/{identifier}/events", headers=AUTH, json={"action": "pulse", "sequence": 2}).status_code, 429)
        with patch("usage_history.MAX_SESSIONS", 1):
            response = self.client.post("/api/realtime-token", headers=AUTH, json={"usage": META})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["usageRecorded"])

    def test_upload_name_never_contains_local_path(self):
        meta = usage_metadata({"usage": {"source": "website", "platform": "website", "version": "test",
                            "avatar": {"kind": "upload", "name": "C:\\private\\portrait.jpg", "digest": "b" * 64}}})
        self.assertEqual(meta["avatar"]["name"], "portrait.jpg")

    def test_avatar_limit_rolls_back_partial_time_and_end_still_works(self):
        identifier = self.start()
        self.event(identifier, "pulse", 1, 1010)
        with UsageStore().connection() as db:
            db.execute("UPDATE usage_avatars SET ordinal=99 WHERE session_id=?", (identifier,))
        response = self.event(identifier, "avatar", 2, 1030, avatar=META["avatar"])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.history()["items"][0]["seconds"], 0)
        self.event(identifier, "end", 3, 1035)
        self.assertEqual(self.history()["items"][0]["seconds"], 25)
