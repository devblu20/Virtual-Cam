# Required changes to your separate meeting extension

This project update does not edit meet-extension.zip or any other folder.
The old extension will NOT work unchanged: token requests now require a personal
access key, and localhost must be replaced after you get your Railway domain.

## API contract

- URL: https://YOUR-RAILWAY-DOMAIN/api/realtime-token
- Method: POST
- Header: Authorization: Bearer PERSONAL_ACCESS_KEY
- Body: none
- Success: HTTP 200, JSON {"apiKey":"temporary-decart-token"}
- Failures: 401 invalid credential; 429 quota; 503 configuration; 502/504 upstream.
- Error JSON: {"detail":"user-facing explanation"}
- API responses use Cache-Control: no-store.

Add a personal-key field to the extension popup. Keep the raw key in the
extension's isolated context (prefer session-only storage), never in MAIN-world
messages. Background requests use it in the Authorization header. Only the
temporary Decart token may be passed to the processing code. Never hardcode
the owner's key, user keys, or the master Decart key in the extension.

Change background.js SERVER to your HTTPS Railway URL, and replace the localhost
host permission in manifest.json with that exact HTTPS host. Fetch from the
service worker, not the meeting page. No wildcard CORS has been added: host
permissions allow the extension service-worker request; CORS is not authentication.

Do not treat an extension ID, Origin, or page message source string as proof of
user identity. Validate message shapes/sender URLs, restrict storage mutations,
require user activation, and rate-limit requests. MAIN-world scripts share the
meeting page's trust boundary; origin checks alone cannot isolate them from
other code on the same page.

## Additional public-release blockers in the supplied extension

- Stop falling back to the real camera on error or concurrent camera requests
  unless the user explicitly agrees.
- Track and clean up all raw, extra-input, output and remote media streams.
- Preserve the selected camera; stop processing on stop/leave.
- Add accurate processing consent, an AI disclosure, and privacy information.
- Remove localhost instructions; add icons; shorten manifest description to
  132 characters or less; use only meeting sites actually tested.
- Rebuild inject.bundle.js using npm ci and npm run build after source edits.
- Test on another laptop with the local Python server OFF.
- Package manifest.json at the ZIP root, not inside a parent directory.
- Submit separately to Chrome Web Store. Hosting this website does not publish
  the extension or guarantee approval/compatibility.

Official references:
[Extension network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests),
[prepare the store package](https://developer.chrome.com/docs/webstore/prepare),
[publish](https://developer.chrome.com/docs/webstore/publish).
