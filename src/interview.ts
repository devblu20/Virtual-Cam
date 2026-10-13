import "./interview.css";

const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const form = get<HTMLFormElement>("interviewForm");
const candidateName = get<HTMLInputElement>("candidateName");
const interviewDate = get<HTMLInputElement>("interviewDate");
const startTime = get<HTMLInputElement>("startTime");
const duration = get<HTMLSelectElement>("duration");
const photoInput = get<HTMLInputElement>("photoInput");
const resumeInput = get<HTMLInputElement>("resumeInput");
const photoPreview = get<HTMLElement>("photoPreview");
const formMessage = get<HTMLElement>("formMessage");
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

candidateName.addEventListener("input", updateSummary);
interviewDate.addEventListener("change", updateSummary);
startTime.addEventListener("change", updateSummary);
duration.addEventListener("change", updateSummary);

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

form.addEventListener("submit", event => {
  event.preventDefault();
  const errors = {
    name: candidateName.value.trim() ? "" : "Enter the candidate’s full name.",
    date: interviewDate.value ? (interviewDate.value < localToday ? "Choose today or a future date." : "") : "Choose an interview date.",
    time: startTime.value ? "" : "Choose a start time.",
    duration: duration.value ? "" : "Choose the interview duration.",
    photo: validateFile(photoInput.files?.[0], ["jpg", "jpeg", "png", "webp"], "Photo"),
    resume: validateFile(resumeInput.files?.[0], ["pdf", "doc", "docx"], "Résumé"),
  };
  get("nameError").textContent = errors.name;
  get("dateError").textContent = errors.date;
  get("timeError").textContent = errors.time;
  get("durationError").textContent = errors.duration;
  get("filesError").textContent = [errors.photo, errors.resume].filter(Boolean).join(" ");
  const firstError = Object.values(errors).find(Boolean);
  if (firstError) {
    formMessage.dataset.tone = "error";
    formMessage.textContent = "Please complete the highlighted interview details.";
    form.querySelector<HTMLElement>(":invalid")?.focus();
    return;
  }
  updateSummary();
  formMessage.dataset.tone = "success";
  formMessage.textContent = "Interview details are ready for review.";
  get("summaryTitle").scrollIntoView({ behavior: "smooth", block: "start" });
});

get<HTMLButtonElement>("clearButton").addEventListener("click", () => {
  form.reset();
  if (photoUrl) URL.revokeObjectURL(photoUrl);
  photoUrl = "";
  photoPreview.innerHTML = '<span aria-hidden="true">◎</span><p>Candidate photo preview</p>';
  ["nameError", "dateError", "timeError", "durationError", "filesError"].forEach(id => get(id).textContent = "");
  get("photoName").textContent = "No photo selected";
  get("resumeName").textContent = "No résumé selected";
  formMessage.textContent = "";
  updateSummary();
  candidateName.focus();
});

updateSummary();
