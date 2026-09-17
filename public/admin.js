"use strict";
(() => {
  const el = id => document.getElementById(id);
  let key = "", offset = 0, total = 0, controller = null, generation = 0;
  const limit = 50;
  const time = value => value == null ? "—" : new Date(value * 1000).toLocaleString();
  const duration = value => { const seconds = Math.floor(value || 0); return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m ${seconds % 60}s`; };
  function message(text, error = false) { el("message").textContent = text; el("message").dataset.error = String(error); }
  function cell(row, text) { const td = document.createElement("td"); td.textContent = text; row.append(td); return td; }
  function small(parent, text) { const span = document.createElement("span"); span.className = "small"; span.textContent = text; parent.append(span); }
  function lock() {
    ++generation; controller?.abort(); key = ""; el("adminKey").value = "";
    el("records").replaceChildren(); el("dashboard").hidden = true; el("loginPanel").hidden = false;
    for (const id of ["count", "duration", "users"]) el(id).textContent = "—";
    message("History locked. Enter the administrator key to load records.");
  }
  async function load() {
    const own = ++generation;
    controller?.abort(); controller = new AbortController();
    const query = new URLSearchParams({ owner: el("owner").value.trim(), platform: el("platform").value, offset: String(offset), limit: String(limit) });
    for (const name of ["after", "before"]) if (el(name).value) {
      const date = new Date(el(name).value + "T00:00:00");
      if (name === "before") date.setDate(date.getDate() + 1);
      query.set(name, String(date.getTime() / 1000));
    }
    message("Loading usage records…");
    try {
      const response = await fetch("/api/admin/usage?" + query, { headers: { Authorization: "Bearer " + key }, cache: "no-store", signal: controller.signal });
      const data = await response.json().catch(() => ({}));
      if (own !== generation) return;
      if (!response.ok) throw new Error(data.detail || `History unavailable (${response.status}).`);
      total = data.total;
      el("adminKey").value = ""; el("loginPanel").hidden = true; el("dashboard").hidden = false;
      el("count").textContent = total.toLocaleString(); el("duration").textContent = "≈ " + duration(data.seconds); el("users").textContent = data.users.toLocaleString();
      el("records").replaceChildren();
      for (const item of data.items) {
        const row = document.createElement("tr");
        const requested = cell(row, time(item.started ?? item.created)); small(requested, "Requested: " + time(item.created));
        cell(row, item.owner); const platform = cell(row, item.platform); small(platform, `${item.source} · ${item.client_version}`);
        const avatars = cell(row, "");
        for (const avatar of item.avatars) { const span = document.createElement("span"); span.className = "avatar-line"; span.textContent = `${avatar.name} · ≈ ${duration(avatar.seconds)}`; avatars.append(span); }
        const active = cell(row, "≈ " + duration(item.seconds)); if (item.has_gaps) small(active, "Missing reporting intervals excluded");
        const state = cell(row, item.state); small(state, item.ended ? `Ended: ${time(item.ended)} (${item.reason || "stopped"})` : `Last report: ${time(item.last_seen)}`);
        el("records").append(row);
      }
      el("empty").hidden = data.items.length !== 0;
      el("page").textContent = total ? `${offset + 1}–${Math.min(offset + limit, total)} of ${total}` : "0 sessions";
      el("previous").disabled = offset === 0; el("next").disabled = offset + limit >= total;
      message("Updated " + time(data.asOf) + ". Refresh to see new reports.");
    } catch (error) { if (own === generation) message(error.message || "History unavailable.", true); }
  }
  el("login").addEventListener("submit", event => { event.preventDefault(); key = el("adminKey").value.trim(); offset = 0; void load(); });
  el("filters").addEventListener("submit", event => { event.preventDefault(); offset = 0; void load(); });
  el("previous").addEventListener("click", () => { offset = Math.max(0, offset - limit); void load(); });
  el("next").addEventListener("click", () => { if (offset + limit < total) { offset += limit; void load(); } });
  el("logout").addEventListener("click", lock);
  window.addEventListener("pagehide", lock);
})();
