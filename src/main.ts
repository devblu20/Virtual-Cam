import { createDecartClient, models, type RealTimeClient } from "@decartai/sdk";
import "./style.css";
import { UsageReporter, referenceMetadata } from "./usage";

// Video profile from the supplied Bluqq ZIP (manifest version 0.3.8).
// Keep initial connections and reference updates aligned. See EXTENSION_PARITY.md.
// The only editable prompt lives in transformation_prompt.txt on Railway.
// Every new connection receives a validated, session-pinned text configuration.
type ProcessingConfig = { schemaVersion: 1; prompt: string; revision: string };
let sessionPrompt: string | null = null;
function processingConfig(value: unknown): ProcessingConfig {
  const config = value as Partial<ProcessingConfig> | null;
  if (!config || config.schemaVersion !== 1 || typeof config.prompt !== "string" ||
      !config.prompt.trim() || config.prompt.length > 4000 ||
      typeof config.revision !== "string" || !/^[a-f0-9]{64}$/.test(config.revision)) {
    throw new Error("Railway did not provide a valid processing prompt. Deploy the updated backend, then reconnect.");
  }
  return { schemaVersion: 1, prompt: config.prompt, revision: config.revision };
}
const EXTENSION_MODEL = "lucy-2.1";
const CAMERA_FPS = 25;
// Keep the explicit identity/clothing instructions intact instead of rewriting them.
const ENHANCE_PROMPT = false;

const el = <T extends HTMLElement>(id: string) => document.querySelector<T>("#" + id)!;
const inputVideo = el<HTMLVideoElement>("inputVideo");
const outputVideo = el<HTMLVideoElement>("outputVideo");
const inputPlaceholder = el<HTMLElement>("inputPlaceholder");
const outputPlaceholder = el<HTMLElement>("outputPlaceholder");
const cameraSelect = el<HTMLSelectElement>("cameraSelect");
const referenceInput = el<HTMLInputElement>("referenceInput");
const referencePreview = el<HTMLImageElement>("referencePreview");
const referenceDetails = el<HTMLElement>("referenceDetails");
const referenceState = el<HTMLElement>("referenceState");
const cameraDetails = el<HTMLElement>("cameraDetails");
const outputDetails = el<HTMLElement>("outputDetails");
const networkDetails = el<HTMLElement>("networkDetails");
const accessKey = el<HTMLInputElement>("accessKey");
const consent = el<HTMLInputElement>("consent");
const cameraButton = el<HTMLButtonElement>("cameraButton");
const connectButton = el<HTMLButtonElement>("connectButton");
const updateButton = el<HTMLButtonElement>("updateButton");
const stopButton = el<HTMLButtonElement>("stopButton");
const fullscreenButton = el<HTMLButtonElement>("fullscreenButton");
const notice = el<HTMLElement>("notice");
let camera: MediaStream | null = null;
let remote: MediaStream | null = null;
let connection: RealTimeClient | null = null;
let busy = false;
let version = 0;
let pending: AbortController | null = null;
let referenceVersion = 0;
let checkingReference = false;
let preparedReference: File | null = null;
let appliedReference: File | null = null;
let referenceUrl: string | null = null;
let usage: UsageReporter | null = null;
// Memory-only, retained through stop() so a failed connection's token can be
// redacted from its error. Replaced on the next token request; never logged.
let diagnosticToken = "";

