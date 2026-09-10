import { createDecartClient, models, resolveFpsNumber, type RealTimeClient } from "@decartai/sdk";
import "./style.css";

// Product defaults: used for both initial connections and reference updates.
// This is frontend code, not a secret; the compiled prompt remains inspectable.
const TRANSFORMATION_PROMPT = "Create a photorealistic professional webcam video using the identity and facial features of the attached consented reference person. Preserve the person’s face shape, skin tone, hairstyle, eyes, eyebrows, nose, facial hair, and age consistently across every frame. Follow the live speaker’s natural lip movements, blinking, expressions, and small head movements accurately. Keep the face stable with no flickering, stretching, melting, identity drift, duplicate features, warped mouth, distorted teeth, or changing hairstyle. Use realistic skin texture, soft indoor lighting, a fixed eye-level webcam angle, natural shoulders, and a clean neutral office background. Frame the person from the chest upward, centered like a normal Zoom call. Keep motion subtle and professional."; 
const ENHANCE_PROMPT = true;

const el = <T extends HTMLElement>(id: string) => document.querySelector<T>("#" + id)!;
const inputVideo = el<HTMLVideoElement>("inputVideo");
const outputVideo = el<HTMLVideoElement>("outputVideo");
const inputPlaceholder = el<HTMLElement>("inputPlaceholder");
const outputPlaceholder = el<HTMLElement>("outputPlaceholder");
const cameraSelect = el<HTMLSelectElement>("cameraSelect");
const modelSelect = el<HTMLSelectElement>("modelSelect");
const referenceInput = el<HTMLInputElement>("referenceInput");
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

