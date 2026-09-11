# Reference-fidelity update — 2026-09-11

## What changed

- `src/main.ts` contains one built-in prompt targeting the reference's facial identity,
  hairline, hairstyle, beard/moustache, shoulders, upper-body build and clothing.
  It keeps live motion/background instead of inventing an office scene. Enhancement
  is disabled to keep these explicit instructions intact. There is no prompt input.
- HD detail now selects Lucy 2.5, whose installed SDK input definition is 1280×720.
  The character alternative is Lucy 2.1 at the installed SDK's 1088×624 input.
  Higher resolution does not establish better identity fidelity; compare both.
- Output requests are explicit: standard 720p or optional 1080p. 1080p requires
  model/account support and can affect bandwidth or service charges. No automatic
  retry or extra paid comparison session is started. If rejected, select 720p
  and reconnect. Inspect **Received**, not just **Requested**, dimensions.
- Camera capture first requests model-native dimensions, falling back to ideal
  dimensions only on an unsupported-constraint error. Low input resolution is
  reported. Switching model/camera requires starting the camera again.
- Reference files are decoded locally before cloud processing. Invalid images,
  files over 10 MB, and images below 512 pixels on either side are rejected.
  The original file is sent without application-side cropping, recompression or
  upscaling. Dimension checks do not detect blur, faces or actual identity quality.
- A full-image reference preview shows which image is selected. **Reference applied**
  appears only after a successful connection/update; selecting another file does
  not silently change the active session. Click **Apply reference** to send it.
- Camera/output previews use `object-fit: contain`, preserving hair and shoulders
  rather than cropping the video to fill its box. Connection-quality feedback
  distinguishes measured network/device problems from identity-matching problems.

## How to test after deploying

1. Push the source changes and let Railway build the latest commit. Hard-refresh
   the website, stop any previous session, and choose **HD detail / 720p** first.
2. Use an original, sharp, well-lit portrait with one front-facing person and
   permission to use it. Show all hair, beard, shoulders and the desired clothing.
   A chest-up image around 1280 pixels on its long side is a useful starting point;
   do not make the face tiny by using a distant full-body photograph.
3. Match chest-up framing in the camera. Use even front lighting and modest
   motion. Check **Camera** resolution; a 640×480 source is still low detail.
4. Connect and inspect **Reference applied**, received dimensions and connection
   quality. Assess face shape, hairline, beard silhouette and clothing separately,
   both while still and during speech/head movement.
5. Stop before changing models/resolution. Compare the character alternative using
   the same reference, lighting and pose. Try 1080p only if supported, and verify
   the actual received size. Never run simultaneous sessions just to compare.
6. Changing the reference while connected should display a pending message until
   **Apply reference** succeeds. Stop must release both live video streams.

## Limits and release scope

This improves the request and capture/display pipeline; it does not add a dedicated
face-swap model, face recognition, detail reconstruction or identity lock. An exact
copy of the reference, especially unseen hair/body regions, cannot be guaranteed.
No real provider generation was run during this change. Automated checks cannot
prove subjective likeness or temporal stability. A consented live comparison is
required before claiming improved generated output.

Only this website project is updated. The separate Chrome extension and its store
package do not automatically receive these changes. Existing consent/access-key
checks and the visible AI-generated label remain in place.

## Verification commands

Verified locally: TypeScript check, production build, 20 frontend simulations and
14 Python tests passed. The built page was opened in a browser and checked for
layout/default controls and console errors (none observed). No real camera or
provider session was used; this is not a measured improvement in generated likeness.

```powershell
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Sources checked for this implementation:

- [SDK realtime resolution and atomic state updates](https://docs.platform.decart.ai/sdks/javascript-realtime)
- [Reference images](https://docs.platform.decart.ai/models/realtime/reference-images)
- [Character prompting](https://docs.platform.decart.ai/models/realtime/lucy-2.5-prompting)
- [Capture and prompt guidance](https://docs.platform.decart.ai/models/realtime/streaming-best-practices)
