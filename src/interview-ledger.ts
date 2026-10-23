import "./interview-ledger.css";

type InterviewStatus = "Scheduled" | "Completed" | "Selected" | "Rejected" | "Cancelled";
type Interview = { id: string; candidateName: string; interviewDate: string; startTime: string; duration: number; photoName: string; resumeName: string; createdAt: number; thumbnail: string; status: InterviewStatus; notes: string; rating: number };
type ManagedUser = { id: string; name: string };
type AccessEntry = { email: string; role: "workspace" | "admin" | "both"; owner: boolean; active: boolean; createdAt: number };
type GoogleCredentialResponse = { credential?: string };
type GoogleIdentity = {
  accounts: { id: {
    initialize: (options: { client_id: string; callback: (response: GoogleCredentialResponse) => void; auto_select?: boolean }) => void;
    renderButton: (element: HTMLElement, options: Record<string, unknown>) => void;
    disableAutoSelect: () => void;
  } };
};
declare global { interface Window { google?: GoogleIdentity; } }
const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const loginView = get<HTMLElement>("loginView");
const ledgerView = get<HTMLElement>("ledgerView");
const rows = get<HTMLTableSectionElement>("ledgerRows");
const emptyState = get<HTMLElement>("emptyState");
const sheetMessage = get<HTMLElement>("sheetMessage");
const dialog = get<HTMLDialogElement>("interviewDialog");
const form = get<HTMLFormElement>("ledgerForm");
const nameInput = get<HTMLInputElement>("ledgerName");
const dateInput = get<HTMLInputElement>("ledgerDate");
const timeInput = get<HTMLInputElement>("ledgerTime");
const durationInput = get<HTMLSelectElement>("ledgerDuration");
const customDurationLabel = get<HTMLLabelElement>("customDurationLabel");
const customDurationInput = get<HTMLInputElement>("customDuration");
const statusInput = get<HTMLSelectElement>("ledgerStatus");
const ratingInput = get<HTMLSelectElement>("ledgerRating");
const notesInput = get<HTMLTextAreaElement>("ledgerNotes");
const photoInput = get<HTMLInputElement>("ledgerPhoto");
const resumeInput = get<HTMLInputElement>("ledgerResume");
const consentInput = get<HTMLInputElement>("ledgerConsent");
const submitButton = get<HTMLButtonElement>("submitInterviewButton");
const candidateDialog = get<HTMLDialogElement>("candidateDialog");
const adminDashboard = document.body.hasAttribute("data-admin-dashboard");
let credentials = "";
let interviews: Interview[] = [];
let managedUsers: ManagedUser[] = [];
let accessEntries: AccessEntry[] = [];
let editing: Interview | null = null;
let geminiInterview: Interview | null = null;
let geminiSourceUrl = "";
let geminiOutputUrl = "";
let geminiOutputBlob: Blob | null = null;
const presetDurations = new Set([15, 30, 45, 60, 90, 120, 150, 180, 240]);

