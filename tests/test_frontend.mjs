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
  const track = { stopped: false, listeners: {}, stop() { this.stopped = true; },
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
    files = []; dataset = {}; listeners = {}; srcObject = null;
    addEventListener(name, cb) { this.listeners[name] = cb; }
    replaceChildren() {} add() {}
    play() { return Promise.resolve(); }
    requestFullscreen() { return Promise.resolve(); }
  }
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  el("modelSelect").value = "lucy-2.1";
  el("referenceInput").files = [{ type: "image/png", size: 1024 }];
  el("accessKey").value = "fake-test-access-key-12345678901234567890";
  el("consent").checked = true;
  const raw = media();
  const remote = media();
  const calls = { fetch: [], decart: [], updates: [], disconnected: 0 };
  const connection = {
    disconnect() { calls.disconnected++; },
    on() {},
    async set(state) { calls.updates.push(state); },
  };
  const hooks = {
    camera: async () => raw,
    fetch: async () => ({ ok: true, json: async () => ({ apiKey: "short-lived-test-token" }) }),
    connect: async (_stream, options) => { options.onRemoteStream(remote); return connection; },
  };
  const sdk = {
    models: { realtime: () => ({ width: 512, height: 512, fps: 25 }) },
    resolveFpsNumber: () => 25,
    createDecartClient: () => ({ realtime: { connect: (...args) => {
      calls.decart.push(args); return hooks.connect(...args);
    } } }),
  };
  const timers = new Set();
  const context = vm.createContext({
    exports: {},
    require: name => name === "@decartai/sdk" ? sdk : {},
    document: { querySelector: selector => el(selector.slice(1)) },
    navigator: { mediaDevices: {
      getUserMedia: (...args) => hooks.camera(...args),
      enumerateDevices: async () => [],
    } },
    window: {
      addEventListener() {},
      setTimeout(cb, ms) { const t = setTimeout(cb, ms); timers.add(t); return t; },
      clearTimeout(t) { clearTimeout(t); timers.delete(t); },
    },
    Option: class {},
    AbortController,
    Error,
    fetch: (...args) => { calls.fetch.push(args); return hooks.fetch(...args); },
  });
  vm.runInContext(compiled, context);
  return {
    el, raw, remote, calls, hooks, connection,
    click: async id => { el(id).listeners.click(); await flush(); },
    cleanup: () => { vm.runInContext("stop()", context); for (const t of timers) clearTimeout(t); },
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
  assert.match(html, /Decart, our AI processing provider/);
  assert.match(html, /AI-generated video/);
});

test("built-in prompt and enhancement apply to both connection and reference updates", async () => {
  const h = harness();
  try {
    await h.click("cameraButton");
    await h.click("connectButton");
    const initial = h.calls.decart[0][1].initialState.prompt;
    assert.match(initial.text, /^Create a photorealistic professional webcam video/);
    assert.match(initial.text, /consented reference person/);
    assert.equal(initial.enhance, true);
    await h.click("updateButton");
    assert.equal(h.calls.updates.length, 1);
    assert.equal(h.calls.updates[0].prompt, initial.text);
    assert.equal(h.calls.updates[0].enhance, true);
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
