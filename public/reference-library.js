// Library management is separate from the camera lifecycle. File selection alone
// never uploads; thumbnails cannot change the current transformation reference.
const libraryElement = id => document.getElementById(id);
const libraryKey = libraryElement("accessKey");
const libraryInput = libraryElement("referenceInput");
const libraryStatus = libraryElement("libraryStatus");
const libraryGrid = libraryElement("referenceLibrary");
const saveReference = libraryElement("saveReferenceButton");
const refreshLibrary = libraryElement("refreshLibraryButton");
const libraryEmpty = libraryElement("libraryEmpty");
const libraryCount = libraryElement("libraryCount");
let libraryGeneration = 0;
let libraryBusy = false;

function libraryMessage(message, tone = "idle") {
  libraryStatus.textContent = message;
  libraryStatus.dataset.tone = tone;
}
function clearLibraryCards() {
  libraryGrid.replaceChildren();
  libraryEmpty.hidden = false;
  libraryCount.textContent = "Up to 20 photos per user";
}

function setLibraryBusy(value) {
  libraryBusy = value;
  saveReference.disabled = value;
  refreshLibrary.disabled = value;
  libraryGrid.setAttribute("aria-busy", String(value));
  for (const button of libraryGrid.querySelectorAll("button")) button.disabled = value;
}
function resetLibrary() {
  libraryGeneration++;
  clearLibraryCards();
  libraryElement("storageConsent").checked = false;
  libraryMessage("Press Refresh photos to load this key's library.");
}
libraryKey.addEventListener("input", resetLibrary);
libraryInput.addEventListener("change", () => { libraryElement("storageConsent").checked = false; });

async function libraryRequest(key, path, options = {}) {
  if (key.length < 32 || key.length > 256) throw new Error("Enter the user's personal access key above first.");
  const response = await fetch(path, { ...options, cache: "no-store", credentials: "omit",
    signal: AbortSignal.timeout(35000),
    headers: { ...options.headers, Authorization: `Bearer ${key}` } });
  if (!response.ok) {
    const message = await response.json().catch(() => ({}));
    throw new Error(typeof message.detail === "string" ? message.detail : "Photo library request failed. Please try again.");
  }
  return response;
}
async function showLibrary(key, generation) {
  const response = await libraryRequest(key, "/api/references");
  const data = await response.json();
  if (generation !== libraryGeneration || libraryKey.value.trim() !== key) return;
  libraryGrid.replaceChildren();
  for (const photo of data.references) {
    const card = document.createElement("article");
    const image = document.createElement("img");
    image.src = photo.thumbnail; image.alt = photo.name;
    const name = document.createElement("p"); name.textContent = photo.name;
    const remove = document.createElement("button");
    remove.type = "button"; remove.textContent = "Delete saved photo"; remove.className = "danger compact";
    remove.addEventListener("click", async () => {
      if (libraryBusy || generation !== libraryGeneration || libraryKey.value.trim() !== key) return;
      if (!window.confirm(`Delete "${photo.name}" from this key's cloud library? Existing downloaded copies, active sessions and provider data are not removed.`)) return;
      setLibraryBusy(true);
      try {
        await libraryRequest(key, `/api/references/${encodeURIComponent(photo.id)}`, { method: "DELETE" });
        await showLibrary(key, generation);
      } catch (error) { if (generation === libraryGeneration) libraryMessage(error.message, "error"); }
      finally { setLibraryBusy(false); }
    });
    card.append(image, name, remove); libraryGrid.append(card);
  }
  libraryEmpty.hidden = data.references.length > 0;
  libraryCount.textContent = `${data.references.length} of ${data.limit} photos saved`;
  libraryMessage(data.references.length
    ? "Your library is up to date. These photos are available in the extension using the same personal key."
    : "No saved photos yet. Choose a reference above, then save it to your extension.", "success");
}
refreshLibrary.addEventListener("click", async () => {
  if (libraryBusy) return;
  const key = libraryKey.value.trim(), generation = ++libraryGeneration;
  clearLibraryCards(); setLibraryBusy(true);
  libraryMessage("Loading saved photos…", "loading");
  try { await showLibrary(key, generation); }
  catch (error) { if (generation === libraryGeneration) libraryMessage(error.message, "error"); }
  finally { setLibraryBusy(false); }
});
saveReference.addEventListener("click", async () => {
  if (libraryBusy) return;
  const key = libraryKey.value.trim(), generation = libraryGeneration;
  const file = libraryInput.files?.[0];
  try {
    if (!file) throw new Error("Choose a reference photo above first.");
    if (!libraryElement("storageConsent").checked) throw new Error("Confirm permission to store and share this photo first.");
    if (!file.size || file.size > 2 * 1024 * 1024) throw new Error("Use a non-empty image up to 2 MB for the extension library.");
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) throw new Error("Choose PNG, JPEG or WebP.");
    const name = libraryElement("referenceName").value.trim() || "Reference photo";
    setLibraryBusy(true); libraryMessage("Saving photo securely…", "loading");
    await libraryRequest(key, "/api/references", { method: "POST", body: file,
      headers: { "Content-Type": file.type, "X-Reference-Consent": "true", "X-Reference-Name": encodeURIComponent(name) } });
    if (generation !== libraryGeneration) return;
    libraryElement("storageConsent").checked = false;
    await showLibrary(key, generation);
  } catch (error) { if (generation === libraryGeneration) libraryMessage(error.message, "error"); }
  finally { setLibraryBusy(false); }
});
window.addEventListener("pagehide", resetLibrary);
