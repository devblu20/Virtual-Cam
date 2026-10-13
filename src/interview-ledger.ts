import "./interview-ledger.css";

type Interview = { id: string; candidateName: string; interviewDate: string; startTime: string; duration: number; photoName: string; resumeName: string; createdAt: number; thumbnail: string };
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
const photoInput = get<HTMLInputElement>("ledgerPhoto");
const resumeInput = get<HTMLInputElement>("ledgerResume");
const consentInput = get<HTMLInputElement>("ledgerConsent");
const submitButton = get<HTMLButtonElement>("submitInterviewButton");
let credentials = "";
let interviews: Interview[] = [];
let editing: Interview | null = null;
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
  const shown = interviews.filter(item => item.candidateName.toLowerCase().includes(query));
  rows.replaceChildren();
  shown.forEach((item, index) => {
    const row = document.createElement("tr");
    row.innerHTML = `<td class="row-number">${index + 1}</td><td><div class="candidate-cell"><span class="face-photo" title="${safe(item.photoName)}"><img src="${item.thumbnail}" alt="${safe(item.candidateName)} candidate photo" /></span><strong class="candidate-name">${safe(item.candidateName)}</strong></div></td><td>${dateLabel(item.interviewDate)}</td><td>${timeLabel(item.startTime)}</td><td>${durationLabel(item.duration)}</td><td><div class="resume-cell"><span title="${safe(item.resumeName)}">${safe(item.resumeName)}</span><button class="download" type="button">↓ Download résumé</button></div></td><td><div class="row-actions"><button class="edit" type="button">Edit details</button><button class="delete" type="button">Delete</button></div></td>`;
    row.querySelector<HTMLButtonElement>(".download")!.onclick = () => downloadResume(item);
    row.querySelector<HTMLButtonElement>(".edit")!.onclick = () => openDialog(item);
    row.querySelector<HTMLButtonElement>(".delete")!.onclick = () => removeInterview(item);
    rows.append(row);
  });
  emptyState.hidden = interviews.length > 0;
  get("totalCount").textContent = String(interviews.length);
  get("upcomingCount").textContent = String(interviews.filter(item => item.interviewDate >= today).length);
  get("totalMinutes").textContent = `${interviews.reduce((sum, item) => sum + item.duration, 0)} min`;
}

async function load() {
  sheetMessage.textContent = "Refreshing interview data…";
  try {
    const response = await request("/api/interviews");
    interviews = ((await response.json()) as { interviews: Interview[] }).interviews;
    sheetMessage.textContent = interviews.length ? `${interviews.length} record${interviews.length === 1 ? "" : "s"} ready.` : "";
    render();
  } catch (error) {
    sheetMessage.textContent = error instanceof Error ? error.message : "Could not load interviews.";
  }
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
  }
  dialog.showModal();
  nameInput.focus();
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
      duration,
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
  credentials = basic(id, password);
  const message = get("loginMessage"); message.textContent = "Opening workspace…";
  try { await load(); loginView.hidden = true; ledgerView.hidden = false; message.textContent = ""; }
  catch (error) { credentials = ""; message.textContent = error instanceof Error ? error.message : "Login failed."; }
};
get("logoutButton").onclick = () => { credentials = ""; interviews = []; ledgerView.hidden = true; loginView.hidden = false; get<HTMLInputElement>("portalPassword").value = ""; };
get("newInterviewButton").onclick = () => openDialog();
get("emptyAddButton").onclick = () => openDialog();
get("closeDialogButton").onclick = () => dialog.close();
get("cancelDialogButton").onclick = () => dialog.close();
get("refreshButton").onclick = load;
get("excelButton").onclick = downloadExcel;
get<HTMLInputElement>("searchInput").oninput = render;
durationInput.onchange = () => {
  const custom = durationInput.value === "custom";
  customDurationLabel.hidden = !custom;
  customDurationInput.required = custom;
  if (custom) customDurationInput.focus();
  else customDurationInput.value = "";
};
form.addEventListener("submit", save);
