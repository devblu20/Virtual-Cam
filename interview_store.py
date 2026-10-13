"""Private, access-key-scoped interview records stored on the Railway volume."""
import asyncio
import base64
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import time
import uuid
import zipfile
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from reference_library import validate_image

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_RESUME_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 28 * 1024 * 1024
MAX_INTERVIEWS = 50
PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}
RESUME_MIMES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def decode_file(value, maximum, label):
    if not isinstance(value, str) or not value or len(value) > (maximum * 4 // 3) + 8:
        raise HTTPException(400, f"Choose a valid {label.lower()}.")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(400, f"The {label.lower()} could not be decoded.") from None
    if not data or len(data) > maximum:
        raise HTTPException(413, f"{label} must be at most {maximum // (1024 * 1024)} MB.")
    return data


def validate_resume(data, mime, filename):
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if RESUME_MIMES.get(mime) != extension:
        raise HTTPException(415, "Use an actual PDF, DOC or DOCX résumé.")
    valid = False
    if extension == "pdf":
        valid = data.startswith(b"%PDF-")
    elif extension == "doc":
        valid = data.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    elif extension == "docx" and data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                valid = "word/document.xml" in archive.namelist()
        except (zipfile.BadZipFile, OSError):
            valid = False
    if not valid:
        raise HTTPException(415, "Use an actual PDF, DOC or DOCX résumé.")


class InterviewStore:
    def __init__(self):
        configured = os.getenv("VCAM_DATA_DIR", "").strip()
        self.directory = Path(configured).resolve() if configured else None
        if os.getenv("RAILWAY_ENVIRONMENT_ID") and self.directory:
            mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "")
            if not mount or not self.directory.is_relative_to(Path(mount).resolve()):
                self.directory = None

    @contextmanager
    def connection(self):
        if self.directory is None:
            raise HTTPException(503, "Interview storage is not configured. Ask Bluqq to attach a Railway volume.")
        db = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.directory / "interviews.sqlite3", timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            db.execute("""CREATE TABLE IF NOT EXISTS interviews (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, candidate_name TEXT NOT NULL,
                interview_date TEXT NOT NULL, start_time TEXT NOT NULL, duration INTEGER NOT NULL,
                photo_name TEXT NOT NULL, photo_mime TEXT NOT NULL, photo BLOB NOT NULL, thumbnail BLOB NOT NULL,
                resume_name TEXT NOT NULL, resume_mime TEXT NOT NULL, resume BLOB NOT NULL,
                created INTEGER NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS interviews_owner ON interviews(owner)")
            with db:
                yield db
        except (sqlite3.Error, OSError):
            raise HTTPException(503, "Interview storage is unavailable. Contact Bluqq support.") from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def metadata(row):
        return {
            "id": row["id"], "candidateName": row["candidate_name"],
            "interviewDate": row["interview_date"], "startTime": row["start_time"],
            "duration": row["duration"], "photoName": row["photo_name"],
            "resumeName": row["resume_name"], "createdAt": row["created"],
            "thumbnail": "data:image/jpeg;base64," + base64.b64encode(row["thumbnail"]).decode("ascii"),
        }

    def list(self, owner):
        with self.connection() as db:
            rows = db.execute("""SELECT id,candidate_name,interview_date,start_time,duration,
                photo_name,resume_name,created,thumbnail FROM interviews WHERE owner=?
                ORDER BY interview_date,start_time,created DESC""", (owner,)).fetchall()
            return {"interviews": [self.metadata(row) for row in rows], "limit": MAX_INTERVIEWS}

    def add(self, owner, record):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM interviews WHERE owner=?", (owner,)).fetchone()[0]
            if count >= MAX_INTERVIEWS:
                raise HTTPException(409, "Your interview list is full. Delete an old interview first.")
            total = db.execute("SELECT COALESCE(SUM(length(photo)+length(resume)+length(thumbnail)),0) FROM interviews").fetchone()[0]
            if total + len(record["photo"]) + len(record["resume"]) + len(record["thumbnail"]) > 512 * 1024 * 1024:
                raise HTTPException(409, "Interview storage is full. Contact Bluqq support.")
            interview_id = str(uuid.uuid4())
            db.execute("INSERT INTO interviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                interview_id, owner, record["candidateName"], record["interviewDate"],
                record["startTime"], record["duration"], record["photoName"], record["photoMime"],
                record["photo"], record["thumbnail"], record["resumeName"], record["resumeMime"],
                record["resume"], int(time.time()),
            ))
            row = db.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone()
            return self.metadata(row)

    def resume(self, owner, interview_id):
        with self.connection() as db:
            row = db.execute("SELECT resume,resume_mime,resume_name FROM interviews WHERE owner=? AND id=?", (owner, interview_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Interview not found.")
            return bytes(row["resume"]), row["resume_mime"], row["resume_name"]

    def delete(self, owner, interview_id):
        with self.connection() as db:
            if db.execute("DELETE FROM interviews WHERE owner=? AND id=?", (owner, interview_id)).rowcount == 0:
                raise HTTPException(404, "Interview not found.")


def register_interview_routes(app, access_keys):
    store = InterviewStore()
    requests = defaultdict(deque)
    save_lock = asyncio.Lock()

    def authenticate(request):
        authorization = request.headers.get("Authorization", "")
        supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not 32 <= len(supplied) <= 256:
            raise HTTPException(401, "Enter a valid personal access key.")
        candidate = hashlib.sha256(supplied.encode()).hexdigest()
        owner = next((user for user, digest in access_keys.items() if hmac.compare_digest(candidate, digest)), None)
        if owner is None:
            raise HTTPException(401, "Access key is invalid or revoked.")
        bucket = requests[owner]
        now = time.monotonic()
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= 90:
            raise HTTPException(429, "Too many interview requests. Try again in a minute.")
        bucket.append(now)
        return owner

    def check_id(interview_id):
        if not re.fullmatch(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}", interview_id):
            raise HTTPException(404, "Interview not found.")

    @app.get("/api/interviews")
    async def list_interviews(request: Request):
        owner = authenticate(request)
        return await run_in_threadpool(store.list, owner)

    @app.post("/api/interviews", status_code=201)
    async def save_interview(request: Request):
        owner = authenticate(request)
        if request.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "Send interview details as JSON.")
        body = bytearray()
        try:
            async with asyncio.timeout(45):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                        raise HTTPException(413, "The interview files are too large.")
                    body.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, "Interview upload timed out.") from None
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Interview details could not be read.") from None
        if not isinstance(payload, dict):
            raise HTTPException(400, "Interview details could not be read.")
        name = payload.get("candidateName")
        interview_date = payload.get("interviewDate")
        start_time = payload.get("startTime")
        duration = payload.get("duration")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(c) < 32 for c in name):
            raise HTTPException(400, "Enter a valid candidate name.")
        try:
            parsed_date = date.fromisoformat(interview_date)
        except (TypeError, ValueError):
            raise HTTPException(400, "Choose a valid interview date.") from None
        if parsed_date.year > date.today().year + 3:
            raise HTTPException(400, "Interview date is too far in the future.")
        if not isinstance(start_time, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
            raise HTTPException(400, "Choose a valid start time.")
        if type(duration) is not int or duration not in {15, 30, 45, 60, 90}:
            raise HTTPException(400, "Choose a valid interview duration.")
        photo_name, photo_mime = payload.get("photoName"), payload.get("photoMime")
        resume_name, resume_mime = payload.get("resumeName"), payload.get("resumeMime")
        for filename, label in ((photo_name, "photo"), (resume_name, "résumé")):
            if not isinstance(filename, str) or not 1 <= len(filename) <= 160 or any(ord(c) < 32 for c in filename):
                raise HTTPException(400, f"Use a valid {label} filename.")
        if photo_mime not in PHOTO_MIMES or resume_mime not in RESUME_MIMES:
            raise HTTPException(415, "Use a supported photo and résumé format.")
        photo = decode_file(payload.get("photoData"), MAX_PHOTO_BYTES, "Photo")
        resume = decode_file(payload.get("resumeData"), MAX_RESUME_BYTES, "Résumé")
        _, _, _, thumbnail = validate_image(photo, photo_mime)
        validate_resume(resume, resume_mime, resume_name)
        record = {
            "candidateName": name.strip(), "interviewDate": interview_date, "startTime": start_time,
            "duration": duration, "photoName": photo_name, "photoMime": photo_mime, "photo": photo,
            "thumbnail": thumbnail, "resumeName": resume_name, "resumeMime": resume_mime, "resume": resume,
        }
        if save_lock.locked():
            raise HTTPException(429, "Another interview is being saved. Try again shortly.")
        async with save_lock:
            return await run_in_threadpool(store.add, owner, record)

    @app.get("/api/interviews/{interview_id}/resume")
    async def download_resume(interview_id: str, request: Request):
        owner = authenticate(request)
        check_id(interview_id)
        data, mime, filename = await run_in_threadpool(store.resume, owner, interview_id)
        safe_name = filename.replace('"', "")
        return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{safe_name}"'})

    @app.delete("/api/interviews/{interview_id}", status_code=204)
    async def delete_interview(interview_id: str, request: Request):
        owner = authenticate(request)
        check_id(interview_id)
        await run_in_threadpool(store.delete, owner, interview_id)
        return Response(status_code=204)
