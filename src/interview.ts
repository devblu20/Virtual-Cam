import "./interview.css";

type SavedInterview = {
  id: string; candidateName: string; interviewDate: string; startTime: string;
  duration: number; photoName: string; resumeName: string; createdAt: number; thumbnail: string;
};

const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const form = get<HTMLFormElement>("interviewForm");
const accessKey = get<HTMLInputElement>("accessKey");
const candidateName = get<HTMLInputElement>("candidateName");
const interviewDate = get<HTMLInputElement>("interviewDate");
const startTime = get<HTMLInputElement>("startTime");
const duration = get<HTMLSelectElement>("duration");
const photoInput = get<HTMLInputElement>("photoInput");
const resumeInput = get<HTMLInputElement>("resumeInput");
const storageConsent = get<HTMLInputElement>("storageConsent");
const photoPreview = get<HTMLElement>("photoPreview");
const formMessage = get<HTMLElement>("formMessage");
const savedMessage = get<HTMLElement>("savedMessage");
const savedGrid = get<HTMLElement>("savedGrid");
const saveButton = get<HTMLButtonElement>("saveButton");
let photoUrl = "";

const today = new Date();
const localToday = new Date(today.getTime() - today.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
interviewDate.min = localToday;

function formatDate(value: string) {
  if (!value) return "Not selected";
  return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", weekday: "short" })
    .format(new Date(`${value}T12:00:00`));
}

function formatTime(value: string) {
  if (!value) return "Not selected";
  const [hours, minutes] = value.split(":").map(Number);
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit" })
    .format(new Date(2000, 0, 1, hours, minutes));
}

function updateSummary() {
  get("summaryName").textContent = candidateName.value.trim() || "Name will appear here";
  get("summaryDate").textContent = formatDate(interviewDate.value);
  get("summaryTime").textContent = formatTime(startTime.value);
  get("summaryDuration").textContent = duration.value ? duration.selectedOptions[0].textContent || "Not selected" : "Not selected";
  get("summaryResume").textContent = resumeInput.files?.[0]?.name || "Not uploaded";
}

function setFileName(input: HTMLInputElement, target: string, empty: string) {
  get(target).textContent = input.files?.[0]?.name || empty;
}

function validateFile(file: File | undefined, allowed: string[], label: string) {
  if (!file) return `${label} is required.`;
  if (file.size > 10 * 1024 * 1024) return `${label} must be 10 MB or smaller.`;
  const extension = file.name.toLowerCase().split(".").pop() || "";
  if (!allowed.includes(extension)) return `Choose a valid ${label.toLowerCase()} file.`;
  return "";
}

async function fileToBase64(file: File) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize));
  }
  return btoa(binary);
}

