// Browser-lifecycle simulations, not a real camera/Decart integration test.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { transformWithOxc } from "vite";

const source = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8")
  .replace(/^import .*from "@decartai\/sdk";/m, 'const { createDecartClient, models, resolveFpsNumber } = require("@decartai/sdk");')
  .replace('import "./style.css";', "");
const compiled = (await transformWithOxc(source, "main.ts")).code.replace(/export\s*\{\s*\};?/g, "");

function media() {
  const track = { stopped: false, settings: { width: 1280, height: 720, frameRate: 30 }, listeners: {}, stop() { this.stopped = true; },
    getSettings() { return this.settings; },
    addEventListener(name, cb) { this.listeners[name] = cb; } };
  return { track, getTracks: () => [track], getVideoTracks: () => [track] };
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function flush() { for (let i = 0; i < 6; i++) await new Promise(setImmediate); }

function harness() {
  const elements = new Map();
  class Element {
    value = ""; checked = false; disabled = false; hidden = false;
    files = []; dataset = {}; listeners = {}; srcObject = null; videoWidth = 0; videoHeight = 0;
    addEventListener(name, cb) { this.listeners[name] = cb; }
    replaceChildren() {} add() {}
    removeAttribute(name) { delete this[name]; }
    play() { return Promise.resolve(); }
    requestFullscreen() { return Promise.resolve(); }
  }
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  el("modelSelect").value = "lucy-2.5";
  el("resolutionSelect").value = "720p";
  el("referenceInput").files = [{ type: "image/png", size: 1024 }];
  el("accessKey").value = "fake-test-access-key-12345678901234567890";
  el("consent").checked = true;
  const raw = media();
  const remote = media();
  const calls = { fetch: [], decart: [], updates: [], camera: [], bitmapClosed: 0, disconnected: 0 };
  const connection = {
    disconnect() { calls.disconnected++; },
    on() {},
    async set(state) { calls.updates.push(state); },
  };
  const hooks = {
    camera: async () => raw,
    decode: async file => ({ width: file.width ?? 1024, height: file.height ?? 1280, close() { calls.bitmapClosed++; } }),
    fetch: async () => ({ ok: true, json: async () => ({ apiKey: "short-lived-test-token" }) }),
    connect: async (_stream, options) => { options.onRemoteStream(remote); return connection; },
  };
  const sdk = {
    models: { realtime: name => ({ name, width: name === "lucy-2.1" ? 1088 : 1280, height: name === "lucy-2.1" ? 624 : 720, fps: 30 }) },
    resolveFpsNumber: fps => fps,
    createDecartClient: () => ({ realtime: { connect: (...args) => {
      calls.decart.push(args); return hooks.connect(...args);
    } } }),
  };
  const timers = new Set();
  const urls = new Set();
  const windowListeners = {};
  const context = vm.createContext({
    exports: {},
    require: name => name === "@decartai/sdk" ? sdk : {},
    document: { querySelector: selector => el(selector.slice(1)) },
    navigator: { mediaDevices: {
      getUserMedia: (...args) => { calls.camera.push(args); return hooks.camera(...args); },
      enumerateDevices: async () => [],
    } },
    window: {
      addEventListener(name, callback) { windowListeners[name] = callback; },
      setTimeout(cb, ms) { const t = setTimeout(cb, ms); timers.add(t); return t; },
      clearTimeout(t) { clearTimeout(t); timers.delete(t); },
    },
    Option: class {},
    AbortController,
    Error,
    URL: { createObjectURL: () => { const url = `blob:test-${urls.size}`; urls.add(url); return url; }, revokeObjectURL: url => urls.delete(url) },
    createImageBitmap: file => hooks.decode(file),
    fetch: (...args) => { calls.fetch.push(args); return hooks.fetch(...args); },
  });
  vm.runInContext(compiled, context);
  el("referenceInput").listeners.change();
  return {
    el, raw, remote, calls, hooks, connection, urls, windowListeners,
    click: async id => { el(id).listeners.click(); await flush(); },
    change: async id => { el(id).listeners.change(); await flush(); },
    cleanup: () => { windowListeners.pagehide(); for (const t of timers) clearTimeout(t); },
  };
}

test("connect sends personal auth and renders only transformed output", async () => {
  const h = harness();
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    assert.equal(h.calls.fetch.length, 1);
    assert.match(h.calls.fetch[0][1].headers.Authorization, /^Bearer fake-test/);
    assert.equal(h.el("outputVideo").srcObject, h.remote);
    await h.click("stopButton");
    assert.equal(h.raw.track.stopped, true);
    assert.equal(h.remote.track.stopped, true);
    assert.equal(h.el("outputVideo").srcObject, null);
  } finally { h.cleanup(); }
});