function model() {
  return models.realtime(EXTENSION_MODEL);
}
function syncReferenceState() {
  referenceState.textContent = checkingReference ? "Checking image locally…"
    : !preparedReference ? "Choose a reference before connecting."
    : preparedReference === appliedReference ? "Reference applied to this session."
    : connection ? "New reference selected — click Apply reference to send it."
    : "Reference ready — it will be sent when you connect.";
}
function releaseReferencePreview() {
  if (referenceUrl) URL.revokeObjectURL(referenceUrl);
  referenceUrl = null;
  referencePreview.removeAttribute("src");
  referencePreview.hidden = true;
}
async function prepareReference() {
  const ownVersion = ++referenceVersion;
  const file = referenceInput.files?.[0];
  preparedReference = null;
  releaseReferencePreview();
  referenceDetails.textContent = "";
  referenceDetails.dataset.tone = "idle";
  checkingReference = !!file;
  syncControls();
  if (!file) return;
  let bitmap: ImageBitmap | null = null;
  try {
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type) || !file.size || file.size > 10 * 1024 * 1024) {
      throw new Error("Choose a PNG, JPEG or WebP image up to 10 MB.");
    }
    bitmap = await createImageBitmap(file);
    if (ownVersion !== referenceVersion) return;
    if (bitmap.width < 512 || bitmap.height < 512) {
      throw new Error(`Reference is only ${bitmap.width} × ${bitmap.height}. Use an original image at least 512 × 512; upscaling does not restore face detail.`);
    }
    preparedReference = file;
    referenceUrl = URL.createObjectURL(file);
    referencePreview.src = referenceUrl;
    referencePreview.hidden = false;
    const modestSize = Math.min(bitmap.width, bitmap.height) < 768;
    referenceDetails.textContent = `${bitmap.width} × ${bitmap.height} · Original image used without cropping or recompression.${modestSize ? " A sharper, larger original can retain more beard and hair detail." : " Check that the face, full hair, beard, shoulders and clothing are visible."}`;
    referenceDetails.dataset.tone = modestSize ? "warning" : "idle";
  } catch (error) {
    if (ownVersion !== referenceVersion) return;
    referenceDetails.textContent = error instanceof Error ? error.message : "This image could not be decoded. Choose another portrait.";
    referenceDetails.dataset.tone = "warning";
    if (connection) referenceDetails.textContent += " The previously applied reference is still active.";
  } finally {
    bitmap?.close();
    if (ownVersion === referenceVersion) { checkingReference = false; syncControls(); }
  }
}
function updateCameraDetails() {
  if (!camera) { cameraDetails.textContent = "Camera: not started"; cameraDetails.dataset.tone = "idle"; return; }
  const settings = camera.getVideoTracks()[0]?.getSettings();
  const width = settings?.width || inputVideo.videoWidth;
  const height = settings?.height || inputVideo.videoHeight;
  const target = model();
  const belowTarget = !!(width && height && width * height < target.width * target.height);
  cameraDetails.textContent = width && height
    ? `Camera: ${width} × ${height}${settings?.frameRate ? ` · ${Math.round(settings.frameRate)} fps` : ""}${belowTarget ? " — below model input target; use a higher-resolution camera setting/device." : ""}`
    : "Camera active · resolution not reported yet";
  cameraDetails.dataset.tone = belowTarget ? "warning" : "idle";
}
function updateOutputDetails() {
  if (!remote) { outputDetails.textContent = "Output: not connected"; outputDetails.dataset.tone = "idle"; return; }
  const width = outputVideo.videoWidth;
  const height = outputVideo.videoHeight;
  const target = model();
  const belowTarget = !!(width && height && width * height < target.width * target.height);
  outputDetails.textContent = width && height
    ? `Received: ${width} × ${height} · Bluqq extension 0.3.8 profile${belowTarget ? " — lower pixel count than the model input; inspect output detail." : ""}`
    : "Output: provider default, matching the extension · Waiting for decoded video dimensions…";
  outputDetails.dataset.tone = belowTarget ? "warning" : "idle";
}
function status(text: string, tone = "idle") {
  el<HTMLElement>("statusText").textContent = text;
  el<HTMLElement>("status").dataset.tone = tone;
}
function errorText(error: unknown, fallback = "The operation failed without an error message. Stop and reconnect; contact Bluqq support if it continues."): string {
  // SDK errors are plain objects, not necessarily Error instances. Read only
  // public message fields, never dump data, cause, stack or request headers.
  function describe(value: unknown, depth = 0): string {
    if (depth > 3) return "";
    if (typeof value === "string") return value.trim() === "[object Object]" ? "" : value.trim();
    if (!value || typeof value !== "object") return "";
    if (Array.isArray(value)) return value.slice(0, 3).map(item => describe(item, depth + 1)).filter(Boolean).join("; ");
    const item = value as Record<string, unknown>;
    const code = typeof item.code === "string" && /^[A-Z0-9_.-]{1,64}$/.test(item.code) ? item.code : "";
    const message = [item.message, item.detail, item.msg, item.error]
      .map(part => describe(part, depth + 1)).find(Boolean) || "";
    return code ? `[${code}] ${message || fallback}` : message;
  }
  let text = describe(error) || fallback;
  for (const secret of [accessKey.value.trim(), diagnosticToken]) {
    if (secret) text = text.split(secret).join("[redacted]");
  }
  // Signaling URLs can contain ephemeral tokens. Keep neither URLs nor
  // credential-shaped fields in the user-visible diagnostic.
  text = text.replace(/\b(?:https?|wss?):\/\/[^\s<>"']+/gi, "[URL redacted]")
    .replace(/\bBearer\s+[^\s,;"']+/gi, "Bearer [redacted]")
    .replace(/\b(api[_-]?key|access[_-]?key|token|authorization)\s*[=:]\s*[^\s,;]+/gi, "$1=[redacted]");
  return text.length > 1200 ? text.slice(0, 1199) + "…" : text;
}
function showError(error: unknown) {
  notice.textContent = errorText(error);
  notice.dataset.tone = "error";
  notice.hidden = false;
}
function syncControls() {
  cameraButton.disabled = busy || !!connection;
  cameraSelect.disabled = busy || !!connection;
  referenceInput.disabled = busy;
  consent.disabled = busy || !!connection;
  accessKey.disabled = busy || !!connection;
  const validReference = !!preparedReference && preparedReference === referenceInput.files?.[0] && !checkingReference;
  connectButton.disabled = busy || !!connection || !camera || !validReference || !consent.checked;
  updateButton.disabled = busy || !connection || !validReference;
  fullscreenButton.disabled = !remote;
  stopButton.disabled = !camera && !remote && !busy;
  cameraButton.textContent = camera ? "Restart camera" : "Start camera";
  syncReferenceState();
}
function clearRemote() {
  const previous = connection;
  connection = null;
  sessionPrompt = null;
  try { previous?.disconnect(); } catch { /* Already disconnected. */ }
  remote?.getTracks().forEach(track => track.stop());
  remote = null;
  appliedReference = null;
  outputVideo.srcObject = null;
  outputPlaceholder.hidden = false;
  updateOutputDetails();
  networkDetails.textContent = "Connection quality: not connected";
  networkDetails.dataset.tone = "idle";
}
function stop(reason = "stopped") {
  ++version;
  usage?.stop(reason);
  usage = null;
  pending?.abort();
  pending = null;
  busy = false;
  clearRemote();
  camera?.getTracks().forEach(track => track.stop());
  camera = null;
  inputVideo.srcObject = null;
  inputPlaceholder.hidden = false;
  updateCameraDetails();
  status("Stopped");
  syncControls();
}
async function loadCameras() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  const previous = cameraSelect.value;
  cameraSelect.replaceChildren(new Option("Default camera", ""));
  devices.filter(d => d.kind === "videoinput").forEach((d, i) => {
    cameraSelect.add(new Option(d.label || "Camera " + (i + 1), d.deviceId));
  });
  if (devices.some(d => d.deviceId === previous)) cameraSelect.value = previous;
}
async function startCamera() {
  if (busy || connection) return;
  stop();
  const ownVersion = version;
  busy = true;
  notice.hidden = true;
  status("Opening camera…", "working");
  syncControls();
  try {
    const selected = model();
    const constraints: MediaStreamConstraints = {
      audio: false,
      video: {
        deviceId: cameraSelect.value ? { exact: cameraSelect.value } : undefined,
        width: { ideal: selected.width },
        height: { ideal: selected.height },
        frameRate: { ideal: CAMERA_FPS, max: CAMERA_FPS },
      },
    };
    // Match the extension's ideal size and fixed 25 fps cap. The browser may
    // negotiate a different size; report the actual settings above the controls.
    const stream = await navigator.mediaDevices.getUserMedia(constraints);
    if (ownVersion !== version) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    camera = stream;
    stream.getVideoTracks().forEach(track => track.addEventListener("ended", () => {
      if (camera === stream) { stop(); showError(new Error("Camera disconnected. Start it again to continue.")); }
    }));
    inputVideo.srcObject = stream;
    inputPlaceholder.hidden = true;
    updateCameraDetails();
    status("Camera ready");
    await loadCameras().catch(() => undefined);
  } catch (error) {
    if (ownVersion === version) { status("Camera unavailable", "error"); showError(error); }
  } finally {
    if (ownVersion === version) { busy = false; syncControls(); }
  }
}
async function fetchToken(signal: AbortSignal, reporter: UsageReporter, reference: File): Promise<{ apiKey: string; config: ProcessingConfig }> {
  diagnosticToken = "";
  const avatar = await referenceMetadata(reference);
  signal.throwIfAborted();
  const response = await fetch("/api/realtime-token", {
    method: "POST",
    headers: { Authorization: "Bearer " + accessKey.value.trim(), "Content-Type": "application/json" },
    body: JSON.stringify({ processingConfigVersion: 1, usage: { source: "website", platform: "website", version: "usage-v2", avatar } }),
    cache: "no-store", credentials: "omit", redirect: "error",
    signal,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || typeof payload?.apiKey !== "string") {
    throw {
      code: Number.isInteger(response.status) ? `HTTP_${response.status}` : "TOKEN_REQUEST_FAILED",
      message: errorText(payload, "Could not create a connection token."),
    };
  }
  diagnosticToken = payload.apiKey;
  reporter.attach(payload.usageSessionId);
  return { apiKey: payload.apiKey, config: processingConfig(payload.processingConfig) };
}
async function connect() {
  if (busy || connection || !camera) return;
  if (!consent.checked) { showError(new Error("Agree to the cloud-processing disclosure before connecting.")); return; }
  const reference = preparedReference;
  if (checkingReference || !reference || reference !== referenceInput.files?.[0]) {
    showError(new Error("Choose a valid reference and wait for the image check before connecting.")); return;
  }
  if (accessKey.value.trim().length < 32) {
    showError(new Error("Enter the personal access key supplied by Bluqq, not a provider API key.")); return;
  }
  const ownVersion = ++version;
  const reporter = new UsageReporter(accessKey.value.trim(), message => {
    if (ownVersion === version) el<HTMLElement>("usageNotice").textContent = message;
  });
  usage = reporter;
  busy = true;
  pending = new AbortController();
  const abort = pending;
  const timer = window.setTimeout(() => {
    if (ownVersion !== version) return;
    stop();
    status("Connection timed out", "error");
    showError(new Error("Connection timed out. Start the camera and try again."));
  }, 45000);
  notice.hidden = true;
  status("Connecting to AI processing…", "working");
  networkDetails.textContent = "Connection quality: waiting for measurements…";
  syncControls();
  try {
    const token = await fetchToken(abort.signal, reporter, reference);
    if (ownVersion !== version) return;
    const client = createDecartClient({
      apiKey: token.apiKey,
      telemetry: false,
      logger: { debug() {}, info() {}, warn() {}, error() {} },
      integration: "bluqq-website",
    });
    const connected = await client.realtime.connect(camera, {
      model: model(),
      mirror: false,
      // No resolution override: use the same provider default as the ZIP.
      initialState: { prompt: { text: token.config.prompt, enhance: ENHANCE_PROMPT }, image: reference },
      onRemoteStream: (stream: MediaStream) => {
        if (ownVersion !== version) { stream.getTracks().forEach(track => track.stop()); return; }
        if (remote && remote !== stream) remote.getTracks().forEach(track => track.stop());
        remote = stream;
        outputVideo.srcObject = stream;
        reporter.watch(outputVideo);
        updateOutputDetails();
        outputPlaceholder.hidden = false;
        void outputVideo.play().then(() => {
          if (ownVersion === version) outputPlaceholder.hidden = true;
        }).catch(() => {
          if (ownVersion === version) showError(new Error("Video playback was blocked. Stop and reconnect."));
        });
        status("AI transformation live", "live");
        syncControls();
      },
      onConnectionQuality: (report) => {
        if (ownVersion !== version) return;
        const low = report.quality === "poor" || report.quality === "critical";
        const fps = report.metrics.fps;
        networkDetails.textContent = report.warmingUp ? "Connection quality: measuring…"
          : `Connection: ${report.quality}${fps == null ? "" : ` · ${Math.round(fps)} received fps`}${report.limitingFactor === "none" ? "" : ` · limited by ${report.limitingFactor}`}${low ? ". Close heavy apps/downloads or try a wired connection." : ""}`;
        networkDetails.dataset.tone = low && !report.warmingUp ? "warning" : "idle";
      },
      onConnectionChange: (state) => {
        if (ownVersion !== version) return;
        if (state === "disconnected") {
          stop("disconnected");
          status("Disconnected", "error");
          showError(new Error("The session ended. Start the camera and reconnect when ready."));
        }
        if (state === "reconnecting") status("Reconnecting…", "working");
      },
    });
    if (ownVersion !== version) { connected.disconnect(); return; }
    connection = connected;
    sessionPrompt = token.config.prompt;
    appliedReference = reference;
    connected.on("error", (error) => {
      if (ownVersion !== version) return;
      stop("failed");
      status("Connection failed", "error");
      showError(error);
    });
  } catch (error) {
    if (ownVersion === version) {
      // Invalidate all callbacks belonging to the failed connection.
      stop("failed");
      status("Connection failed", "error");
      showError(error);
    }
  } finally {
    window.clearTimeout(timer);
    if (ownVersion === version) { busy = false; pending = null; syncControls(); }
  }
}
async function update() {
  const reference = preparedReference;
  if (!connection || !reference || reference !== referenceInput.files?.[0] || busy || checkingReference) return;
  const ownVersion = version;
  busy = true;
  syncControls();
  try {
    const avatar = await referenceMetadata(reference);
    if (ownVersion !== version || !connection) return;
    if (sessionPrompt === null) throw new Error("Reconnect to load the Railway processing prompt.");
    await connection.set({ image: reference, prompt: sessionPrompt, enhance: ENHANCE_PROMPT });
    if (ownVersion === version) { usage?.avatar(avatar); appliedReference = reference; notice.hidden = true; status("AI transformation live", "live"); }
  } catch (error) {
    if (ownVersion === version) showError(error);
  } finally {
    if (ownVersion === version) { busy = false; syncControls(); }
  }
}
cameraButton.addEventListener("click", () => void startCamera());
connectButton.addEventListener("click", () => void connect());
updateButton.addEventListener("click", () => void update());
stopButton.addEventListener("click", () => stop());
referenceInput.addEventListener("change", () => void prepareReference());
// A new device needs fresh camera constraints.
cameraSelect.addEventListener("change", () => {
  if (camera) { stop(); status("Settings changed — start camera again"); }
});
inputVideo.addEventListener("loadedmetadata", updateCameraDetails);
inputVideo.addEventListener("resize", updateCameraDetails);
outputVideo.addEventListener("loadedmetadata", updateOutputDetails);
outputVideo.addEventListener("resize", updateOutputDetails);
consent.addEventListener("change", syncControls);
el<HTMLButtonElement>("refreshCamerasButton").addEventListener("click", () => void loadCameras().catch(showError));
fullscreenButton.addEventListener("click", () => void el<HTMLElement>("outputCard").requestFullscreen().catch(showError));
window.addEventListener("pagehide", () => { stop("closed"); ++referenceVersion; checkingReference = false; preparedReference = null; releaseReferencePreview(); });
window.addEventListener("pageshow", () => { if (referenceInput.files?.[0] && !preparedReference) void prepareReference(); });
window.addEventListener("beforeunload", () => stop("closed"));
syncControls();
if (!navigator.mediaDevices?.getUserMedia) {
  cameraButton.disabled = true;
  showError(new Error("Camera access needs HTTPS (or localhost) and a supported browser."));
} else {
  void loadCameras().catch(() => undefined);
}