async function api(path: string, options: RequestInit = {}) {
  const key = accessKey.value.trim();
  if (!key) throw new Error("Enter your personal access key first.");
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${key}`, ...(options.headers || {}) },
  });
  if (!response.ok) {
    let message = "The interview request failed. Try again.";
    try { message = (await response.json()).detail || message; } catch { /* Use safe fallback. */ }
    throw new Error(message);
  }
  return response;
}

function escapeText(value: string) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function renderSaved(interviews: SavedInterview[]) {
  savedGrid.replaceChildren();
  if (!interviews.length) {
    savedMessage.dataset.tone = "idle";
    savedMessage.textContent = "No saved interviews for this personal access key.";
    return;
  }
  savedMessage.dataset.tone = "success";
  savedMessage.textContent = `${interviews.length} saved interview${interviews.length === 1 ? "" : "s"} loaded.`;
  interviews.forEach(interview => {
    const card = document.createElement("article");
    card.className = "saved-interview";
    card.innerHTML = `
      <div class="saved-interview-top">
        <img src="${interview.thumbnail}" alt="${escapeText(interview.candidateName)}" />
        <div class="saved-interview-copy">
          <h3>${escapeText(interview.candidateName)}</h3>
          <p>${escapeText(formatDate(interview.interviewDate))} · ${escapeText(formatTime(interview.startTime))}</p>
          <p>${interview.duration} minutes</p>
          <p class="resume-name">${escapeText(interview.resumeName)}</p>
        </div>
      </div>
      <div class="saved-actions">
        <button class="secondary download-resume" type="button">Download résumé</button>
        <button class="delete-button" type="button" aria-label="Delete ${escapeText(interview.candidateName)}">Delete</button>
      </div>`;
    card.querySelector<HTMLButtonElement>(".download-resume")!.addEventListener("click", () => downloadResume(interview));
    card.querySelector<HTMLButtonElement>(".delete-button")!.addEventListener("click", () => deleteInterview(interview));
    savedGrid.append(card);
  });
}

async function loadInterviews() {
  savedMessage.dataset.tone = "working";
  savedMessage.textContent = "Loading saved interviews…";
  try {
    const response = await api("/api/interviews");
    const result = await response.json() as { interviews: SavedInterview[] };
    renderSaved(result.interviews);
  } catch (error) {
    savedGrid.replaceChildren();
    savedMessage.dataset.tone = "error";
    savedMessage.textContent = error instanceof Error ? error.message : "Could not load saved interviews.";
  }
}

async function downloadResume(interview: SavedInterview) {
  try {
    const response = await api(`/api/interviews/${encodeURIComponent(interview.id)}/resume`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = interview.resumeName;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    savedMessage.dataset.tone = "error";
    savedMessage.textContent = error instanceof Error ? error.message : "Could not download the résumé.";
  }
}

async function deleteInterview(interview: SavedInterview) {
  if (!window.confirm(`Delete the saved interview for ${interview.candidateName}?`)) return;
  try {
    await api(`/api/interviews/${encodeURIComponent(interview.id)}`, { method: "DELETE" });
    await loadInterviews();
  } catch (error) {
    savedMessage.dataset.tone = "error";
    savedMessage.textContent = error instanceof Error ? error.message : "Could not delete the interview.";
  }
}

candidateName.addEventListener("input", updateSummary);
interviewDate.addEventListener("change", updateSummary);
startTime.addEventListener("change", updateSummary);
duration.addEventListener("change", updateSummary);
get<HTMLButtonElement>("loadInterviewsButton").addEventListener("click", loadInterviews);
get<HTMLButtonElement>("refreshInterviewsButton").addEventListener("click", loadInterviews);

photoInput.addEventListener("change", () => {
  const file = photoInput.files?.[0];
  setFileName(photoInput, "photoName", "No photo selected");
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = "";
  if (file && !validateFile(file, ["jpg", "jpeg", "png", "webp"], "Photo")) {
    photoUrl = URL.createObjectURL(file);
    photoPreview.replaceChildren(Object.assign(document.createElement("img"), { src: photoUrl, alt: "Selected candidate" }));
  } else {
    photoPreview.innerHTML = '<span aria-hidden="true">◎</span><p>Candidate photo preview</p>';
  }
  updateSummary();
});

resumeInput.addEventListener("change", () => {
  setFileName(resumeInput, "resumeName", "No résumé selected");
  updateSummary();
});

form.addEventListener("submit", async event => {
  event.preventDefault();
  const photo = photoInput.files?.[0];
  const resume = resumeInput.files?.[0];
  const errors = {
    key: accessKey.value.trim().length >= 32 ? "" : "Enter a valid personal access key.",
    name: candidateName.value.trim() ? "" : "Enter the candidate’s full name.",
    date: interviewDate.value ? (interviewDate.value < localToday ? "Choose today or a future date." : "") : "Choose an interview date.",
    time: startTime.value ? "" : "Choose a start time.",
    duration: duration.value ? "" : "Choose the interview duration.",
    photo: validateFile(photo, ["jpg", "jpeg", "png", "webp"], "Photo"),
    resume: validateFile(resume, ["pdf", "doc", "docx"], "Résumé"),
    consent: storageConsent.checked ? "" : "Confirm permission to store the candidate files.",
  };
  get("keyError").textContent = errors.key;
  get("nameError").textContent = errors.name;
  get("dateError").textContent = errors.date;
  get("timeError").textContent = errors.time;
  get("durationError").textContent = errors.duration;
  get("filesError").textContent = [errors.photo, errors.resume, errors.consent].filter(Boolean).join(" ");
  if (Object.values(errors).some(Boolean) || !photo || !resume) {
    formMessage.dataset.tone = "error";
    formMessage.textContent = "Please complete the highlighted interview details.";
    return;
  }
  saveButton.disabled = true;
  formMessage.dataset.tone = "working";
  formMessage.textContent = "Saving interview and candidate files…";
  try {
    const photoExtension = photo.name.split(".").pop()?.toLowerCase() || "";
    const photoMime = photo.type || ({ jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp" } as Record<string, string>)[photoExtension];
    const resumeExtension = resume.name.split(".").pop()?.toLowerCase() || "";
    const resumeMime = resume.type || ({ pdf: "application/pdf", doc: "application/msword", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" } as Record<string, string>)[resumeExtension];
    const payload = {
      candidateName: candidateName.value.trim(), interviewDate: interviewDate.value,
      startTime: startTime.value, duration: Number(duration.value),
      photoName: photo.name, photoMime, photoData: await fileToBase64(photo),
      resumeName: resume.name, resumeMime, resumeData: await fileToBase64(resume),
    };
    await api("/api/interviews", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    formMessage.dataset.tone = "success";
    formMessage.textContent = "Interview saved to your private library.";
    await loadInterviews();
    get("savedSection").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    formMessage.dataset.tone = "error";
    formMessage.textContent = error instanceof Error ? error.message : "Could not save the interview.";
  } finally {
    saveButton.disabled = false;
  }
});

get<HTMLButtonElement>("clearButton").addEventListener("click", () => {
  const key = accessKey.value;
  form.reset();
  accessKey.value = key;
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = "";
  photoPreview.innerHTML = '<span aria-hidden="true">◎</span><p>Candidate photo preview</p>';
  ["keyError", "nameError", "dateError", "timeError", "durationError", "filesError"].forEach(id => get(id).textContent = "");
  get("photoName").textContent = "No photo selected";
  get("resumeName").textContent = "No résumé selected";
  formMessage.textContent = "";
  updateSummary();
  candidateName.focus();
});

updateSummary();