const current = new Date();
const today = new Date(current.getTime() - current.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
dateInput.min = today;

function basic(id: string, password: string) {
  const bytes = new TextEncoder().encode(`${id}:${password}`);
  let binary = "";
  bytes.forEach(byte => binary += String.fromCharCode(byte));
  return `Basic ${btoa(binary)}`;
}

async function openWorkspace(nextCredentials: string) {
  const message = get("loginMessage");
  credentials = nextCredentials;
  message.textContent = "Opening workspace…";
  try {
    const panel = adminDashboard ? "admin" : "workspace";
    const session = await fetch(`/api/interview-admin/session?panel=${panel}`, {
      method: "POST",
      headers: { Authorization: credentials },
    });
    if (!session.ok) {
      let detail = "Login failed.";
      try { detail = (await session.json()).detail || detail; } catch { /* Safe fallback. */ }
      throw new Error(detail);
    }
    credentials = "";
    await load();
    loginView.hidden = true;
    ledgerView.hidden = false;
    message.textContent = "";
  } catch (error) {
    credentials = "";
    message.textContent = error instanceof Error ? error.message : "Login failed.";
  }
}

async function restoreWorkspace() {
  const panel = adminDashboard ? "admin" : "workspace";
  try {
    const response = await fetch(`/api/interview-admin/session?panel=${panel}`, { cache: "no-store" });
    if (!response.ok) return;
    await load();
    loginView.hidden = true;
    ledgerView.hidden = false;
  } catch { /* Keep the sign-in view available. */ }
  finally { document.body.classList.remove("auth-checking"); }
}

async function setupGoogleSignIn() {
  const container = get<HTMLElement>("googleLogin");
  try {
    const response = await fetch("/api/interview-admin/google-config", { cache: "no-store" });
    const config = await response.json() as { clientId?: string };
    if (!response.ok || !config.clientId) return;
    for (let attempt = 0; attempt < 50 && !window.google; attempt += 1) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!window.google) return;
    window.google.accounts.id.initialize({
      client_id: config.clientId,
      auto_select: false,
      callback: googleResponse => {
        if (!googleResponse.credential) {
          get("loginMessage").textContent = "Google sign-in did not return a valid account.";
          return;
        }
        void openWorkspace(`Google ${googleResponse.credential}`);
      },
    });
    window.google.accounts.id.renderButton(get("googleSignIn"), {
      type: "standard", theme: "outline", size: "large", text: "signin_with",
      shape: "rectangular", logo_alignment: "left", width: 340,
    });
    container.hidden = false;
  } catch { /* Keep the existing password login available if Google is unavailable. */ }
}

async function request(path: string, options: RequestInit = {}) {
  const response = await fetch(path, { ...options, headers: { Authorization: credentials, ...(options.headers || {}) } });
  if (!response.ok) {
    let message = "Request failed. Please try again.";
    try { message = (await response.json()).detail || message; } catch { /* Safe fallback. */ }
    throw new Error(message);
  }
  return response;
}

function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric" }).format(new Date(`${value}T12:00:00`));
}

function timeLabel(value: string) {
  const [hours, minutes] = value.split(":").map(Number);
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit" }).format(new Date(2000, 0, 1, hours, minutes));
}

