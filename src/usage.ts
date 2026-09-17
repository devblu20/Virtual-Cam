// Usage metadata only. Never send video, meeting URLs, or a provider key here.
export type AvatarMetadata = { kind: "upload"; digest: string; name: string };
export async function referenceMetadata(file: File): Promise<AvatarMetadata> {
  const hash = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  const name = (file.name || "Uploaded reference").replace(/\\/g, "/").split("/").pop()!
    .replace(/[\x00-\x1f\x7f]/g, "").trim().slice(0, 80) || "Uploaded reference";
  return { kind: "upload", digest: Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, "0")).join(""), name };
}

export class UsageReporter {
  private id: string | null = null;
  private sequence = 0;
  private closed = false;
  private reason = "stopped";
  private lastPulse = -Infinity;
  private frame: number | null = null;
  private video: HTMLVideoElement | null = null;
  private queue: Promise<void> = Promise.resolve();
  constructor(private key: string, private notice: (message: string) => void) {}
  attach(id: unknown) {
    if (typeof id !== "string" || !/^[0-9a-f-]{36}$/i.test(id)) {
      if (!this.closed) this.notice("Usage history is unavailable for this session. Ask the administrator to check storage.");
      return;
    }
    this.id = id;
    if (this.closed) this.send({ action: "end", reason: this.reason }, true);
    else this.notice("Session usage is recorded for the administrator. Durations are estimates.");
  }
  private send(body: Record<string, unknown>, final = false) {
    if (!this.id) return;
    const data = JSON.stringify({ ...body, sequence: ++this.sequence });
    const url = `/api/usage/${this.id}/events`, key = this.key;
    const run = async () => {
      try {
        const response = await fetch(url, { method: "POST", headers: { Authorization: "Bearer " + key, "Content-Type": "application/json" },
          body: data, cache: "no-store", keepalive: final, signal: AbortSignal.timeout(8000) });
        if (!response.ok) throw new Error("Usage report unavailable");
      } catch {
        if (!this.closed) this.notice("Usage reporting interrupted; history may be incomplete. Video can continue.");
      }
    };
    // A page-close report must be dispatched immediately, not wait for a queue
    // which may disappear with the document. Its sequence closes older reports.
    if (final) void run();
    else this.queue = this.queue.then(run, run);
  }
  watch(video: HTMLVideoElement) {
    this.cancelFrame();
    if (this.closed) return;
    this.video = video;
    const tick = () => {
      if (this.closed || this.video !== video) return;
      if (performance.now() - this.lastPulse >= 20_000) {
        this.lastPulse = performance.now();
        this.send({ action: "pulse" });
      }
      this.frame = video.requestVideoFrameCallback(tick);
    };
    // Actual decoded frames, not just a connected WebRTC socket.
    if (typeof video.requestVideoFrameCallback === "function") this.frame = video.requestVideoFrameCallback(tick);
    else this.notice("This browser cannot report decoded-frame activity; duration will be unconfirmed.");
  }
  avatar(value: AvatarMetadata) {
    if (!this.closed) this.send({ action: "avatar", avatar: value });
  }
  private cancelFrame() {
    if (this.frame !== null) this.video?.cancelVideoFrameCallback(this.frame);
    this.frame = null;
    this.video = null;
  }
  stop(reason = "stopped") {
    if (this.closed) return;
    this.closed = true;
    this.reason = reason;
    this.cancelFrame();
    this.send({ action: "end", reason }, true);
  }
}
