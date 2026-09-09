# Verification — 2026-09-09

Passed locally on Windows with Python 3.13.9 and Node.js 22.14.0:

- Clean npm ci installation (the original lockfile's resolved versions were kept,
  and direct dependency ranges were replaced with those exact versions).
- npm run typecheck.
- npm run build: production frontend generated in dist/.
- npm test: 7 mocked browser-lifecycle tests passed.
- Python unittest discovery: 14 tests passed, including a real subprocess/HTTP
  startup check using PORT, authorization failures, configuration checks, revoked
  credentials, quota behavior, static serving, and sanitized upstream failures.
- pip check: no broken Python requirements.
- package.json/package-lock.json direct dependency consistency and railway.json
  JSON syntax checked.

The browser tests simulate DOM/media/SDK behavior. They are NOT real browser,
webcam or Google Meet tests. Backend token tests mock Decart and use fake keys.
The HTTP smoke test only requests health, static HTML and an unauthenticated
token endpoint; it does not issue any paid Decart requests.

Known non-blocking warnings:

- The bundled LiveKit dependency produces Vite's >500 kB chunk warning.
- The installed Starlette TestClient warns that its httpx compatibility path is
  deprecated; the pinned test client still passes all tests.

Not verified here:

- Docker image build/Linux execution (Docker is not installed on this computer).
- Live Railway deployment, HTTPS domain, or Railway account configuration.
- Real Decart credentials, available credit, model access or video processing.
- Actual browser camera permission, long-session cleanup, reconnect behavior,
  meeting compatibility, or the separately maintained extension.

Before inviting users, deploy with real server variables and complete a real
camera/Decart test. Test the updated extension on another laptop with the local
Python server off. The extension still needs the integration and release fixes
listed in EXTENSION_INTEGRATION.md.