function durationLabel(minutes: number) {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hr${hours > 1 ? "s" : ""}${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}

function safe(value: string) {
  const span = document.createElement("span"); span.textContent = value; return span.innerHTML;
}

function render() {
  const query = get<HTMLInputElement>("searchInput").value.trim().toLowerCase();
  const status = get<HTMLSelectElement>("statusFilter").value;
  const filterDate = get<HTMLInputElement>("dateFilter").value;
  const shown = interviews.filter(item => item.candidateName.toLowerCase().includes(query) && (!status || item.status === status) && (!filterDate || item.interviewDate === filterDate));
  rows.replaceChildren();
  shown.forEach((item, index) => {
    const row = document.createElement("tr");
    const assignment = adminDashboard
      ? `<td><div class="assignment-control"><select class="user-assignment" aria-label="Send ${safe(item.candidateName)} photo to user"><option value="">Choose user…</option>${managedUsers.map(user => `<option value="${safe(user.id)}">${safe(user.name)}</option>`).join("")}</select><span class="assignment-status" aria-live="polite"></span></div></td>`
      : "";
    const geminiAction = adminDashboard ? '<button class="gemini-edit" type="button">Gemini edit</button>' : "";
    row.innerHTML = `<td class="row-number">${index + 1}</td><td><button class="candidate-cell view" type="button"><span class="face-photo" title="${safe(item.photoName)}"><img src="${item.thumbnail}" alt="${safe(item.candidateName)} candidate photo" /></span><span><strong class="candidate-name">${safe(item.candidateName)}</strong><small>View profile</small></span></button></td><td><span class="status-badge status-${item.status.toLowerCase()}">${safe(item.status)}</span></td><td>${dateLabel(item.interviewDate)}</td><td>${timeLabel(item.startTime)}</td><td>${durationLabel(item.duration)}</td><td><div class="resume-cell"><span title="${safe(item.resumeName)}">${safe(item.resumeName)}</span><button class="download" type="button">↓ Download résumé</button></div></td>${assignment}<td><div class="row-actions">${geminiAction}<button class="edit" type="button">Edit details</button><button class="delete" type="button">Delete</button></div></td>`;
    row.querySelector<HTMLButtonElement>(".view")!.onclick = () => openCandidate(item);
    row.querySelector<HTMLButtonElement>(".download")!.onclick = () => downloadResume(item);
    row.querySelector<HTMLButtonElement>(".edit")!.onclick = () => openDialog(item);
    row.querySelector<HTMLButtonElement>(".delete")!.onclick = () => removeInterview(item);
    row.querySelector<HTMLButtonElement>(".gemini-edit")?.addEventListener("click", () => { void openGeminiPhotoEditor(item); });
    const userAssignment = row.querySelector<HTMLSelectElement>(".user-assignment");
    if (userAssignment) userAssignment.onchange = () => sendPhotoToUser(item, userAssignment);
    rows.append(row);
  });
  emptyState.hidden = interviews.length > 0;
  if (interviews.length && !shown.length) sheetMessage.textContent = "No candidates match these filters.";
  get("totalCount").textContent = String(interviews.length);
  get("upcomingCount").textContent = String(interviews.filter(item => item.status === "Scheduled" && item.interviewDate >= today).length);
  get("totalMinutes").textContent = `${interviews.reduce((sum, item) => sum + item.duration, 0)} min`;
}

async function load() {
  sheetMessage.textContent = "Refreshing interview data…";
  try {
    const interviewResponse = await request("/api/interviews");
    interviews = ((await interviewResponse.json()) as { interviews: Interview[] }).interviews;
    if (adminDashboard) {
      const userResponse = await request("/api/interview-admin/users");
      managedUsers = ((await userResponse.json()) as { users: ManagedUser[] }).users;
      await loadAccessManagement();
    }
    sheetMessage.textContent = interviews.length ? `${interviews.length} record${interviews.length === 1 ? "" : "s"} ready.` : "";
    render();
  } catch (error) {
    sheetMessage.textContent = error instanceof Error ? error.message : "Could not load interviews.";
    throw error;
  }
}

function accessRoleLabel(role: AccessEntry["role"]) {
  return role === "both" ? "Both panels" : role === "admin" ? "Admin Panel" : "Private Workspace";
}

function renderAccess() {
  const list = document.getElementById("accessList");
  if (!list) return;
  list.replaceChildren();
  accessEntries.forEach(entry => {
    const row = document.createElement("article");
    row.className = `access-row${entry.active ? "" : " revoked"}`;
    row.innerHTML = `<div><strong>${safe(entry.email)}</strong><span>${entry.owner ? "Owner · " : ""}${accessRoleLabel(entry.role)}</span></div><span class="access-state">${entry.active ? "Active" : "Revoked"}</span>${entry.owner ? '<span class="owner-lock">Protected</span>' : entry.active ? '<button class="revoke-access" type="button">Revoke</button>' : '<span class="owner-lock">Add again to restore</span>'}`;
    row.querySelector<HTMLButtonElement>(".revoke-access")?.addEventListener("click", () => { void revokeAccess(entry); });
    list.append(row);
  });
}

async function loadAccessManagement() {
  const button = document.getElementById("manageAccessButton") as HTMLButtonElement | null;
  try {
    const response = await fetch("/api/interview-admin/access", { headers: { Authorization: credentials }, cache: "no-store" });
    if (response.status === 403) {
      if (button) button.hidden = true;
      return;
    }
    if (!response.ok) throw new Error("Access list could not be loaded.");
    accessEntries = ((await response.json()) as { access: AccessEntry[] }).access;
    if (button) button.hidden = false;
    renderAccess();
  } catch {
    if (button) button.hidden = true;
  }
}

async function saveAccess(event: SubmitEvent) {
  event.preventDefault();
  const email = get<HTMLInputElement>("accessEmail").value.trim().toLowerCase();
  const role = get<HTMLSelectElement>("accessRole").value;
  const button = get<HTMLButtonElement>("saveAccessButton");
  const message = get("accessMessage");
  button.disabled = true;
  message.textContent = "Saving access…";
  try {
    await request("/api/interview-admin/access", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, role }),
    });
    get<HTMLFormElement>("accessForm").reset();
    await loadAccessManagement();
    message.textContent = `Access saved for ${email}.`;
  } catch (error) {
    message.textContent = error instanceof Error ? error.message : "Access could not be saved.";
  } finally { button.disabled = false; }
}