test("website uses Bluqq branding without editable prompt controls", () => {
  const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
  assert.match(html, /A product by <strong>Bluqq<\/strong>/);
  assert.doesNotMatch(html, /id="(?:promptInput|enhanceInput)"/);
  assert.match(html, /agree to send my camera video, reference image, and the built-in prompt/);
  assert.match(html, /AI-generated video/);
});

test("built-in prompt and enhancement apply to both connection and reference updates", async () => {
  const h = harness();
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    const initial = h.calls.decart[0][1].initialState.prompt;
    assert.match(initial.text, /^Replace the person in the live video/);
    assert.match(initial.text, /consented person/);
    assert.match(initial.text, /beard and moustache/);
    assert.match(initial.text, /hairline, hairstyle, hair color/);
    assert.match(initial.text, /upper-body build, clothing/);
    assert.ok(initial.text.length < 750);
    assert.equal(initial.enhance, false);
    assert.equal(h.calls.decart[0][1].initialState.image, h.el("referenceInput").files[0]);
    assert.match(h.el("referenceState").textContent, /Reference applied/);
    await h.click("updateButton");
    assert.equal(h.calls.updates.length, 1);
    assert.equal(h.calls.updates[0].prompt, initial.text);
    assert.equal(h.calls.updates[0].enhance, false);
    assert.equal(h.calls.updates[0].image, h.el("referenceInput").files[0]);
  } finally { h.cleanup(); }
});

test("stop while camera permission is pending stops the late stream", async () => {
  const h = harness();
  const waiting = deferred();
  h.hooks.camera = () => waiting.promise;
  try {
    await h.click("cameraButton");
    await h.click("stopButton");
    waiting.resolve(h.raw);
    await flush();
    assert.equal(h.raw.track.stopped, true);
    assert.equal(h.el("inputVideo").srcObject, null);
  } finally { h.cleanup(); }
});

test("stop during Decart connection disconnects a late result", async () => {
  const h = harness();
  const waiting = deferred();
  h.hooks.connect = () => waiting.promise;
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    await h.click("stopButton");
    waiting.resolve(h.connection);
    await flush();
    assert.equal(h.calls.disconnected, 1);
    assert.equal(h.el("outputVideo").srcObject, null);
  } finally { h.cleanup(); }
});

test("transformation failure never substitutes raw camera as output", async () => {
  const h = harness();
  h.hooks.connect = async () => { throw new Error("upstream failed"); };
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    assert.equal(h.el("outputVideo").srcObject, null);
    assert.equal(h.raw.track.stopped, true);
    assert.match(h.el("notice").textContent, /upstream failed/);
  } finally { h.cleanup(); }
});

test("missing consent prevents network processing", async () => {
  const h = harness();
  h.el("consent").checked = false;
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    assert.equal(h.calls.fetch.length, 0);
  } finally { h.cleanup(); }
});

test("invalid access key prevents token request", async () => {
  const h = harness();
  h.el("accessKey").value = "";
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    assert.equal(h.calls.fetch.length, 0);
  } finally { h.cleanup(); }
});

