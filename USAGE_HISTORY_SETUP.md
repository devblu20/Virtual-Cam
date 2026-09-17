# Administrator-only usage history

## What is recorded

New instrumented sessions record the configured user ID, avatar ID/name, source
(website/extension), platform (website/Meet/Zoom Web/Teams Web), client version,
server request/start/end times, status and estimated activity seconds. Website
reference changes create separate avatar segments. Unsaved website references
use a SHA-256 image fingerprint and basename; existing library originals are
matched by digest to that user's saved photo. Deleting a photo does not remove
its past usage name. No new image copy is stored in usage tables.

There is no signup. A shared personal key identifies a shared account, not a
particular human. Never reuse an old user identifier for a different customer
without handling their existing data.

No video/audio recording, screenshots, meeting URL/title/participants, raw keys
or IP address is stored in the usage tables. Infrastructure logs are separate.

## Railway setup (manual)

1. Push this project to your existing GitHub repository and deploy the backend
   **before distributing extension 0.3.12**. `Dockerfile` now copies
   `usage_history.py`; the frontend build includes `public/admin.html` and its
   local scripts/styles.
2. Keep the existing Railway persistent volume mounted at `/data` and
   `VCAM_DATA_DIR=/data/bluqq`. Follow `PHOTO_LIBRARY_SETUP.md` for volume write
   permissions. Usage tables and photos share `/data/bluqq/references.sqlite3`.
   Do not change to an ephemeral directory. If no volume is available, video
   authorization continues but new clients display missing usage history.
3. Generate a **new, separate** administrator credential on your own terminal:

   ```powershell
   cd 'D:\PROJECTS\Internship_Projects\VirtualCAMshare'
   .\.venv\Scripts\python.exe scripts/create_admin_key.py
   ```

   This command prints a raw administrator key and its SHA-256 hash. Keep the
   raw key in your password manager, not GitHub, screenshots, chat or the
   extension. Put **only the hash** in Railway's `VCAM_ADMIN_KEY_SHA256` variable.
   Do not put this credential in `VCAM_ACCESS_KEYS`; the backend rejects a shared
   admin/personal hash. This helper does not edit `.env` or Railway for you.
4. Apply Railway variable changes / redeploy. Existing `DECART_API_KEY` and
   `VCAM_ACCESS_KEYS` keep their existing roles. No new dependency is required.
5. Open:
   `https://virtual-cam-production-9734.up.railway.app/admin.html`
   and enter the **raw administrator key**. The page keeps it in memory, clears
   the input after successful login, and clears credentials and rows on Lock or
   page exit. The static page can be opened by anyone, but records require the
   separately authenticated API. No admin credential is bundled in the page.
6. Filter by user/platform/date; click Apply / refresh for fresh results. Times
   render in the viewing browser's local timezone. Dates filter **request time**.
   Totals apply to all matching sessions, not just the current 50-row page.

## Install the new reporting client once

Use `Bluqq-Virtual-CAM-0.3.12.zip` from the delivered extension-releases folder.
For local testing extract and Load unpacked, then refresh the meeting tab.
For store distribution upload the new package and update privacy disclosures
for usage metadata before publishing. This task does not submit either store.

The video engine, fixed transformation prompt, provider SDK, permissions and
AI labels are unchanged. No remote JavaScript/Wasm is downloaded. The popup
still has no upload/manage-photos link. Older extensions still transform and
load photos, but **do not report usage**. Existing records cannot be backfilled.
Future compatible dashboard/API/photo-library changes do not require another
ZIP. New browser-side capabilities still require an extension release.

## Meaning of duration and status

- A token request creates a `connecting` attempt. Token failure becomes `failed`.
- The first website decoded frame or fresh extension timing measurement starts
  confirmed client-reported activity. Merely opening a camera preview does not.
- Reports arrive approximately every 20 seconds. Server receipt times determine
  elapsed time. Gaps over 45 seconds are excluded and marked as missing.
- Stop/close sends a best-effort final report; server receipt sets end time.
  Closing a browser abruptly may prevent that report.
- After 65 seconds without reports, an open row displays `interrupted` (had
  activity) or `unconfirmed` (never confirmed). No duration is extrapolated to
  the current time. Later reports can resume an unclosed session, excluding gaps.
- Avatar changes split the website's per-avatar time after successful Apply.
  In the extension, Stop / change avatar / Start creates another session.

All durations are **estimates**, not provider-billed minutes or verified meeting
attendance. Client messages can be manipulated, sessions on different devices
can overlap, reports can be lost, hidden-page throttling may reduce reporting,
and a transformed preview does not prove participants received it. Do not use
this as a billing or surveillance ledger. Extension sessions without fresh
video timing measurements remain unconfirmed even if the user sees output.

## Storage, retention and operations

No automatic deletion is enabled. The live database retains records until an
administrator removes them. Maximum 100,000 session rows; at capacity new usage
recording fails open and video continues. Each session supports up to 100 avatar
selections; further avatar reporting requires a new session. Set a retention policy,
monitor capacity/volume space, and handle deletion requests and backups. SQLite
is for this application's existing **one worker / one replica** deployment.
Do not scale replicas against separate local volumes; use a shared database
design first if scaling becomes necessary.

Back up the database with a SQLite-consistent backup or a quiesced service and
test restores. Removing a Railway volume loses these records; a persistent
volume alone is not a backup. Rotation of the admin hash does not delete data.
The application logs a generic warning when history storage fails; clients
also expose unavailable/interrupted reporting. It never logs credentials in
these warning messages.

Before public release review the updated privacy notice and store declarations
against actual deployment/provider practices. No compliance certification or
provider retention promise is made by these changes.

## Acceptance check

1. Personal key and no key must fail to load `/api/admin/usage`; admin key succeeds.
2. Run the website for about a minute, Apply another reference, then Stop. Check
   both avatars and nonzero approximate duration under that user ID.
3. Use 0.3.12 in a supported browser meeting, turn on transformed video, wait for
   measured timing and at least two usage reports, then Stop. Check its platform.
4. Abruptly close a test tab; after 65 seconds verify stale status/no growing time.
5. Restart/redeploy the backend with the same volume and verify records remain.

Tests use fake credentials, temporary databases and mocked video/network calls;
real meeting/provider operation and production volume persistence still need
the above manual checks. Deployment and live sessions are not performed by tests.
