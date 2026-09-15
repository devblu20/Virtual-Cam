# Website / Bluqq extension video-profile alignment

Updated 2026-09-15 against the supplied `Bluqq-Virtual-CAM-0.3.2.zip`, whose manifest identifies version **0.3.8**. This is video-request alignment, not a port of every meeting-extension feature.

## Aligned settings

- Fixed Lucy 2.1 using the pinned SDK 0.1.22, as in the ZIP.
- Identical built-in prompt from the ZIP's `presets.js`; applied both on connect and when changing the reference.
- Prompt enhancement off.
- Preferred capture size 1088 x 624 with ideal/max 25 fps. Actual camera settings remain visible; browsers can negotiate a different size.
- `mirror: false`, matching both extension provider paths.
- No explicit resolution override, matching the ZIP's provider-default output request. The old website model and resolution selectors are removed to prevent accidental differences.
- SDK telemetry disabled and quiet SDK logger. Integration metadata identifies the website honestly as `bluqq-website`. Existing token-request cancellation and late-stream version guards are retained; the website's unmodified SDK does not accept the ZIP's connection `signal` option.

The fixture `tests/fixtures/bluqq-0.3.8-video-profile.json` records the reviewed extension settings and preset-file hash. Tests verify exact prompt and request-setting parity without depending on files outside this repository.

## Intentionally retained website behavior

- Users can upload an image. No fixed person's appearance or portrait assets were added. To compare outputs, upload the exact same portrait file used by the extension (one of its `presets/*.jpg` files).
- Local image validation, selected/applied reference state, consent, personal-key authorization, network diagnostics, full-frame previews, AI disclosure and Stop behavior remain.
- No microphone capture or audio-delay processing. Those extension features address browser meeting audio/video timing, not the website's silent preview.
- The website displays the remote video directly. The extension uses an additional output canvas, browser meeting capture wrappers and Teams-specific provider routing.
- No vendor SDK worker/timing patches were copied from compiled extension bundles. They require the missing extension source/build project to port and validate safely. The website keeps its existing SDK connection/reconnection behavior.
- No backend authentication, Railway variables, access keys or deployment settings changed. No extension code changed.

## Known limit: identical settings are not identical frames

The camera scene, actual negotiated dimensions, reference file, network conditions, provider behavior and downstream meeting compression can still change the result. This update does not promise exact facial identity, better realism, equal latency or pixel-identical output.

The ZIP prompt is **792 characters**. Decart's guidance estimates roughly 750 English characters, with the exact limit depending on wording. It is retained verbatim for the requested alignment, not certified as accepted. No paid provider request was made during implementation. If it is rejected, shorten the prompt in BOTH source projects, update the fixture, and rebuild both clients; do not silently fall back to another prompt or start extra paid sessions.

Reference: https://docs.platform.decart.ai/models/realtime/streaming-best-practices#prompt-strategy

## Deploy and compare

1. Commit/push the website changes to the branch connected to Railway; wait for a successful deployment.
2. Reload the website. Confirm the profile reads `Bluqq extension 0.3.8 · Lucy 2.1` and that there are no model/resolution dropdowns.
3. Stop other camera/AI sessions. Select your physical camera, upload the same permitted portrait used in the extension, and consent before connecting.
4. Check actual input/output dimensions. Compare with the extension using the same lighting and framing, sequentially rather than running both paid sessions together.
5. Test a new reference with Apply reference, then Stop. Both streams should close. Confirm any error instead of assuming an active-looking preview establishes success.

## Local verification

Run TypeScript, frontend lifecycle simulations, production build and Python backend tests. These establish local behavior, not live AI quality or provider acceptance.

```powershell
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```