function model() {
  return models.realtime(modelSelect.value === "lucy-2.5" ? "lucy-2.5" : "lucy-2.1");
}
function status(text: string, tone = "idle") {
  el<HTMLElement>("statusText").textContent = text;
  el<HTMLElement>("status").dataset.tone = tone;
}
function showError(error: unknown) {
  notice.textContent = error instanceof Error ? error.message : String(error);
  notice.dataset.tone = "error";
  notice.hidden = false;
}
function syncControls() {
  cameraButton.disabled = busy || !!connection;
  cameraSelect.disabled = busy || !!connection;
  modelSelect.disabled = busy || !!connection;
  referenceInput.disabled = busy;
  consent.disabled = busy || !!connection;
  accessKey.disabled = busy || !!connection;
  connectButton.disabled = busy || !!connection || !camera || !referenceInput.files?.[0] || !consent.checked;
  updateButton.disabled = busy || !connection || !referenceInput.files?.[0];
  fullscreenButton.disabled = !remote;
  stopButton.disabled = !camera && !remote && !busy;
  cameraButton.textContent = camera ? "Restart camera" : "Start camera";
}
function clearRemote() {
  const previous = connection;
  connection = null;
  try { previous?.disconnect(); } catch { /* Already disconnected. */ }
  remote?.getTracks().forEach(track => track.stop());
  remote = null;
  outputVideo.srcObject = null;
  outputPlaceholder.hidden = false;
}
function stop() {
  ++version;
  pending?.abort();
  pending = null;
  busy = false;
  clearRemote();
  camera?.getTracks().forEach(track => track.stop());
  camera = null;
  inputVideo.srcObject = null;
  inputPlaceholder.hidden = false;
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
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: {
        deviceId: cameraSelect.value ? { exact: cameraSelect.value } : undefined,
        width: { ideal: selected.width },
        height: { ideal: selected.height },
        frameRate: { ideal: resolveFpsNumber(selected.fps, 25), max: resolveFpsNumber(selected.fps, 25) },
      },
    });
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
    status("Camera ready");
    await loadCameras().catch(() => undefined);
  } catch (error) {
    if (ownVersion === version) { status("Camera unavailable", "error"); showError(error); }
  } finally {
    if (ownVersion === version) { busy = false; syncControls(); }
  }
}
async function fetchToken(signal: AbortSignal): Promise<string> {
  const response = await fetch("/api/realtime-token", {
    method: "POST",
    headers: { Authorization: "Bearer " + accessKey.value.trim() },
    signal,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || typeof payload.apiKey !== "string") {
    throw new Error(payload.detail || "Could not create a connection token.");
  }
  return payload.apiKey;
}
async function connect() {
  if (busy || connection || !camera) return;
  if (!consent.checked) { showError(new Error("Agree to the cloud-processing disclosure before connecting.")); return; }
  const reference = referenceInput.files?.[0];
  if (!reference) { showError(new Error("Choose a reference image.")); return; }
  if (!["image/png", "image/jpeg", "image/webp"].includes(reference.type) || reference.size > 10 * 1024 * 1024) {
    showError(new Error("Choose a PNG, JPEG, or WebP image smaller than 10 MB.")); return;
  }
  if (accessKey.value.trim().length < 32) {
    showError(new Error("Enter the personal access key supplied by Bluqq, not a provider API key.")); return;
  }
  const ownVersion = ++version;
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
  syncControls();
  try {
    const token = await fetchToken(abort.signal);
    if (ownVersion !== version) return;
    const client = createDecartClient({ apiKey: token });
    const connected = await client.realtime.connect(camera, {
      model: model(),
      mirror: "auto",
      initialState: { prompt: { text: TRANSFORMATION_PROMPT, enhance: ENHANCE_PROMPT }, image: reference },
      onRemoteStream: (stream: MediaStream) => {
        if (ownVersion !== version) { stream.getTracks().forEach(track => track.stop()); return; }
        if (remote && remote !== stream) remote.getTracks().forEach(track => track.stop());
        remote = stream;
        outputVideo.srcObject = stream;
        outputPlaceholder.hidden = false;
        void outputVideo.play().then(() => {
          if (ownVersion === version) outputPlaceholder.hidden = true;
        }).catch(() => {
          if (ownVersion === version) showError(new Error("Video playback was blocked. Stop and reconnect."));
        });
        status("AI transformation live", "live");
        syncControls();
      },
      onConnectionChange: (state) => {
        if (ownVersion !== version) return;
        if (state === "disconnected") {
          stop();
          status("Disconnected", "error");
          showError(new Error("The session ended. Start the camera and reconnect when ready."));
        }
        if (state === "reconnecting") status("Reconnecting…", "working");
      },
    });
    if (ownVersion !== version) { connected.disconnect(); return; }
    connection = connected;
    connected.on("error", (error) => {
      if (ownVersion !== version) return;
      stop();
      status("Connection failed", "error");
      showError(error);
    });
  } catch (error) {
    if (ownVersion === version) {
      // Invalidate all callbacks belonging to the failed connection.
      stop();
      status("Connection failed", "error");
      showError(error);
    }
  } finally {
    window.clearTimeout(timer);
    if (ownVersion === version) { busy = false; pending = null; syncControls(); }
  }
}
async function update() {
  const reference = referenceInput.files?.[0];
  if (!connection || !reference || busy) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(reference.type) || reference.size > 10 * 1024 * 1024) {
    showError(new Error("Choose a PNG, JPEG, or WebP image smaller than 10 MB.")); return;
  }
  const ownVersion = version;
  busy = true;
  syncControls();
  try {
    await connection.set({ image: reference, prompt: TRANSFORMATION_PROMPT, enhance: ENHANCE_PROMPT });
    if (ownVersion === version) status("AI transformation live", "live");
  } catch (error) {
    if (ownVersion === version) showError(error);
  } finally {
    if (ownVersion === version) { busy = false; syncControls(); }
  }
}
cameraButton.addEventListener("click", () => void startCamera());
connectButton.addEventListener("click", () => void connect());
updateButton.addEventListener("click", () => void update());
stopButton.addEventListener("click", stop);
referenceInput.addEventListener("change", syncControls);
consent.addEventListener("change", syncControls);
el<HTMLButtonElement>("refreshCamerasButton").addEventListener("click", () => void loadCameras().catch(showError));
fullscreenButton.addEventListener("click", () => void el<HTMLElement>("outputCard").requestFullscreen().catch(showError));
window.addEventListener("pagehide", stop);
window.addEventListener("beforeunload", stop);
syncControls();
if (!navigator.mediaDevices?.getUserMedia) {
  cameraButton.disabled = true;
  showError(new Error("Camera access needs HTTPS (or localhost) and a supported browser."));
} else {
  void loadCameras().catch(() => undefined);
}
