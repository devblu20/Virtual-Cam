// Library management is separate from the camera lifecycle. File selection alone
// never uploads; thumbnails cannot change the current transformation reference.
const libraryElement = id => document.getElementById(id);
const libraryKey = libraryElement("accessKey");
const libraryInput = libraryElement("referenceInput");
const libraryStatus = libraryElement("libraryStatus");
const libraryGrid = libraryElement("referenceLibrary");
const saveReference = libraryElement("saveReferenceButton");
const refreshLibrary = libraryElement("refreshLibraryButton");
let libraryGeneration = 0;
let libraryBusy = false;

function setLibraryBusy(value) {
  libraryBusy = value;
  saveReference.disabled = value;
  refreshLibrary.disabled = value;
  for (const button of libraryGrid.querySelectorAll("button")) button.disabled = value;
}
function resetLibrary() {
  libraryGeneration++;
  libraryGrid.replaceChildren();
  libraryElement("storageConsent").checked = false;
  libraryStatus.textContent = "Press Show / refresh saved photos to load this key's library.";
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
      } catch (error) { if (generation === libraryGeneration) libraryStatus.textContent = error.message; }
      finally { setLibraryBusy(false); }
    });
    card.append(image, name, remove); libraryGrid.append(card);
  }
  libraryStatus.textContent = `${data.references.length} / ${data.limit} photos saved. Open the extension with the same personal key; its library refreshes while the popup is open.`;
}
refreshLibrary.addEventListener("click", async () => {
  if (libraryBusy) return;
  const key = libraryKey.value.trim(), generation = ++libraryGeneration;
  libraryGrid.replaceChildren(); setLibraryBusy(true);
  libraryStatus.textContent = "Loading saved photos…";
  try { await showLibrary(key, generation); }
  catch (error) { if (generation === libraryGeneration) libraryStatus.textContent = error.message; }
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
    setLibraryBusy(true); libraryStatus.textContent = "Saving photo securely…";
    await libraryRequest(key, "/api/references", { method: "POST", body: file,
      headers: { "Content-Type": file.type, "X-Reference-Consent": "true", "X-Reference-Name": encodeURIComponent(name) } });
    if (generation !== libraryGeneration) return;
    libraryElement("storageConsent").checked = false;
    await showLibrary(key, generation);
  } catch (error) { if (generation === libraryGeneration) libraryStatus.textContent = error.message; }
  finally { setLibraryBusy(false); }
});
window.addEventListener("pagehide", resetLibrary);
