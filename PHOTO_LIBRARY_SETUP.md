# Website-to-extension photo library (extension 0.3.9)

## What changed

The website can explicitly save a reference photo in a private, persistent library. The extension fetches thumbnails for the personal key entered in its popup and downloads the chosen original when Start is pressed. The original image bytes are preserved; no AI processing, crop or retouch is applied during storage. Thumbnails are only for the gallery.

This is private per configured user ID in `VCAM_ACCESS_KEYS`, not a public gallery. Give each person their own key and user ID. Anyone who shares a key can view, upload and delete that user's library. No new admin role or signup was added: to upload on a person's behalf, use that person's authorized personal key on the website. Never use the permanent provider API key here.

## 1. Deploy the backend and website first

Push this project to the GitHub repository connected to your existing Railway service. Changes include `reference_library.py`, `server.py`, `requirements.txt`, `Dockerfile`, the website UI, `public/reference-library.js` and the privacy notice. Do not upload `.env`, the local database, real photos or personal keys to GitHub.

In Railway, attach a **Volume** to the same web service with mount path `/data`. Do not mount it at `/app` (that would hide application files). Configure these service variables:

| Variable | Value |
| --- | --- |
| `VCAM_DATA_DIR` | `/data/bluqq` |
| `RAILWAY_RUN_UID` | `0` |

Keep the existing `DECART_API_KEY` and `VCAM_ACCESS_KEYS` values. Do not regenerate keys unnecessarily. Redeploy after adding the volume and variables.

