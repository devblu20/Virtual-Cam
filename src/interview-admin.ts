import "./interview-admin.css";

type Interview = { id: string; candidateName: string; interviewDate: string; startTime: string; duration: number; status: string };
const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const loginView = get<HTMLElement>("loginView");
const dashboard = get<HTMLElement>("dashboard");
const logoutButton = get<HTMLButtonElement>("logoutButton");
const message = get<HTMLElement>("loginMessage");
let credentials = "";

function basic(email: string, password: string) {
  const bytes = new TextEncoder().encode(`${email}:${password}`);
  let binary = "";
  bytes.forEach(byte => binary += String.fromCharCode(byte));
  return `Basic ${btoa(binary)}`;
}

function escape(value: string) {
  return value.replace(/[&<>'"]/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character] || character);
}

function formatDuration(minutes: number) {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} hr ${rest} min` : `${hours} hr`;
}

async function load() {
  const response = await fetch("/api/interview-admin", { headers: { Authorization: credentials } });
  const payload = await response.json().catch(() => ({ detail: "The server response could not be read." }));
  if (!response.ok) throw new Error(payload.detail || "Admin Dashboard could not be loaded.");
  const interviews = payload.interviews as Interview[];
  const today = new Date().toISOString().slice(0, 10);
  get("signedInAs").textContent = `Signed in as ${payload.administrator}`;
  get("totalCount").textContent = String(interviews.length);
  get("upcomingCount").textContent = String(interviews.filter(item => item.interviewDate >= today && item.status === "Scheduled").length);
  get("completedCount").textContent = String(interviews.filter(item => item.status === "Completed").length);
  get("totalTime").textContent = formatDuration(interviews.reduce((sum, item) => sum + item.duration, 0));
  get("recordCount").textContent = `${interviews.length} record${interviews.length === 1 ? "" : "s"}`;
  get("emptyState").hidden = interviews.length > 0;
  get<HTMLTableSectionElement>("adminRows").innerHTML = interviews.map(item => `<tr><td><strong>${escape(item.candidateName)}</strong></td><td><span class="status status-${escape(item.status.toLowerCase())}">${escape(item.status)}</span></td><td>${escape(item.interviewDate)}</td><td>${escape(item.startTime)}</td><td>${formatDuration(item.duration)}</td></tr>`).join("");
  get("dashboardMessage").textContent = `Last refreshed ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}

get<HTMLFormElement>("loginForm").addEventListener("submit", async event => {
  event.preventDefault();
  credentials = basic(get<HTMLInputElement>("adminEmail").value.trim().toLowerCase(), get<HTMLInputElement>("adminPassword").value);
  message.textContent = "Opening Admin Dashboard…";
  try {
    await load();
    message.textContent = "";
    get<HTMLInputElement>("adminPassword").value = "";
    loginView.hidden = true;
    dashboard.hidden = false;
    logoutButton.hidden = false;
  } catch (error) {
    credentials = "";
    message.textContent = error instanceof Error ? error.message : "Admin login failed.";
  }
});

get("refreshButton").addEventListener("click", () => load().catch(error => { get("dashboardMessage").textContent = error instanceof Error ? error.message : "Refresh failed."; }));
logoutButton.addEventListener("click", () => {
  credentials = "";
  dashboard.hidden = true;
  logoutButton.hidden = true;
  loginView.hidden = false;
  get<HTMLInputElement>("adminEmail").value = "";
  message.textContent = "";
});
