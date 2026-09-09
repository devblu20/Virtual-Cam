# Virtual CAM Cloud — Railway setup

This is the cloud edition of your existing VirtualCAMshare project. It serves a
camera-preview website and an authenticated Decart temporary-token API.
No OBS, virtual-camera driver, or Python installation is needed on a visitor's
computer for browser use. The website alone does NOT add a meeting/OS camera.
Your separate meeting extension must be updated; see EXTENSION_INTEGRATION.md.

## 1. Push this folder to GitHub

Create an empty repository on GitHub, then open PowerShell in this project:

```powershell
Set-Location 'D:\PROJECTS\Internship_Projects\VirtualCAMshare'
git init
git branch -M main
git add .
git status
```

Inspect the staged files. Never commit .env, personal access keys or Decart keys.
.gitignore excludes local dependencies, builds, ZIPs and .env files. The supplied
.env.example has no real credentials.

```powershell
git commit -m "Prepare Virtual CAM for Railway"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Replace YOUR_USERNAME and YOUR_REPOSITORY. Dockerfile and railway.json must be
at the repository root. Do not upload a ZIP instead of the extracted source.
If you already initialized Git, skip git init and reuse the existing remote.

## 2. Generate your first user's access key

Run locally (Python 3.13 is used by the server; this helper uses only the standard library):

```powershell
python scripts/create_access_key.py owner
```

It prints two things:

- A PRIVATE personal access key. Keep it safe and enter it in the website.
- A JSON object containing a SHA-256 digest. Use the whole JSON object as the
  VCAM_ACCESS_KEYS Railway variable.

The raw personal key is different from your Decart API key. Do not share the
Decart key. Do not put any raw key in GitHub, extension code, screenshots or CI logs.

## 3. Deploy on Railway

1. Create a Railway project and select deployment from your GitHub repository.
2. Choose this repository. Railway uses the included Dockerfile.
3. Under service Variables, add:
   - DECART_API_KEY: your real Decart server API key.
   - VCAM_ACCESS_KEYS: the JSON object printed by the helper above.
4. Apply the variables and deploy/redeploy. If automatic deployment ran before
   the variables were set, its health check will fail until these are configured.
5. Leave custom Build Command and Start Command empty; Dockerfile supplies both.
   Keep one replica and one worker. Do not add a volume or database for this pilot.
6. After deployment succeeds, go to Settings > Networking > Public Networking >
   Generate Domain. Use the HTTPS address.
7. Open /api/health on that domain. Expected: HTTP 200 and configured: true.
8. Open the website, enter your personal access key, choose a reference image,
   agree to processing, Start camera, then Connect and transform.
9. Stop when finished. Transformation uses paid Decart service.

PORT is supplied by Railway. Do not hardcode localhost or port 5173 in Railway.
The health check confirms local configuration/build presence, not Decart account
validity or available credit. Token requests must also succeed.

Official references:
[Railway configuration](https://docs.railway.com/config-as-code/reference),
[public networking](https://docs.railway.com/networking/public-networking).

## 4. Invite or revoke users

Run the helper again with a different non-personal ID, such as tester-02.
Merge its JSON entry into the existing VCAM_ACCESS_KEYS object; do not replace
the whole object and accidentally remove other users. Give each user only their
own PRIVATE raw key through a private channel.

To revoke access, remove the user's entry and redeploy/restart. Settings are read
at process startup. Revocation prevents NEW token requests; it does not terminate
an already-established Decart session. Rotate a leaked key by generating a new
key for that ID, replacing its digest and restarting.

## 5. Run locally (optional)

Python 3.13 and Node.js 24 are recommended to match Docker.
Do not recreate a virtual environment while it is active.

```powershell
npm ci
npm run typecheck
npm run build
npm test
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

If .env does not already exist, copy .env.example to .env, then edit .env to add
the same two settings used on Railway. Do not overwrite an existing .env.

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
notepad .env
.\.venv\Scripts\python.exe server.py
```

Open http://localhost:5173. No virtual-environment activation is needed.
Stop the server with Ctrl+C.

Tests use fake credentials and mock Decart; they do not spend credits:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Scope and important limits

- This is an invite-only cloud pilot, not an unrestricted paid SaaS.
- Request limits: 5 token attempts per user per minute, 50 per rolling 24 hours,
  and 30 globally per minute. Failed upstream attempts count too.
- Counters are in memory and reset on restart. Multiple replicas would weaken
  these limits. Use a shared durable quota store before horizontal scaling.
- Temporary tokens request a 120-second expiry. This is NOT a two-minute session
  limit or a guaranteed cost cap. Configure appropriate Decart-side spending
  controls and monitor use. Railway and Decart costs are separate.
- No self-service signup, payments, durable usage accounting, or admin dashboard.
- The website does not record video, capture microphone audio, or save your key
  or reference to browser storage. Video/image/prompt go directly to Decart.
  Railway receives token requests and may retain request metadata in platform logs.
- Publish an accurate privacy policy with owner/contact details and verified
  provider retention rules before public distribution. Do not claim Decart stores
  nothing unless you have verified its terms/configuration.
- Meetings need the separate updated extension. Native meeting apps are not
  supported by this cloud-only approach.

The original obs_control.py and vcam_control.py are preserved as unused reference
files. The cloud server does not import them or expose their endpoints; Docker
does not include them. They are not a working desktop edition with these new
requirements. A full pre-change backup was saved outside the repository.

## Troubleshooting

- Health check 503: set both variables; VCAM_ACCESS_KEYS must be valid JSON with
  unique 64-character lowercase SHA-256 digests and simple user IDs.
- 401: use the raw personal key, NOT the digest or Decart key.
- 429: wait for the returned Retry-After period; do not restart to bypass quotas.
- 502: check Decart key, account/model access, credits and outbound connectivity.
- 504: upstream timed out; retry later.
- Camera unavailable: use HTTPS or localhost, allow browser camera permission,
  and ensure the camera is connected.
- Extension still asks for localhost: it has not been updated. See the integration
  guide; deploying this repository does not update an installed extension.