async function revokeAccess(entry: AccessEntry) {
  const message = get("accessMessage");
  message.textContent = `Revoking ${entry.email}…`;
  try {
    await request(`/api/interview-admin/access/${encodeURIComponent(entry.email)}`, { method: "DELETE" });
    await loadAccessManagement();
    message.textContent = `Access revoked for ${entry.email}.`;
  } catch (error) {
    message.textContent = error instanceof Error ? error.message : "Access could not be revoked.";
  }
}

async function sendPhotoToUser(item: Interview, select: HTMLSelectElement) {
  const owner = select.value;
  if (!owner) return;
  const userName = select.selectedOptions[0]?.textContent || "selected user";
  const status = select.parentElement?.querySelector<HTMLElement>(".assignment-status");
  select.disabled = true;
  if (status) { status.className = "assignment-status pending"; status.textContent = `Sharing with ${userName}…`; }
  sheetMessage.textContent = `Sending ${item.candidateName}'s photo to ${userName}…`;
  try {
    await request(`/api/interview-admin/interviews/${encodeURIComponent(item.id)}/photo`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner }),
    });
    if (status) { status.className = "assignment-status success"; status.textContent = `✓ Shared with ${userName}`; }
    sheetMessage.textContent = `${item.candidateName}'s photo saved to ${userName}'s Photo Library.`;
  } catch (error) {
    const message = error instanceof Error ? error.message : "Photo could not be saved to this user.";
    select.value = "";
    if (status) { status.className = "assignment-status error"; status.textContent = message; }
    sheetMessage.textContent = message;
  } finally {
    select.disabled = false;
  }
}

function clearGeminiPhotoEditor() {
  if (geminiSourceUrl) URL.revokeObjectURL(geminiSourceUrl);
  if (geminiOutputUrl) URL.revokeObjectURL(geminiOutputUrl);
  geminiSourceUrl = "";
  geminiOutputUrl = "";
  geminiOutputBlob = null;
  get<HTMLImageElement>("geminiPhotoOriginal").removeAttribute("src");
  get<HTMLImageElement>("geminiPhotoOutput").removeAttribute("src");
  get("geminiPhotoPlaceholder").hidden = false;
  get<HTMLButtonElement>("saveGeminiPhotoButton").hidden = true;
}

async function openGeminiPhotoEditor(item: Interview) {
  geminiInterview = item;
  clearGeminiPhotoEditor();
  get("geminiCandidateLabel").textContent = `Edit ${item.candidateName}'s shirt, colour and framing before sharing the photo.`;
  get("geminiPhotoMessage").textContent = "Loading original candidate photo…";
  const generate = get<HTMLButtonElement>("generateGeminiPhotoButton");
  generate.disabled = true;
  get<HTMLDialogElement>("geminiPhotoDialog").showModal();
  try {
    const response = await request(`/api/interview-admin/interviews/${encodeURIComponent(item.id)}/photo`);
    if (geminiInterview?.id !== item.id) return;
    const source = await response.blob();
    geminiSourceUrl = URL.createObjectURL(source);
    get<HTMLImageElement>("geminiPhotoOriginal").src = geminiSourceUrl;
    get("geminiPhotoMessage").textContent = "Original photo ready. Choose the settings and generate.";
    generate.disabled = false;
  } catch (error) {
    get("geminiPhotoMessage").textContent = error instanceof Error ? error.message : "Candidate photo could not be loaded.";
  }
}

