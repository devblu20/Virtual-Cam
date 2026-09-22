# One prompt, managed on Railway

The only editable production transformation prompt is `transformation_prompt.txt`
in this project. Its original wording is preserved verbatim. With approval on
2026-09-22, instructions for stable backgrounds, realistic objects and faithful
document/phone-screen content were appended, not substituted for the original.

The provider rejected the first expanded version (1,795 characters), reporting
that only its first 1,065 characters fit this model. The added section has been
condensed to 192 characters, retaining the original 792-character prompt verbatim.
This is a prompt-specific provider rejection, not proof of a universal character
limit. Local schema validation cannot guarantee the provider's model budget;
validate future prompt changes with a live connection before public rollout.

## One-time rollout

1. Push and deploy this project to Railway first. The Dockerfile includes the
   prompt file and processing_config.py. No new environment variable is needed.
2. Update the existing extension to 0.3.16, then refresh meeting tabs. Do not run
   duplicate Bluqq copies. Older extension versions keep their bundled prompt;
   deploying Railway alone cannot convert them to server-managed prompts.
3. Refresh the website to load the new frontend. Test a consented session on the
   website and in each supported browser meeting before public distribution.

## Future approved prompt changes

Edit only `transformation_prompt.txt`, commit/push it, and let Railway redeploy.
Stop and start transformation to use the new prompt. No extension ZIP update is
needed for compatible prompt-text changes after version 0.3.16 is installed.
An active session keeps its current prompt, including website Apply reference.
It does not silently change appearance midway through a meeting.

## Delivery and limits

Each new authorized token request includes processingConfigVersion: 1. Railway
reads the prompt file for that request and returns plain JSON processingConfig:
schemaVersion, prompt, and its SHA-256 revision. The endpoint is authenticated,
uses no-store caching, and ignores client-provided prompt overrides. Requests
without the new field retain the previous token response for older clients.

The website pins the returned text to its connection. The extension passes it
through its credential bridge to the Meet/Zoom connection, or through its
authorized isolated Teams provider configuration. It is transient client data,
not a second editable prompt source. Prompts are inspectable in the client and
are not secrets. Never put credentials or private business data in this file.

Use non-empty UTF-8 text, at most 4,000 UTF-16 code units / 16,000 UTF-8 bytes.
Missing/invalid prompt files are rejected before creating a provider token for
new clients. Updated clients reject missing/malformed configuration, including
an outdated backend; there is no old bundled-prompt fallback. Provider limits
can be stricter; length validation does not guarantee acceptance or video quality.

Only text instructions are remote. Model selection, enhancement=false, service
URLs, permissions, camera and audio logic remain local code. No eval, remote
JavaScript, or remote Wasm is added. The source SDK bundle is retained; a small
Meet/Zoom application-level token receiver now binds the server prompt to the
session. Teams' existing isolated-provider prompt path is reused unchanged.

Chrome distinguishes remote configuration from executable remote code:
https://developer.chrome.com/docs/webstore/program-policies/mv3-requirements
Store approval is still subject to review; no approval is guaranteed.
Changes to browser functionality, permissions, UI, or this data contract can
still require an extension update. This is not remote extension-code updating.

## Verification

Automated tests use fake credentials and provider mocks. Test failure handling
with an unavailable backend, Stop during authorization, and a fresh connection
after an approved server prompt change. Real camera/receiver quality and exact
document-text preservation need live validation; a prompt cannot guarantee them.