Railway mounts volumes as root and documents `RAILWAY_RUN_UID=0` for images using a non-root user, as this Dockerfile does. This override runs the container as root; it is a deployment-permissions tradeoff, not an extra extension permission. For a hardened deployment, provision volume ownership for UID 10001 and retain the non-root runtime instead. See [Railway volume setup and permissions](https://docs.railway.com/volumes).

Keep **one worker and one replica**, as in the existing configuration. The SQLite library lives at `/data/bluqq/references.sqlite3`; it must be on the volume, not the temporary container filesystem. Railway supplies `RAILWAY_VOLUME_MOUNT_PATH` automatically. In a Railway environment, the API rejects storage paths outside that mount. Without photo-storage configuration, existing video processing remains available and library requests report a setup error.

Library limits: 20 photos per user; 2 MiB per file; PNG, JPEG or WebP; at least 512 x 512, at most 16 megapixels; no animated images. Total stored image and thumbnail payload is capped at 256 MiB for this initial release. Requests are throttled. Duplicate original files for the same user return the existing entry rather than consuming another slot. Uploading a duplicate does not rename it.

## 2. Save a user's photo

1. Open your deployed website and enter that user's personal access key.
2. Choose their permitted photo in **Reference portrait and upper body**.
3. Scroll to **Your extension photo library** and optionally enter a photo name.
4. Check the separate storage-permission checkbox, then press **Save to extension**.
5. Wait for the saved thumbnail to appear. A local preview alone is not a cloud upload.

Camera permission, a live AI session and provider credits are not required to save or list photos. Saving does not consume an AI token or change a running transformation. The existing website preview remains based on the locally selected file.

## 3. Install and use extension 0.3.9

Use the new `Bluqq-Virtual-CAM-0.3.9.zip`, not the old ZIP with a 0.3.2 filename/0.3.8 manifest. The new ZIP contains `manifest.json` at its root.

For testing: extract it, open `chrome://extensions`, enable Developer mode and **Load unpacked** the extracted folder that contains `manifest.json`. Disable the previous test copy to avoid competing camera hooks, then open a fresh supported meeting tab. Updating a store-installed extension for all users requires submitting this new version through the store; local installation does not update other users.

1. Open a supported Google Meet, Zoom **Web**, or Teams **Web** meeting tab.
2. Open the Bluqq popup and enter the same personal access key used for the upload.
3. **Your saved photos** loads automatically. While the popup stays open, it checks every 20 seconds; you can also press **Refresh saved photos**.
4. Select a saved photo, give cloud-processing consent and press **Start & refresh meeting** before joining the call.
5. Enable the meeting camera and check the transformed preview. Tell participants it is AI-transformed.

A key entered only for browsing the library is not persisted by that action. The existing Start workflow stores it in extension session storage, so later popup openings can load automatically. No personal key is embedded in the ZIP. The public Railway base URL is the same as before. Photos are fetched through authenticated requests, never public URLs.

Newly saved photos appear without rebuilding or re-uploading the extension. Library refresh does not automatically select a new photo or switch an active session. Stop first to change a portrait. This does not make the extension work in native Zoom/Teams desktop apps.

## 4. Verify before public release

- Upload a permitted test photo using key A; open the extension with key A and confirm the thumbnail appears.
- Select it and test a real browser meeting with another consenting participant.
- Use a different user's key B: A's photo must not appear.
- Upload a second photo while the popup is open: it should appear within approximately 20 seconds without changing a live feed.
- Redeploy Railway and confirm both photos still exist.
- Delete one from the website, refresh the extension and confirm it disappears. Starting with a stale/deleted selection must fail, not fall back to another face.
- Verify `/privacy.html` is publicly available and reflects your actual retention and provider agreements before updating store privacy disclosures.

Offline validation: 30 Python backend tests, 20 existing camera-frontend tests and 14 new mocked website/extension sync tests passed; TypeScript and production build passed. This does **not** establish successful live camera/provider/meeting processing or completed Railway deployment.

## 5. Privacy, deletion and key rotation

Only use images supplied with permission to store and process them. Originals may contain EXIF metadata. The backend retains the original, thumbnail, name, timestamp and owning user ID. It does not add camera-recording storage.

The website's **Delete saved photo** removes the live library entry. It does not erase already downloaded extension working copies, active sessions, provider data, meeting recordings or earlier volume backups. Use **Clear saved settings** in the extension to clear its local copy and session key. Define and disclose your backup-retention policy; configure volume backups as appropriate. The privacy notice remains a review copy where provider and retention terms are unconfirmed.

When rotating a person's key, retain their existing user ID in `VCAM_ACCESS_KEYS` so their library stays with them. Removing a key prevents new library requests after deployment but does not delete stored photos or terminate existing sessions. Never reuse an old user's ID for a different person without removing the old library first. For a full account deletion, remove their photos while authorized, revoke their key, and address backups/provider records separately.

## Code map / future changes

- `reference_library.py`: validation, stable-user ownership, SQLite storage, authenticated API and limits.
- `server.py`: registers the library routes before the static website mount; existing token endpoint remains unchanged.
- `public/reference-library.js`: explicit upload/list/delete controls; no camera-state changes.
- `index.html` and `src/style.css`: library UI and permission checkbox.
- Extension `cloud-references.js`: worker-only authenticated gallery/original fetches.
- Extension `background.js`: only trusted popup messages can list photos; Start rechecks original access before enabling.
- Extension `popup.js` / `popup.html`: saved-photo gallery, refresh and stale-key guards.
- `inject.bundle.js`, `provider.bundle.js`, `bridge.js`, `presets.js`: unchanged from the supplied 0.3.8 engine. Compiled diagnostic version strings may still show 0.3.8; the package and popup are 0.3.9. Full original bundle source was not available, so no engine rebuild is claimed.

API: `GET /api/references`, `POST /api/references` (raw image; `Content-Type`, `X-Reference-Consent: true`, optional percent-encoded `X-Reference-Name`), `GET /api/references/{id}`, `DELETE /api/references/{id}`. All require `Authorization: Bearer <personal key>`, check ownership and return `Cache-Control: no-store`. Do not put keys in query strings or logs.

Local backend tests: `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`. Existing frontend checks: `npm test`, `npm run typecheck`, `npm run build`. Set `VCAM_DATA_DIR=./data` only for local storage; that folder is ignored by Git and Docker.
