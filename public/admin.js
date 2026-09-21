"use strict";
(() => {
  const el = id => document.getElementById(id);
  const platformNames = { website: "Website preview", meet: "Google Meet", zoom: "Zoom Web", teams: "Microsoft Teams Web" };
  const reasons = { stopped: "Stopped by client", closed: "Tab / session closed", replaced: "New session started",
    disconnected: "Connection ended", failed: "Client reported a failure", provider_timeout: "AI service timed out", provider_error: "AI service connection failed" };
  let key = "", offset = 0, total = 0, controller = null, generation = 0, renderedOffset = 0;
  let applied = { owner: "", platform: "", after: "", before: "" };
  const limit = 50;
  const time = value => value == null ? "Not reported" : new Date(value * 1000).toLocaleString();
  const date = value => new Date(value * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  const clock = value => new Date(value * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  function duration(value) {
    const seconds = Math.max(0, Math.floor(value || 0));
    if (!seconds) return "0 sec";
    const units = [];
    if (seconds >= 3600) units.push(Math.floor(seconds / 3600) + " hr");
    if (seconds % 3600 >= 60) units.push(Math.floor(seconds % 3600 / 60) + " min");
    if (seconds % 60) units.push(seconds % 60 + " sec");
    return units.join(" ");
  }
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  }
  function message(text, error = false) { el("message").textContent = text; el("message").dataset.error = String(error); }
  function cell(row, label) { const td = node("td"); td.dataset.label = label; row.append(td); return td; }
  function small(parent, text) { parent.append(node("span", "small", text)); }
  function status(item) {
    if (item.state === "failed") return ["Connection failed", "error", reasons[item.reason] || "Could not start the connection"];
    if (item.state === "ended") {
      if (item.started == null) return ["No video confirmed", "muted", "Ended before video activity was reported"];
      if (item.reason === "failed" || item.reason === "disconnected") return ["Connection ended", "warning", reasons[item.reason]];
      return ["Finished", "success", reasons[item.reason] || "Session ended"];
    }
    if (item.state === "interrupted") return ["Reports stopped", "warning", "No recent reports; disconnect not confirmed"];
    if (item.state === "unconfirmed") return ["No video confirmed", "muted", "No video activity report received"];
    if (item.state === "active") return ["Activity reported", "active", "Recent client activity reports"];
    if (item.state === "connecting") return ["Connecting", "muted", "Waiting for a video activity report"];
    return ["Unknown status", "muted", "Refresh to check the latest status"];
  }
  function lock() {
    ++generation; controller?.abort(); key = ""; el("adminKey").value = "";
    el("records").replaceChildren(); el("dashboard").hidden = true; el("loginPanel").hidden = false;
    el("logout").hidden = true; el("refresh").hidden = true;
    for (const id of ["count", "duration", "users"]) el(id).textContent = "—";
    el("recordCount").textContent = "0";
    message("Dashboard locked. Enter your administrator key to view usage.");
  }
  function renderRow(item) {
    const row = node("tr");
    const requested = cell(row, "Session");
    requested.append(node("span", "cell-title", date(item.started ?? item.created)));
    small(requested, clock(item.started ?? item.created) + (item.started == null ? " · Requested" : " · Activity started"));
    const details = node("details", "row-details");
    details.append(node("summary", "", "Session details"));
    for (const [label, value] of [
      ["Requested", time(item.created)], ["First activity report", time(item.started)],
      ["Ended", time(item.ended)], ["Last report", time(item.last_seen)],
      ["Client", (item.source === "extension" ? "Browser extension" : "Website") + " · " + item.client_version],
      ["Session ID", item.id || "Not reported"],
    ]) {
      const entry = node("p"); entry.append(node("strong", "", label), node("span", "", value)); details.append(entry);
    }
    requested.append(details);
    const person = cell(row, "Name / account");
    person.append(node("span", "cell-title", item.participant_name || "Name not provided"));
    small(person, item.participant_name ? "Self-reported · not verified" : "Older client or website session");
    person.append(node("span", "account-label", item.owner));
    cell(row, "Platform").append(node("span", "cell-title", platformNames[item.platform] || "Unknown platform"));
    const avatars = cell(row, "Avatar");
    const avatarList = item.avatars || [];
    function avatarLine(avatar) {
      const line = node("span", "avatar-line");
      line.append(node("span", "avatar-name", avatar.name));
      if (avatarList.length > 1) small(line, duration(avatar.seconds) + " measured");
      return line;
    }
    if (!avatarList.length) small(avatars, "No avatar recorded");
    else {
      avatars.append(avatarLine(avatarList[0]));
      if (avatarList.length > 1) {
        const changes = node("details", "row-details");
        changes.append(node("summary", "", "+" + (avatarList.length - 1) + " avatar selection" + (avatarList.length > 2 ? "s" : "")));
        for (const avatar of avatarList.slice(1)) changes.append(avatarLine(avatar));
        avatars.append(changes);
      }
    }
    const active = cell(row, "Video time");
    active.append(node("span", item.seconds > 0 ? "duration-value" : "no-duration", item.seconds > 0 ? duration(item.seconds) : "No measured time"));
    if (item.seconds > 0) small(active, "Estimated");
    else if (item.started != null) small(active, "Activity reported; no timed interval");
    if (item.has_gaps) small(active, "Reporting gaps excluded");
    const state = cell(row, "Status");
    const [label, tone, explanation] = status(item);
    const badge = node("span", "status-badge", label); badge.dataset.tone = tone;
    state.append(badge); small(state, explanation);
    return row;
  }
  async function load() {
    const own = ++generation;
    controller?.abort(); controller = new AbortController();
    const selection = { ...applied };
    const query = new URLSearchParams({ owner: selection.owner, platform: selection.platform, offset: String(offset), limit: String(limit) });
    for (const name of ["after", "before"]) if (selection[name]) {
      const boundary = new Date(selection[name] + "T00:00:00");
      if (name === "before") boundary.setDate(boundary.getDate() + 1);
      query.set(name, String(boundary.getTime() / 1000));
    }
    el("previous").disabled = true; el("next").disabled = true;
    message("Loading session history…");
    try {
      const response = await fetch("/api/admin/usage?" + query, { headers: { Authorization: "Bearer " + key }, cache: "no-store", signal: controller.signal });
      const data = await response.json().catch(() => ({}));
      if (own !== generation) return;
      if (!response.ok) {
        if ([401, 403].includes(response.status)) lock();
        message(data.detail || `History unavailable (${response.status}). Previously loaded results, if any, have not been refreshed.`, true);
        return;
      }
      total = data.total;
      if (total && offset >= total) { offset = 0; return load(); }
      renderedOffset = offset;
      el("adminKey").value = ""; el("loginPanel").hidden = true; el("dashboard").hidden = false;
      el("logout").hidden = false; el("refresh").hidden = false;
      el("count").textContent = total.toLocaleString(); el("duration").textContent = duration(data.seconds); el("users").textContent = data.users.toLocaleString();
      el("recordCount").textContent = total.toLocaleString();
      const scope = [selection.owner ? "Account: " + selection.owner : "All accounts",
        selection.platform ? platformNames[selection.platform] : "All platforms",
        selection.after || selection.before ? (selection.after || "Beginning") + " → " + (selection.before || "Latest") : "All dates"];
      el("scope").textContent = scope.join(" · ") + " — totals include every matching page.";
      el("records").replaceChildren(...data.items.map(renderRow));
      el("empty").hidden = data.items.length !== 0;
      const filtered = Object.values(selection).some(Boolean);
      el("emptyTitle").textContent = filtered ? "No matching sessions" : "No activity yet";
      el("emptyDescription").textContent = filtered
        ? "Try a different account or date range, or clear the filters to see all recorded activity."
        : "Start a transformation on the website or in extension 0.3.12 or later, then refresh this page.";
      el("page").textContent = total ? `Showing ${offset + 1}–${Math.min(offset + limit, total)} of ${total.toLocaleString()} session attempts` : "0 session attempts";
      message("Last refreshed " + time(data.asOf));
    } catch (error) {
      if (own === generation) message((error.message || "History unavailable.") + " Previously loaded results, if any, have not been refreshed.", true);
    } finally {
      if (own === generation) {
        offset = renderedOffset;
        el("previous").disabled = offset === 0;
        el("next").disabled = offset + limit >= total;
      }
    }
  }
  function applyFilters() {
    const next = Object.fromEntries(["owner", "platform", "after", "before"].map(id => [id, el(id).value.trim()]));
    if (next.after && next.before && next.after > next.before) { message("Choose a From date on or before the Through date.", true); return; }
    applied = next; offset = 0; void load();
  }
  function localDate(value) { return value.getFullYear() + "-" + String(value.getMonth() + 1).padStart(2, "0") + "-" + String(value.getDate()).padStart(2, "0"); }
  function range(days) {
    const today = new Date(), first = new Date();
    first.setDate(today.getDate() - days + 1);
    el("after").value = localDate(first); el("before").value = localDate(today); applyFilters();
  }
  el("timeZone").textContent = "Local timezone: " + Intl.DateTimeFormat().resolvedOptions().timeZone;
  el("login").addEventListener("submit", event => { event.preventDefault(); key = el("adminKey").value.trim(); offset = 0; void load(); });
  el("filters").addEventListener("submit", event => { event.preventDefault(); applyFilters(); });
  el("refresh").addEventListener("click", () => void load());
  el("resetFilters").addEventListener("click", () => { for (const id of ["owner", "platform", "after", "before"]) el(id).value = ""; applyFilters(); });
  el("rangeToday").addEventListener("click", () => range(1));
  el("rangeWeek").addEventListener("click", () => range(7));
  el("rangeAll").addEventListener("click", () => { el("after").value = ""; el("before").value = ""; applyFilters(); });
  el("previous").addEventListener("click", () => { offset = Math.max(0, offset - limit); void load(); });
  el("next").addEventListener("click", () => { if (offset + limit < total) { offset += limit; void load(); } });
  el("logout").addEventListener("click", lock);
  window.addEventListener("pagehide", lock);
})();
