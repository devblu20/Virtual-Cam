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
from xml.sax.saxutils import escape

from fastapi import HTTPException, Request
from fastapi.responses import Response
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token as google_id_token
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

try:
    from asyncio import timeout as async_timeout
except ImportError:  # Python 3.10 compatibility
    from async_timeout import timeout as async_timeout

from reference_library import MAX_BYTES as MAX_REFERENCE_BYTES, validate_image

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_RESUME_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = 28 * 1024 * 1024
MAX_INTERVIEWS = 50
INTERVIEW_STATUSES = {"Scheduled", "Completed", "Selected", "Rejected", "Cancelled"}
INTERVIEW_ADMIN_CREDENTIALS = {
    "harshitakothari2703@gmail.com": "2d24d533562b532e162640dde496257e54aee68440f0bb26aee7b871273497bd",
    "devblu.work@gmail.com": "bd03f224ac6c5069831826865e773836d2a5023bec835663cc31eb72e6997f25",
}
INTERVIEW_OWNER_EMAILS = frozenset(INTERVIEW_ADMIN_CREDENTIALS)
PHOTO_MIMES = {"image/jpeg", "image/png", "image/webp"}
RESUME_MIMES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def face_thumbnail(thumbnail):
    """Turn the stored full portrait thumbnail into a compact upper-face preview."""
    try:
        with Image.open(io.BytesIO(thumbnail)) as image:
            image = image.convert("RGB")
            width, height = image.size
            side = min(width, max(1, round(height * 0.56)))
            left = max(0, (width - side) // 2)
            top = max(0, min(height - side, round(height * 0.035)))
            image = image.crop((left, top, left + side, top + side)).resize((160, 160), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=82, optimize=True)
            return output.getvalue()
    except (OSError, ValueError):
        return thumbnail


def excel_column(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def make_excel(rows):
    headers = ["Candidate Name", "Status", "Interview Date", "Start Time", "Duration (minutes)",
               "Rating", "Interview Notes", "Photo Filename", "Resume Filename", "Saved At"]
    values = [headers] + [[
        row["candidate_name"], row["status"], row["interview_date"], row["start_time"], row["duration"],
        row["rating"], row["notes"], row["photo_name"], row["resume_name"],
        time.strftime("%Y-%m-%d %H:%M", time.localtime(row["created"])),
    ] for row in rows]
    sheet_rows = []
    for row_number, row in enumerate(values, 1):
        cells = []
        for column_number, value in enumerate(row, 1):
            reference = f"{excel_column(column_number)}{row_number}"
            if isinstance(value, int):
                cells.append(f'<c r="{reference}" s="{1 if row_number == 1 else 0}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{reference}" t="inlineStr" s="{1 if row_number == 1 else 0}"><is><t>{escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols>
<col min="1" max="2" width="22" customWidth="1"/><col min="3" max="6" width="16" customWidth="1"/>
<col min="7" max="7" width="42" customWidth="1"/><col min="8" max="10" width="28" customWidth="1"/>
</cols><sheetData>{''.join(sheet_rows)}</sheetData><autoFilter ref="A1:J{max(1, len(values))}"/></worksheet>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''')
        archive.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        archive.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Interviews" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        archive.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        archive.writestr("xl/styles.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Aptos"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Aptos"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF2563EB"/><bgColor indexed="64"/></patternFill></fill><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>''')
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


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
            existing_columns = {row[1] for row in db.execute("PRAGMA table_info(interviews)")}
            for column, definition in (
                ("status", "TEXT NOT NULL DEFAULT 'Scheduled'"),
                ("notes", "TEXT NOT NULL DEFAULT ''"),
                ("rating", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in existing_columns:
                    db.execute(f"ALTER TABLE interviews ADD COLUMN {column} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS interviews_owner ON interviews(owner)")
            db.execute("""CREATE TABLE IF NOT EXISTS interview_access (
                email TEXT PRIMARY KEY, workspace INTEGER NOT NULL, admin INTEGER NOT NULL,
                owner INTEGER NOT NULL, active INTEGER NOT NULL, created INTEGER NOT NULL,
                created_by TEXT NOT NULL, modified INTEGER NOT NULL
            )""")
            now = int(time.time())
            for email in INTERVIEW_OWNER_EMAILS:
                db.execute("""INSERT OR IGNORE INTO interview_access
                    (email,workspace,admin,owner,active,created,created_by,modified)
                    VALUES (?,?,?,?,?,?,?,?)""", (email, 1, 1, 1, 1, now, "system", now))
                db.execute("""UPDATE interview_access SET workspace=1,admin=1,owner=1,active=1
                    WHERE email=?""", (email,))
            db.commit()
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
            "status": row["status"], "notes": row["notes"], "rating": row["rating"],
            "thumbnail": "data:image/jpeg;base64," + base64.b64encode(face_thumbnail(row["thumbnail"])).decode("ascii"),
        }

    def list(self, owner):
        with self.connection() as db:
            rows = db.execute("""SELECT id,candidate_name,interview_date,start_time,duration,
                photo_name,resume_name,created,thumbnail,status,notes,rating FROM interviews WHERE owner=?
                ORDER BY interview_date,start_time,created DESC""", (owner,)).fetchall()
            return {"interviews": [self.metadata(row) for row in rows], "limit": MAX_INTERVIEWS}

    def excel(self, owner):
        with self.connection() as db:
            rows = db.execute("""SELECT candidate_name,interview_date,start_time,duration,
                photo_name,resume_name,created,status,notes,rating FROM interviews WHERE owner=?
                ORDER BY interview_date,start_time,created DESC""", (owner,)).fetchall()
            return make_excel(rows)

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
            db.execute("""INSERT INTO interviews (
                id,owner,candidate_name,interview_date,start_time,duration,photo_name,photo_mime,
                photo,thumbnail,resume_name,resume_mime,resume,created,status,notes,rating
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                interview_id, owner, record["candidateName"], record["interviewDate"],
                record["startTime"], record["duration"], record["photoName"], record["photoMime"],
                record["photo"], record["thumbnail"], record["resumeName"], record["resumeMime"],
                record["resume"], int(time.time()), record["status"], record["notes"], record["rating"],
            ))
            row = db.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone()
            return self.metadata(row)

    def resume(self, owner, interview_id):
        with self.connection() as db:
            row = db.execute("SELECT resume,resume_mime,resume_name FROM interviews WHERE owner=? AND id=?", (owner, interview_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Interview not found.")
            return bytes(row["resume"]), row["resume_mime"], row["resume_name"]

    def photo(self, owner, interview_id):
        with self.connection() as db:
            row = db.execute("SELECT photo,photo_mime,candidate_name FROM interviews WHERE owner=? AND id=?", (owner, interview_id)).fetchone()
            if row is None:
                raise HTTPException(404, "Interview not found.")
            return bytes(row["photo"]), row["photo_mime"], row["candidate_name"]

    def update(self, owner, interview_id, record):
        with self.connection() as db:
            current = db.execute("SELECT * FROM interviews WHERE owner=? AND id=?", (owner, interview_id)).fetchone()
            if current is None:
                raise HTTPException(404, "Interview not found.")
            values = {
                "candidate_name": record["candidateName"], "interview_date": record["interviewDate"],
                "start_time": record["startTime"], "duration": record["duration"],
                "status": record["status"], "notes": record["notes"], "rating": record["rating"],
                "photo_name": record.get("photoName", current["photo_name"]),
                "photo_mime": record.get("photoMime", current["photo_mime"]),
                "photo": record.get("photo", current["photo"]),
                "thumbnail": record.get("thumbnail", current["thumbnail"]),
                "resume_name": record.get("resumeName", current["resume_name"]),
                "resume_mime": record.get("resumeMime", current["resume_mime"]),
                "resume": record.get("resume", current["resume"]),
            }
            db.execute("""UPDATE interviews SET candidate_name=:candidate_name, interview_date=:interview_date,
                start_time=:start_time, duration=:duration, photo_name=:photo_name, photo_mime=:photo_mime,
                photo=:photo, thumbnail=:thumbnail, resume_name=:resume_name, resume_mime=:resume_mime,
                resume=:resume, status=:status, notes=:notes, rating=:rating
                WHERE owner=:owner AND id=:id""", {**values, "owner": owner, "id": interview_id})
            row = db.execute("SELECT * FROM interviews WHERE id=?", (interview_id,)).fetchone()
            return self.metadata(row)

    def update_photo(self, owner, interview_id, photo_name, photo_mime, photo, thumbnail):
        with self.connection() as db:
            if db.execute("""UPDATE interviews SET photo_name=?,photo_mime=?,photo=?,thumbnail=?
                WHERE owner=? AND id=?""", (photo_name, photo_mime, photo, thumbnail, owner, interview_id)).rowcount == 0:
                raise HTTPException(404, "Interview not found.")
            row = db.execute("SELECT * FROM interviews WHERE owner=? AND id=?", (owner, interview_id)).fetchone()
            return self.metadata(row)

    def delete(self, owner, interview_id):
        with self.connection() as db:
            if db.execute("DELETE FROM interviews WHERE owner=? AND id=?", (owner, interview_id)).rowcount == 0:
                raise HTTPException(404, "Interview not found.")

    @staticmethod
    def access_metadata(row):
        role = "both" if row["workspace"] and row["admin"] else "admin" if row["admin"] else "workspace"
        return {"email": row["email"], "role": role, "owner": bool(row["owner"]),
                "active": bool(row["active"]), "createdAt": row["created"]}

    def access_allowed(self, email, permission):
        column = "admin" if permission == "admin" else "workspace"
        with self.connection() as db:
            row = db.execute(f"SELECT active,{column} FROM interview_access WHERE email=?", (email,)).fetchone()
            return bool(row and row["active"] and row[column])

    def access_owner(self, email):
        with self.connection() as db:
            row = db.execute("SELECT active,owner FROM interview_access WHERE email=?", (email,)).fetchone()
            return bool(row and row["active"] and row["owner"])

    def list_access(self):
        with self.connection() as db:
            rows = db.execute("""SELECT email,workspace,admin,owner,active,created
                FROM interview_access ORDER BY owner DESC,active DESC,email""").fetchall()
            return {"access": [self.access_metadata(row) for row in rows]}

    def save_access(self, email, role, administrator):
        workspace = int(role in {"workspace", "both"})
        admin = int(role in {"admin", "both"})
        now = int(time.time())
        with self.connection() as db:
            db.execute("""INSERT INTO interview_access
                (email,workspace,admin,owner,active,created,created_by,modified)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(email) DO UPDATE SET workspace=excluded.workspace,admin=excluded.admin,
                active=1,modified=excluded.modified""",
                (email, workspace, admin, 0, 1, now, administrator, now))
            row = db.execute("""SELECT email,workspace,admin,owner,active,created
                FROM interview_access WHERE email=?""", (email,)).fetchone()
            return self.access_metadata(row)

    def revoke_access(self, email):
        if email in INTERVIEW_OWNER_EMAILS:
            raise HTTPException(409, "Owner access cannot be revoked.")
        with self.connection() as db:
            if db.execute("UPDATE interview_access SET active=0,modified=? WHERE email=?", (int(time.time()), email)).rowcount == 0:
                raise HTTPException(404, "Access record not found.")

def register_interview_routes(app, access_keys, image_editor=None):
    store = InterviewStore()
    requests = defaultdict(deque)
    save_lock = asyncio.Lock()

    def google_email(request):
        authorization = request.headers.get("Authorization", "")
        token = authorization[7:] if authorization.startswith("Google ") else ""
        if not token:
            return None
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        if not client_id:
            raise HTTPException(503, "Google sign-in is not configured yet.")
        if len(token) > 8192:
            raise HTTPException(401, "Google sign-in could not be verified.")
        try:
            claims = google_id_token.verify_oauth2_token(token, GoogleRequest(), client_id)
        except (ValueError, OSError):
            raise HTTPException(401, "Google sign-in expired or could not be verified.") from None
        email = claims.get("email", "").strip().lower()
        if claims.get("email_verified") is not True or not email:
            raise HTTPException(403, "This Google account could not be verified.")
        return email

    def portal_owner():
        expected_id = os.getenv("INTERVIEW_PORTAL_ID", "").strip()
        if not expected_id:
            raise HTTPException(503, "Interview login is not configured yet.")
        return "portal-" + hashlib.sha256(expected_id.encode()).hexdigest()[:24]

    @app.get("/api/interview-admin/google-config")
    async def interview_google_config():
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        return {"clientId": client_id if len(client_id) <= 256 else ""}

    def authenticate_interview_admin(request):
        email = google_email(request)
        if email:
            if not store.access_allowed(email, "admin"):
                raise HTTPException(403, "This Google account does not have Admin Panel access.")
            return email, portal_owner()
        authorization = request.headers.get("Authorization", "")
        try:
            decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8") if authorization.startswith("Basic ") else ""
            email, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(401, "Enter an approved administrator email and password.") from None
        email = email.strip().lower()
        expected_id = os.getenv("INTERVIEW_PORTAL_ID", "").strip()
        if not expected_id:
            raise HTTPException(503, "Interview Admin login is not configured yet.")
        candidate_hash = hashlib.sha256(password.encode()).hexdigest()
        expected_hash = INTERVIEW_ADMIN_CREDENTIALS.get(email, "")
        if not expected_hash or not hmac.compare_digest(candidate_hash, expected_hash):
            raise HTTPException(401, "Email or password is incorrect.")
        return email, portal_owner()

    def authenticate(request):
        email = google_email(request)
        if email:
            if not store.access_allowed(email, "workspace"):
                raise HTTPException(403, "This Google account does not have Private Workspace access.")
            owner = portal_owner()
            bucket = requests[owner]
            now = time.monotonic()
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) >= 90:
                raise HTTPException(429, "Too many interview requests. Try again in a minute.")
            bucket.append(now)
            return owner
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
                login_name, password = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(401, "Enter a valid Interview Ledger ID and password.") from None
            expected_id = os.getenv("INTERVIEW_PORTAL_ID", "").strip()
            expected_hash = os.getenv("INTERVIEW_PORTAL_PASSWORD_SHA256", "").strip().lower()
            if not expected_id or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
                raise HTTPException(503, "Interview Ledger login is not configured yet.")
            candidate_hash = hashlib.sha256(password.encode()).hexdigest()
            admin_hash = INTERVIEW_ADMIN_CREDENTIALS.get(login_name.strip().lower(), "")
            portal_login = hmac.compare_digest(login_name, expected_id) and hmac.compare_digest(candidate_hash, expected_hash)
            admin_login = bool(admin_hash) and hmac.compare_digest(candidate_hash, admin_hash)
            if not (portal_login or admin_login):
                raise HTTPException(401, "Email, workspace ID or password is incorrect.")
            owner = "portal-" + hashlib.sha256(expected_id.encode()).hexdigest()[:24]
            bucket = requests[owner]
            now = time.monotonic()
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) >= 90:
                raise HTTPException(429, "Too many interview requests. Try again in a minute.")
            bucket.append(now)
            return owner
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

    def profile_fields(payload):
        status = payload.get("status", "Scheduled")
        notes = payload.get("notes", "")
        rating = payload.get("rating", 0)
        if status not in INTERVIEW_STATUSES:
            raise HTTPException(400, "Choose a valid interview status.")
        if not isinstance(notes, str) or len(notes) > 2000 or any(ord(char) < 32 and char not in "\n\r\t" for char in notes):
            raise HTTPException(400, "Interview notes must be 2,000 characters or fewer.")
        if type(rating) is not int or not 0 <= rating <= 5:
            raise HTTPException(400, "Choose a rating from 1 to 5, or leave it unrated.")
        return status, notes.strip(), rating

    def reference_ready_photo(data, content_type):
        if len(data) <= MAX_REFERENCE_BYTES:
            return data, content_type
        try:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                for max_side, quality in ((2600, 88), (2200, 82), (1800, 76), (1500, 70), (1280, 66)):
                    prepared = image.copy()
                    prepared.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                    output = io.BytesIO()
                    prepared.save(output, format="JPEG", quality=quality, optimize=True)
                    if output.tell() <= MAX_REFERENCE_BYTES:
                        return output.getvalue(), "image/jpeg"
        except (OSError, ValueError):
            raise HTTPException(400, "Candidate photo could not be prepared for the user's library.") from None
        raise HTTPException(413, "Candidate photo is too large for the user's library.")

    @app.get("/api/interview-admin")
    async def interview_admin_overview(request: Request):
        administrator, owner = authenticate_interview_admin(request)
        data = await run_in_threadpool(store.list, owner)
        return {**data, "administrator": administrator}

    @app.get("/api/interview-admin/users")
    async def interview_admin_users(request: Request):
        authenticate_interview_admin(request)
        listing = await run_in_threadpool(access_keys.listing)
        users = [
            {"id": user["id"], "name": user["name"]}
            for user in listing["users"] if user.get("active")
        ]
        return {"users": users}

    def authenticate_access_owner(request):
        administrator, _ = authenticate_interview_admin(request)
        if not store.access_owner(administrator):
            raise HTTPException(403, "Only an owner can manage email access.")
        return administrator

    @app.get("/api/interview-admin/access")
    async def interview_admin_access(request: Request):
        authenticate_access_owner(request)
        return await run_in_threadpool(store.list_access)

    @app.post("/api/interview-admin/access", status_code=201)
    async def save_interview_admin_access(request: Request):
        administrator = authenticate_access_owner(request)
        if request.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "Send access details as JSON.")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Access details could not be read.") from None
        email = payload.get("email", "").strip().lower() if isinstance(payload, dict) else ""
        role = payload.get("role", "") if isinstance(payload, dict) else ""
        if len(email) > 254 or not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,189}", email):
            raise HTTPException(400, "Enter a valid email address.")
        if role not in {"workspace", "admin", "both"}:
            raise HTTPException(400, "Choose Private Workspace, Admin Panel or Both.")
        return await run_in_threadpool(store.save_access, email, role, administrator)

    @app.delete("/api/interview-admin/access/{email}", status_code=204)
    async def revoke_interview_admin_access(email: str, request: Request):
        authenticate_access_owner(request)
        normalized = email.strip().lower()
        if len(normalized) > 254 or not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,189}", normalized):
            raise HTTPException(404, "Access record not found.")
        await run_in_threadpool(store.revoke_access, normalized)

    @app.post("/api/interview-admin/interviews/{interview_id}/photo", status_code=201)
    async def assign_interview_photo(interview_id: str, request: Request):
        _, interview_owner = authenticate_interview_admin(request)
        check_id(interview_id)
        if request.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "Send the selected user as JSON.")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Selected user could not be read.") from None
        target_owner = payload.get("owner") if isinstance(payload, dict) else None
        if not isinstance(target_owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", target_owner):
            raise HTTPException(400, "Choose a valid user.")
        target_user = await run_in_threadpool(access_keys.user, target_owner)
        if not target_user.get("active"):
            raise HTTPException(409, "This user's access is disabled.")
        photo, content_type, candidate_name = await run_in_threadpool(store.photo, interview_owner, interview_id)
        photo, content_type = await run_in_threadpool(reference_ready_photo, photo, content_type)
        saved = await run_in_threadpool(access_keys.add, target_owner, candidate_name, photo, content_type)
        return {"photo": saved, "user": {"id": target_owner, "name": target_user["name"]}}

    @app.get("/api/interview-admin/interviews/{interview_id}/photo")
    async def interview_admin_photo(interview_id: str, request: Request):
        _, interview_owner = authenticate_interview_admin(request)
        check_id(interview_id)
        photo, content_type, _ = await run_in_threadpool(store.photo, interview_owner, interview_id)
        return Response(photo, media_type=content_type)

    @app.post("/api/interview-admin/interviews/{interview_id}/gemini-edit")
    async def interview_admin_gemini_edit(interview_id: str, request: Request):
        administrator, interview_owner = authenticate_interview_admin(request)
        check_id(interview_id)
        if image_editor is None:
            raise HTTPException(503, "Gemini image editing is not configured yet.")
        photo, content_type, _ = await run_in_threadpool(store.photo, interview_owner, interview_id)
        return await image_editor(
            request,
            f"interview-admin:{administrator}:{interview_id}",
            False,
            photo,
            content_type,
        )

    @app.put("/api/interview-admin/interviews/{interview_id}/photo")
    async def save_interview_admin_photo(interview_id: str, request: Request):
        _, interview_owner = authenticate_interview_admin(request)
        check_id(interview_id)
        content_type = request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in PHOTO_MIMES:
            raise HTTPException(415, "Choose a JPEG, PNG or WebP photo.")
        content_length = request.headers.get("Content-Length", "")
        if content_length.isdigit() and int(content_length) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "Edited photo must be 10 MB or smaller.")
        data = await request.body()
        if not data or len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(413, "Edited photo must be between 1 byte and 10 MB.")
        _, _, _, thumbnail = validate_image(data, content_type)
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
        _, _, candidate_name = await run_in_threadpool(store.photo, interview_owner, interview_id)
        filename = f"{candidate_name[:120]} - Gemini.{extension}"
        return await run_in_threadpool(
            store.update_photo, interview_owner, interview_id, filename, content_type, data, thumbnail
        )

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
            async with async_timeout(45):
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
        status, notes, rating = profile_fields(payload)
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
        if type(duration) is not int or not 1 <= duration <= 720:
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
            "status": status, "notes": notes, "rating": rating,
        }
        if save_lock.locked():
            raise HTTPException(429, "Another interview is being saved. Try again shortly.")
        async with save_lock:
            return await run_in_threadpool(store.add, owner, record)

    @app.get("/api/interviews/export.xlsx")
    async def export_interviews(request: Request):
        owner = authenticate(request)
        workbook = await run_in_threadpool(store.excel, owner)
        return Response(workbook,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="bluqq-interviews.xlsx"'})

    @app.patch("/api/interviews/{interview_id}")
    async def update_interview(interview_id: str, request: Request):
        owner = authenticate(request)
        check_id(interview_id)
        if request.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "Send interview details as JSON.")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, "Interview details could not be read.") from None
        name, interview_date = payload.get("candidateName"), payload.get("interviewDate")
        start_time, duration = payload.get("startTime"), payload.get("duration")
        status, notes, rating = profile_fields(payload)
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
        if type(duration) is not int or not 1 <= duration <= 720:
            raise HTTPException(400, "Choose a valid interview duration.")
        record = {"candidateName": name.strip(), "interviewDate": interview_date, "startTime": start_time,
                  "duration": duration, "status": status, "notes": notes, "rating": rating}
        if payload.get("photoData") is not None:
            photo_name, photo_mime = payload.get("photoName"), payload.get("photoMime")
            if not isinstance(photo_name, str) or not 1 <= len(photo_name) <= 160 or photo_mime not in PHOTO_MIMES:
                raise HTTPException(400, "Use a valid photo.")
            photo = decode_file(payload.get("photoData"), MAX_PHOTO_BYTES, "Photo")
            _, _, _, thumbnail = validate_image(photo, photo_mime)
            record.update(photoName=photo_name, photoMime=photo_mime, photo=photo, thumbnail=thumbnail)
        if payload.get("resumeData") is not None:
            resume_name, resume_mime = payload.get("resumeName"), payload.get("resumeMime")
            if not isinstance(resume_name, str) or not 1 <= len(resume_name) <= 160 or resume_mime not in RESUME_MIMES:
                raise HTTPException(400, "Use a valid résumé.")
            resume = decode_file(payload.get("resumeData"), MAX_RESUME_BYTES, "Résumé")
            validate_resume(resume, resume_mime, resume_name)
            record.update(resumeName=resume_name, resumeMime=resume_mime, resume=resume)
        async with save_lock:
            return await run_in_threadpool(store.update, owner, interview_id, record)

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