test("double connect does not create multiple sessions", async () => {
  const h = harness();
  const waiting = deferred();
  h.hooks.connect = () => waiting.promise;
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    await h.click("connectButton");
    assert.equal(h.calls.decart.length, 1);
    waiting.resolve(h.connection);
    await flush();
  } finally { h.cleanup(); }
});

test("default HD model requests its native camera dimensions and explicit output resolution", async () => {
  const h = harness();
  try {
    await h.click("cameraButton");
    assert.equal(h.calls.camera[0][0].video.width.exact, 1280);
    assert.equal(h.calls.camera[0][0].video.height.exact, 720);
    assert.equal(h.calls.camera[0][0].audio, false);
    h.el("resolutionSelect").value = "1080p";
    await h.click("connectButton");
    assert.equal(h.calls.decart[0][1].model.name, "lucy-2.5");
    assert.equal(h.calls.decart[0][1].resolution, "1080p");
    assert.equal(h.el("resolutionSelect").disabled, true);
  } finally { h.cleanup(); }
});

test("unsupported exact camera size falls back to ideal and exposes a low-resolution warning", async () => {
  const h = harness();
  h.hooks.camera = async () => {
    if (h.calls.camera.length === 1) {
      const error = new Error("Unsupported dimensions"); error.name = "OverconstrainedError"; throw error;
    }
    h.raw.track.settings = { width: 640, height: 480, frameRate: 30 };
    return h.raw;
  };
  try {
    await h.click("cameraButton");
    assert.equal(h.calls.camera.length, 2);
    assert.equal(h.calls.camera[1][0].video.width.ideal, 1280);
    assert.match(h.el("cameraDetails").textContent, /640 × 480/);
    assert.equal(h.el("cameraDetails").dataset.tone, "warning");
  } finally { h.cleanup(); }
});

test("permission denial does not trigger a second camera request", async () => {
  const h = harness();
  h.hooks.camera = async () => { const error = new Error("Permission denied"); error.name = "NotAllowedError"; throw error; };
  try {
    await h.click("cameraButton");
    assert.equal(h.calls.camera.length, 1);
    assert.equal(h.calls.fetch.length, 0);
  } finally { h.cleanup(); }
});

test("switching model stops an existing camera so it must be recaptured at the new size", async () => {
  const h = harness();
  try {
    await h.click("cameraButton");
    h.el("modelSelect").value = "lucy-2.1";
    await h.change("modelSelect");
    assert.equal(h.raw.track.stopped, true);
    assert.equal(h.el("connectButton").disabled, true);
    await h.click("cameraButton");
    assert.equal(h.calls.camera[1][0].video.width.exact, 1088);
    assert.equal(h.calls.camera[1][0].video.height.exact, 624);
  } finally { h.cleanup(); }
});

test("tiny images are rejected before network processing", async () => {
  const h = harness();
  try {
    await flush();
    h.el("referenceInput").files = [{ type: "image/png", size: 1000, width: 256, height: 256 }];
    await h.change("referenceInput");
    await h.click("cameraButton");
    await h.click("connectButton");
    assert.equal(h.calls.fetch.length, 0);
    assert.match(h.el("referenceDetails").textContent, /at least 512/);
    assert.equal(h.el("referencePreview").hidden, true);
    assert.equal(h.urls.size, 0);
    assert.equal(h.calls.bitmapClosed, 2);
  } finally { h.cleanup(); }
});

test("invalid format, oversized files and decode errors cannot start cloud processing", async () => {
  for (const file of [
    { type: "image/gif", size: 1000 },
    { type: "image/png", size: 11 * 1024 * 1024 },
    { type: "image/png", size: 0 },
    { type: "image/png", size: 1000, corrupt: true },
  ]) {
    const h = harness();
    try {
      await flush();
      h.hooks.decode = async () => { throw new Error("Cannot decode image"); };
      h.el("referenceInput").files = [file];
      await h.change("referenceInput");
      await h.click("cameraButton");
      await h.click("connectButton");
      assert.equal(h.calls.fetch.length, 0);
      assert.equal(h.el("connectButton").disabled, true);
    } finally { h.cleanup(); }
  }
});

