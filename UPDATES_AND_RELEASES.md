# What updates without a new extension ZIP?

The website and the extension are separate applications. They share the authenticated photo-library API, not their JavaScript bundles.

| Change | How to publish it | New extension ZIP? |
| --- | --- | --- |
| Add or delete saved reference photos | Use the website's photo library | No |
| Website layout, wording, colors or forms | Push this repository; deploy on Railway | No |
| Backend access records, quotas or storage implementation | Update Railway/configuration, keeping the API compatible | No |
| Extension popup layout, links or buttons | Build and publish a new extension version | Yes |
| Extension camera hooks, meeting support, bundled SDK or permissions | Build, test and publish a new extension version | Yes |
| Transformation prompt for extension 0.3.16+ | Edit `transformation_prompt.txt`, push and deploy Railway; reconnect | No |

The popup loads saved photos when opened with a session key, or after a valid-length key is entered. It polls every 20 seconds while open and visible. A Refresh button is also available. New photos do not alter the active meeting or silently choose a face. The user selects a reference before starting.

The website and extension 0.3.16+ share the server-managed prompt: edit `transformation_prompt.txt`, deploy Railway, then stop and reconnect to use it. Older extensions retain their bundled prompt until upgraded once. Other website processing-code changes do not automatically update extension code. Compatible server changes can ship independently; breaking endpoint changes or new browser capabilities may need a coordinated extension update.

There is no compliant promise that every future code change can bypass extension releases. Chrome Manifest V3 requires extension logic to be bundled. Remote images and data are allowed, but downloading and executing updated JavaScript/WASM from Railway is not a substitute for store review. See [Chrome's Manifest V3 requirements](https://developer.chrome.com/docs/webstore/program-policies/mv3-requirements).

## This update: extension 0.3.11

- Removed the popup's **Upload / manage photos on the website** link. The private gallery, key entry, Refresh, consent, Start/Stop and privacy notice remain.
- Kept the camera engine, photo-library worker, prompt, permissions and automatic gallery refresh unchanged.
- Refreshed the website with Bluqq branding, a clearer preview/setup section, separate save/gallery columns, styled file/name inputs, empty states, photo counts, and visible loading/success/error messages.
- Did not add remote executable code, remote UI injection, new analytics, or changes to access permissions.

Install/publish the new extension once to remove the link. After that, website-only edits and photo changes do not require another extension package. Continue to manage uploads on the website directly; hiding a link is not a new admin-only authorization system.

The website source changes are in `index.html`, `src/style.css` and `public/reference-library.js`. Push them to the existing GitHub/Railway deployment. The persistent volume configuration described in `PHOTO_LIBRARY_SETUP.md` is still required; a UI update does not provision Railway storage.

Validation: 30 backend tests, 20 existing camera-frontend tests, 16 offline UI/extension checks, TypeScript and a production build passed. The actual local website was also rendered at 1440px and 390px viewports with no horizontal overflow; the photo-library layouts were visually inspected. Photo-sync checks live beside the local extension releases in `tests/photo-sync.test.mjs`; set `EXTENSION_DIR` to the 0.3.11 folder and `EXTENSION_VERSION=0.3.11` when running that script. No production deployment, extension-store submission or live meeting is performed by this local release.