async function generateGeminiPhoto() {
  const item = geminiInterview;
  if (!item) return;
  const colour = get<HTMLInputElement>("geminiPhotoColour").value.trim();
  if (!colour) {
    get("geminiPhotoMessage").textContent = "Enter a shirt colour.";
    return;
  }
  const generate = get<HTMLButtonElement>("generateGeminiPhotoButton");
  const saveButton = get<HTMLButtonElement>("saveGeminiPhotoButton");
  generate.disabled = true;
  saveButton.hidden = true;
  get("geminiPhotoMessage").textContent = "Gemini is editing the clothing. Please wait…";
  const query = new URLSearchParams({
    color: colour,
    mode: get<HTMLSelectElement>("geminiPhotoMode").value,
    wrinkles: get<HTMLSelectElement>("geminiPhotoWrinkles").value,
    framing: get<HTMLSelectElement>("geminiPhotoFraming").value,
    aspect_ratio: get<HTMLSelectElement>("geminiPhotoRatio").value,
  });
  try {
    const response = await request(`/api/interview-admin/interviews/${encodeURIComponent(item.id)}/gemini-edit?${query}`, { method: "POST" });
    if (geminiInterview?.id !== item.id) return;
    geminiOutputBlob = await response.blob();
    if (geminiOutputUrl) URL.revokeObjectURL(geminiOutputUrl);
    geminiOutputUrl = URL.createObjectURL(geminiOutputBlob);
    get<HTMLImageElement>("geminiPhotoOutput").src = geminiOutputUrl;
    get("geminiPhotoPlaceholder").hidden = true;
    saveButton.hidden = false;
    get("geminiPhotoMessage").textContent = "Gemini edit ready. Review it, then save it as the candidate photo.";
  } catch (error) {
    get("geminiPhotoMessage").textContent = error instanceof Error ? error.message : "Gemini image editing failed.";
  } finally {
    if (geminiInterview?.id === item.id) generate.disabled = false;
  }
}

async function saveGeminiPhoto() {
  const item = geminiInterview;
  if (!item || !geminiOutputBlob) return;
  const saveButton = get<HTMLButtonElement>("saveGeminiPhotoButton");
  saveButton.disabled = true;
  get("geminiPhotoMessage").textContent = "Saving edited candidate photo…";
  try {
    await request(`/api/interview-admin/interviews/${encodeURIComponent(item.id)}/photo`, {
      method: "PUT",
      headers: { "Content-Type": geminiOutputBlob.type || "image/png" },
      body: geminiOutputBlob,
    });
    get<HTMLDialogElement>("geminiPhotoDialog").close();
    await load();
    sheetMessage.textContent = `${item.candidateName}'s Gemini-edited photo is now saved and ready to share.`;
  } catch (error) {
    get("geminiPhotoMessage").textContent = error instanceof Error ? error.message : "Edited photo could not be saved.";
  } finally { saveButton.disabled = false; }
}

function closeGeminiPhotoEditor() {
  get<HTMLDialogElement>("geminiPhotoDialog").close();
  geminiInterview = null;
  clearGeminiPhotoEditor();
}

function openDialog(item: Interview | null = null) {
  form.reset();
  customDurationLabel.hidden = true;
  customDurationInput.required = false;
  editing = item;
  get("dialogMessage").textContent = "";
  get("dialogKicker").textContent = item ? "UPDATE RECORD" : "NEW RECORD";
  get("dialogTitle").textContent = item ? "Update interview details" : "Add an interview";
  get("dialogDescription").textContent = item ? "Change the schedule or replace the saved photo and résumé." : "Candidate details will appear as a new row in your ledger.";
  submitButton.textContent = item ? "Update details" : "Save to ledger";
  photoInput.required = !item;
  resumeInput.required = !item;
  get("photoHelp").textContent = item ? `Current: ${item.photoName} · choose a file only to replace it` : "JPG, PNG or WebP · max 10 MB";
  get("resumeHelp").textContent = item ? `Current: ${item.resumeName} · choose a file only to replace it` : "PDF, DOC or DOCX · max 10 MB";
  if (item) {
    nameInput.value = item.candidateName;
    dateInput.value = item.interviewDate;
    timeInput.value = item.startTime;
    if (presetDurations.has(item.duration)) durationInput.value = String(item.duration);
    else {
      durationInput.value = "custom";
      customDurationInput.value = String(item.duration);
      customDurationLabel.hidden = false;
      customDurationInput.required = true;
    }
    consentInput.checked = true;
    statusInput.value = item.status;
    ratingInput.value = String(item.rating);
    notesInput.value = item.notes;
  }
  dialog.showModal();
  nameInput.focus();
}