test("a slow old image decode cannot replace the newest selected reference", async () => {
  const h = harness();
  const waiting = deferred();
  const oldFile = { type: "image/png", size: 1000 };
  const newFile = { type: "image/jpeg", size: 2000 };
  try {
    await flush();
    const decode = h.hooks.decode;
    h.hooks.decode = file => file === oldFile ? waiting.promise : decode(file);
    h.el("referenceInput").files = [oldFile];
    await h.change("referenceInput");
    assert.equal(h.el("connectButton").disabled, true);
    h.el("referenceInput").files = [newFile];
    await h.change("referenceInput");
    let closed = false;
    waiting.resolve({ width: 4096, height: 4096, close() { closed = true; } });
    await flush();
    await h.click("cameraButton");
    await h.click("connectButton");
    assert.equal(h.calls.decart[0][1].initialState.image, newFile);
    assert.match(h.el("referenceDetails").textContent, /1024 × 1280/);
    assert.equal(closed, true);
  } finally { h.cleanup(); }
});

test("selected image stays pending until Apply reference succeeds", async () => {
  const h = harness();
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    const image = { type: "image/jpeg", size: 1234 };
    h.el("referenceInput").files = [image];
    await h.change("referenceInput");
    assert.match(h.el("referenceState").textContent, /click Apply reference/);
    assert.equal(h.calls.updates.length, 0);
    const set = h.connection.set;
    h.connection.set = async () => { throw new Error("Update failed"); };
    await h.click("updateButton");
    assert.match(h.el("referenceState").textContent, /click Apply reference/);
    h.connection.set = set;
    await h.click("updateButton");
    assert.equal(h.calls.updates[0].image, image);
    assert.match(h.el("referenceState").textContent, /Reference applied/);
  } finally { h.cleanup(); }
});

test("diagnostics report real received dimensions and ignore stale network callbacks", async () => {
  const h = harness();
  try {
    await h.click("cameraButton");
    h.el("resolutionSelect").value = "1080p";
    await h.click("connectButton");
    h.el("outputVideo").videoWidth = 1280;
    h.el("outputVideo").videoHeight = 720;
    h.el("outputVideo").listeners.resize();
    assert.match(h.el("outputDetails").textContent, /Received: 1280 × 720 · Requested: 1080p/);
    assert.equal(h.el("outputDetails").dataset.tone, "warning");
    const quality = h.calls.decart[0][1].onConnectionQuality;
    quality({ quality: "poor", limitingFactor: "bandwidth", warmingUp: false, metrics: { fps: 12 } });
    assert.match(h.el("networkDetails").textContent, /bandwidth/);
    await h.click("stopButton");
    quality({ quality: "poor", limitingFactor: "bandwidth", warmingUp: false, metrics: { fps: 12 } });
    assert.equal(h.el("networkDetails").textContent, "Connection quality: not connected");
    assert.equal(h.el("outputDetails").textContent, "Output: not connected");
  } finally { h.cleanup(); }
});

test("page cleanup releases reference URLs and prevents late preview resurrection", async () => {
  const h = harness();
  const waiting = deferred();
  await flush();
  assert.equal(h.urls.size, 1);
  h.hooks.decode = () => waiting.promise;
  h.el("referenceInput").files = [{ type: "image/png", size: 1000 }];
  await h.change("referenceInput");
  h.cleanup();
  let closed = false;
  waiting.resolve({ width: 1024, height: 1024, close() { closed = true; } });
  await flush();
  assert.equal(h.urls.size, 0);
  assert.equal(h.el("referencePreview").hidden, true);
  assert.equal(closed, true);
});

test("video and reference previews keep the full frame rather than cropping hair and shoulders", () => {
  const css = readFileSync(new URL("../src/style.css", import.meta.url), "utf8");
  assert.match(css, /\.video-card video\s*\{[^}]*object-fit: contain/);
  assert.match(css, /\.reference-review img\s*\{[^}]*object-fit: contain/);
});
