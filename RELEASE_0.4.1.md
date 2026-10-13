# Bluqq 0.4.1 — managed users and prompts

This backend supports both the existing 0.3.21 extension and the new 0.4.1 extension. Deployment through GitHub main was verified on Railway on 14 October 2026: health and the managed-user page returned HTTP 200; unauthenticated configuration and admin requests were rejected. Chrome Web Store submission has not been performed.

The latest production website and Interview Ledger changes were merged before deployment. The pre-release production v1 prompt is preserved. No production environment variables, credentials or user assignments were changed. An independent volume backup and authenticated production session tests were not available without administrator access; public endpoint checks do not establish those results.

## What changes

- `/users.html` is the administrator-only user and prompt editor. Sign in with the existing separate administrator key.
- Create a personal key at any time. The key identifies a permanent user ID; entered names never choose a prompt or change ownership.
- Set an optional custom prompt and room note for each user. Otherwise the managed default applies. The starting default is the existing prompt file.
- Change prompts without an extension release or Railway redeployment. Changes apply on the next connection, with a popup notice for active 0.4.1 users.
- Rotate keys or disable users. Rotation retains the user ID, saved portraits and history. Old keys cannot authenticate new requests.
- Usage history includes the administrator-assigned name and prompt revision at session start. Existing usage metrics remain estimates, not provider billing or recordings.
- Room profiles are manually selected by the administrator. This release does not analyze room video or include the separate lighting-preprocessing prototype. Prompt changes do not guarantee removal of generated lighting artifacts.

## Compatibility contracts

| Client | Backend behavior |
| --- | --- |
| Original 0.3.21 | Original token response and extension prompt. Managed prompt edits have no effect. Existing environment keys continue working unless explicitly disabled or rotated. |
| Processing-config v1 clients | Continue using `transformation_prompt.txt`, unchanged by managed prompt edits. |
| 0.4.1 / config v2 | Authenticated server prompt wins. No extension fallback if config is missing or invalid. |

Keep 0.3.21 and unpacked 0.4.1 in separate browser profiles for testing. Do not enable both on the same meeting tab. Provider concurrency and existing request quotas still apply.

## Deployment order

1. Preserve the existing `DECART_API_KEY`, `VCAM_ACCESS_KEYS`, and separate `VCAM_ADMIN_KEY_SHA256`. Do not delete or replace working user records as part of rollout.
2. Confirm `VCAM_DATA_DIR` points inside the existing persistent Railway volume, writable by container UID 10001. On Railway, `RAILWAY_VOLUME_MOUNT_PATH` must identify that volume. Do not use ephemeral storage for user keys.
3. Take a consistent SQLite backup of `references.sqlite3` using SQLite's backup API or the managed volume backup workflow. This release adds `managed_users`, `managed_defaults`, and `usage_profiles` tables; it does not drop existing photos or usage tables.
4. Deploy this complete backend with its Dockerfile. Keep one replica/worker: current quotas are process-local. A deployment can briefly interrupt API requests; this is not a zero-downtime guarantee.
5. Confirm `/api/health`, administrator sign-in at `/users.html`, original 0.3.21 connection, and 0.4.1 `/api/client-config`/token flow. Create a separate test key before testing 0.4.1.
6. In the admin page, choose a user, enable custom prompt, paste the complete prompt, and save. Room notes describe the setup but are not automatically appended to the prompt.
7. Test camera start, speaking, stop, reconnect and at least several minutes of output on Teams, Meet and Zoom before store submission. Local tests used fake credentials/provider responses and consumed no provider credits.

No administrator or provider key belongs in the extension. Newly created user keys are displayed once; save and share them privately. Lost keys can be replaced. Users sharing a key share an account and cannot be distinguished reliably by typed names.

## Prompt limits and updates

The application enforces **1–1,024 UTF-16 code units**, matching JavaScript textarea/string counting. Ordinary English characters count as one; emoji may count as two. This is the release's conservative application limit, not a newly verified claim about the provider's word limit. Invalid or overlong prompts are rejected before minting a token.

The full prompt, not an addition to the bundled prompt, is saved. A saved default affects only users without a custom prompt. Revision checks reject concurrent stale edits. Prompt content is treated as plain text; the extension loads no remote executable code.

0.4.1 checks access about every 30 seconds while the browser runs. Revocation/replacement stops its active session on the next check; verification failure lasting three minutes also stops it. Browser suspension can delay this check. Older clients do not have active-session revocation checks, and issued provider tokens are not remotely recalled by this backend.

## Suggested lighting test prompt

Use as a complete per-user prompt for controlled testing, not as a guarantee of model output:

> Replace only the live person with the consented reference person. Match identity, facial features, skin tone, hair shape and color, facial hair, clothing and natural body proportions. Use the reference for appearance, not lighting. Follow live pose, gestures, blinking, lip closures, jaw motion and speaking rhythm without exaggeration. Keep head, neck, shoulders and torso connected without stretching. Follow the live camera's light direction and intensity without amplification. Keep hair, forehead and shoulder highlights subdued with visible texture. Maintain stable exposure, neutral color and soft shadows over time; no added golden light, glowing edges or progressive brightening. Keep hair volume consistent without added curls or flyaway strands. Preserve the live background, wall colors, furniture and objects. Preserve held or entering objects, including those crossing the body, with their original shape, text, logos and symbols.

## Validation

- Python: `python -m unittest discover -s tests` — 61 tests passed, including legacy contracts, personal-key isolation, rotation/revocation, persistence, prompt limits and usage snapshots.
- Frontend: `npm run typecheck`, `npm run build`, and `node --test tests/test_frontend.mjs tests/test_admin_ui.mjs` — build passed, 42 tests passed.
- Extension release: `node releases/v0.4.1/build.mjs` and `node --test releases/v0.4.1/test.mjs` — six release checks passed. Capture bundles match 0.3.21 apart from the MAIN token-to-prompt assignment; the isolated provider bundle is unchanged.
- Chromium: unpacked 0.4.1 service worker loaded; access alarm registered; admin create/save/lock flow passed at desktop/mobile sizes with no page errors. Screenshots were inspected.
- Live paid-provider video, administrator-authenticated production writes and volume backup/restore remain untested. Public deployment checks succeeded as described above.

## Rollback

Roll back the application image if necessary while preserving the volume and all tables. The old backend does not recognize newly created database-managed keys or their revocations; it only knows its environment keys. Therefore rollback is not a safe way to maintain new managed accounts: stop 0.4.1 testing and deliberately reconcile revoked/rotated legacy credentials before returning to the old backend. Original 0.3.21 files were not modified.