function openCandidate(item: Interview) {
  get<HTMLImageElement>("candidatePhoto").src = item.thumbnail;
  get<HTMLImageElement>("candidatePhoto").alt = `${item.candidateName} candidate photo`;
  const status = get("candidateStatus");
  status.textContent = item.status;
  status.className = `status-badge status-${item.status.toLowerCase()}`;
  get("candidateName").textContent = item.candidateName;
  get("candidateSchedule").textContent = `${dateLabel(item.interviewDate)} · ${timeLabel(item.startTime)}`;
  get("candidateDuration").textContent = durationLabel(item.duration);
  get("candidateRating").textContent = item.rating ? `${"★".repeat(item.rating)} ${item.rating}/5` : "Not rated";
  get("candidateResume").textContent = item.resumeName;
  get("candidateNotes").textContent = item.notes || "No interview notes added yet.";
  get<HTMLButtonElement>("candidateResumeButton").onclick = () => downloadResume(item);
  get<HTMLButtonElement>("candidateEditButton").onclick = () => { candidateDialog.close(); openDialog(item); };
  candidateDialog.showModal();
}

async function toBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  return btoa(binary);
}

function mime(file: File, kind: "photo" | "resume") {
  if (file.type) return file.type;
  const extension = file.name.split(".").pop()?.toLowerCase() || "";
  const types: Record<string, string> = kind === "photo"
    ? { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp" }
    : { pdf: "application/pdf", doc: "application/msword", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
  return types[extension] || "";
}

async function save(event: SubmitEvent) {
  event.preventDefault();
  const photo = photoInput.files?.[0];
  const resume = resumeInput.files?.[0];
  const message = get("dialogMessage");
  const duration = durationInput.value === "custom" ? Number(customDurationInput.value) : Number(durationInput.value);
  if (!nameInput.value.trim() || !dateInput.value || !timeInput.value || !durationInput.value || !Number.isInteger(duration) || duration < 1 || duration > 720 || (!editing && (!photo || !resume)) || !consentInput.checked) {
    message.textContent = "Complete every field and confirm permission to store the files.";
    return;
  }
  if (dateInput.value < today || (photo && photo.size > 10 * 1024 * 1024) || (resume && resume.size > 10 * 1024 * 1024)) {
    message.textContent = "Use a current/future date and files no larger than 10 MB.";
    return;
  }
  submitButton.disabled = true;
  message.textContent = "Saving candidate and files…";
  try {
    const payload: Record<string, unknown> = {
      candidateName: nameInput.value.trim(), interviewDate: dateInput.value, startTime: timeInput.value,
      duration, status: statusInput.value, rating: Number(ratingInput.value), notes: notesInput.value.trim(),
    };
    if (photo) Object.assign(payload, { photoName: photo.name, photoMime: mime(photo, "photo"), photoData: await toBase64(photo) });
    if (resume) Object.assign(payload, { resumeName: resume.name, resumeMime: mime(resume, "resume"), resumeData: await toBase64(resume) });
    await request(editing ? `/api/interviews/${encodeURIComponent(editing.id)}` : "/api/interviews", {
      method: editing ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    dialog.close();
    await load();
    sheetMessage.textContent = editing ? "Interview details updated successfully." : "Interview saved successfully.";
    editing = null;
  } catch (error) {
    message.textContent = error instanceof Error ? error.message : "Could not save the interview.";
  } finally { submitButton.disabled = false; }
}

async function downloadResume(item: Interview) {
  try {
    const response = await request(`/api/interviews/${encodeURIComponent(item.id)}/resume`);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = item.resumeName; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { sheetMessage.textContent = error instanceof Error ? error.message : "Download failed."; }
}

async function removeInterview(item: Interview) {
  if (!confirm(`Delete ${item.candidateName}'s interview?`)) return;
  try { await request(`/api/interviews/${encodeURIComponent(item.id)}`, { method: "DELETE" }); await load(); }
  catch (error) { sheetMessage.textContent = error instanceof Error ? error.message : "Delete failed."; }
}

async function downloadExcel() {
  try {
    const response = await request("/api/interviews/export.xlsx");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a"); link.href = url; link.download = "bluqq-interviews.xlsx"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { sheetMessage.textContent = error instanceof Error ? error.message : "Excel export failed."; }
}

get<HTMLFormElement>("loginForm").onsubmit = async event => {
  event.preventDefault();
  const id = get<HTMLInputElement>("portalId").value.trim();
  const password = get<HTMLInputElement>("portalPassword").value;
  await openWorkspace(basic(id, password));
};
get("logoutButton").onclick = async () => {
  try { await fetch("/api/interview-admin/session/logout", { method: "POST" }); } catch { /* Clear the local view regardless. */ }
  credentials = ""; interviews = []; accessEntries = []; window.google?.accounts.id.disableAutoSelect();
  ledgerView.hidden = true; loginView.hidden = false; get<HTMLInputElement>("portalPassword").value = "";
  const accessDialog = document.getElementById("accessDialog") as HTMLDialogElement | null;
  if (accessDialog?.open) accessDialog.close();
};
get("newInterviewButton").onclick = () => openDialog();
get("emptyAddButton").onclick = () => openDialog();
get("closeDialogButton").onclick = () => dialog.close();
get("cancelDialogButton").onclick = () => dialog.close();
get("closeCandidateButton").onclick = () => candidateDialog.close();
get("refreshButton").onclick = () => { void load().catch(() => undefined); };
get("excelButton").onclick = downloadExcel;
get<HTMLInputElement>("searchInput").oninput = render;
get<HTMLSelectElement>("statusFilter").onchange = render;
get<HTMLInputElement>("dateFilter").onchange = render;
get("clearFiltersButton").onclick = () => {
  get<HTMLInputElement>("searchInput").value = "";
  get<HTMLSelectElement>("statusFilter").value = "";
  get<HTMLInputElement>("dateFilter").value = "";
  sheetMessage.textContent = interviews.length ? `${interviews.length} record${interviews.length === 1 ? "" : "s"} ready.` : "";
  render();
};
durationInput.onchange = () => {
  const custom = durationInput.value === "custom";
  customDurationLabel.hidden = !custom;
  customDurationInput.required = custom;
  if (custom) customDurationInput.focus();
  else customDurationInput.value = "";
};
form.addEventListener("submit", save);
if (adminDashboard) {
  get<HTMLButtonElement>("manageAccessButton").onclick = () => {
    get<HTMLDialogElement>("accessDialog").showModal();
  };
  get<HTMLButtonElement>("closeAccessButton").onclick = () => { get<HTMLDialogElement>("accessDialog").close(); };
  get<HTMLFormElement>("accessForm").addEventListener("submit", saveAccess);
  get<HTMLButtonElement>("closeGeminiPhotoButton").onclick = closeGeminiPhotoEditor;
  get<HTMLButtonElement>("cancelGeminiPhotoButton").onclick = closeGeminiPhotoEditor;
  get<HTMLButtonElement>("generateGeminiPhotoButton").onclick = () => { void generateGeminiPhoto(); };
  get<HTMLButtonElement>("saveGeminiPhotoButton").onclick = () => { void saveGeminiPhoto(); };
  get<HTMLInputElement>("geminiPhotoColourPicker").oninput = event => {
    const value = (event.target as HTMLInputElement).value;
    get<HTMLInputElement>("geminiPhotoColour").value = `${value} custom colour`;
    document.querySelectorAll("[data-gemini-colour]").forEach(button => button.classList.remove("active"));
  };
  document.querySelectorAll<HTMLButtonElement>("[data-gemini-colour]").forEach(button => {
    button.onclick = () => {
      document.querySelectorAll("[data-gemini-colour]").forEach(option => option.classList.toggle("active", option === button));
      get<HTMLInputElement>("geminiPhotoColourPicker").value = button.dataset.geminiColour || "#efb4be";
      get<HTMLInputElement>("geminiPhotoColour").value = button.dataset.geminiName || "soft blush pink";
    };
  });
  get<HTMLSelectElement>("geminiPhotoFraming").onchange = event => {
    get<HTMLLabelElement>("geminiPhotoRatioLabel").hidden = (event.target as HTMLSelectElement).value !== "auto";
  };
}
void setupGoogleSignIn();
void restoreWorkspace();
