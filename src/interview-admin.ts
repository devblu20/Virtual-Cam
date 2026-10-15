import "./interview-admin.css";

const get = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const unlockPanel = get<HTMLElement>("unlockPanel");
const accessPanel = get<HTMLElement>("accessPanel");
const unlockMessage = get<HTMLElement>("unlockMessage");
const accessMessage = get<HTMLElement>("accessMessage");
const adminKeyInput = get<HTMLInputElement>("adminKey");
const emailOne = get<HTMLInputElement>("emailOne");
const emailTwo = get<HTMLInputElement>("emailTwo");
let adminKey = "";

async function request(method = "GET", body?: { emails: string[] }) {
  const response = await fetch("/api/admin/interview-access", {
    method,
    headers: {
      Authorization: `Bearer ${adminKey}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({ detail: "The server response could not be read." }));
  if (!response.ok) throw new Error(payload.detail || "Access settings could not be loaded.");
  return payload as { emails: string[]; limit: number };
}

function lock() {
  adminKey = "";
  adminKeyInput.value = "";
  emailOne.value = "";
  emailTwo.value = "";
  accessMessage.textContent = "";
  accessPanel.hidden = true;
  unlockPanel.hidden = false;
  adminKeyInput.focus();
}

get<HTMLFormElement>("unlockForm").addEventListener("submit", async event => {
  event.preventDefault();
  adminKey = adminKeyInput.value;
  unlockMessage.textContent = "Checking administrator access…";
  try {
    const data = await request();
    emailOne.value = data.emails[0] || "";
    emailTwo.value = data.emails[1] || "";
    unlockMessage.textContent = "";
    adminKeyInput.value = "";
    unlockPanel.hidden = true;
    accessPanel.hidden = false;
    emailOne.focus();
  } catch (error) {
    adminKey = "";
    unlockMessage.textContent = error instanceof Error ? error.message : "Administrator access failed.";
  }
});

get<HTMLFormElement>("accessForm").addEventListener("submit", async event => {
  event.preventDefault();
  accessMessage.classList.remove("success");
  accessMessage.textContent = "Saving approved emails…";
  try {
    const data = await request("PUT", { emails: [emailOne.value, emailTwo.value] });
    emailOne.value = data.emails[0];
    emailTwo.value = data.emails[1];
    accessMessage.classList.add("success");
    accessMessage.textContent = "Access saved. Only these two emails can now sign in.";
  } catch (error) {
    accessMessage.textContent = error instanceof Error ? error.message : "Access settings could not be saved.";
  }
});

get("lockButton").addEventListener("click", lock);
