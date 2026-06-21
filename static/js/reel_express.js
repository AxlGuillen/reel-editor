// ReelForge — módulo reel_express (pipeline: vertical + descarga + mezcla).
const RX_API = "/api/reel-express";

const rxClipZone = el("rx-clip-zone");
const rxClipInput = el("rx-clip-input");
const rxClipName = el("rx-clip-name");
const rxEditor = el("rx-editor");
const rxUrl = el("rx-url");
const rxProcessBtn = el("rx-process-btn");

const rxProgressWrap = el("rx-progress-wrap");
const rxProgressFill = el("rx-progress-fill");
const rxProgressLabel = el("rx-progress-label");
const rxResultWrap = el("rx-result-wrap");
const rxDownloadBtn = el("rx-download-btn");
const rxErrorWrap = el("rx-error-wrap");
const rxErrorMsg = el("rx-error-msg");

let rxClipFile = null;

// --- Carga del clip (click + drag & drop) ---
rxClipZone.addEventListener("click", () => rxClipInput.click());
rxClipInput.addEventListener("change", (e) => {
  if (e.target.files.length) loadClip(e.target.files[0]);
});
["dragenter", "dragover"].forEach((evt) =>
  rxClipZone.addEventListener(evt, (e) => {
    e.preventDefault();
    rxClipZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  rxClipZone.addEventListener(evt, (e) => {
    e.preventDefault();
    rxClipZone.classList.remove("dragover");
  })
);
rxClipZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) loadClip(file);
});

function loadClip(file) {
  rxClipFile = file;
  rxClipName.textContent = file.name;
  rxEditor.classList.remove("hidden");
  rxResetOutputs();
  updateReady();
  rxEditor.scrollIntoView({ behavior: "smooth" });
}

function updateReady() {
  rxProcessBtn.disabled = !(rxClipFile && rxUrl.value.trim());
}
rxUrl.addEventListener("input", updateReady);

// --- Sliders: labels en vivo ---
const rxLabels = {
  "rx-blur_intensity": "rx-blur-val",
  "rx-bg_brightness": "rx-brightness-val",
  "rx-main_clip_scale": "rx-scale-val",
  "rx-enhance_intensity": "rx-enhance-val",
  "rx-original_volume": "rx-vo-val",
  "rx-new_volume": "rx-vn-val",
};
Object.entries(rxLabels).forEach(([inputId, labelId]) => {
  const input = el(inputId);
  input.addEventListener("input", () => (el(labelId).textContent = input.value));
});

// --- Process ---
rxProcessBtn.addEventListener("click", () => {
  if (!rxClipFile || !rxUrl.value.trim()) return;

  const form = new FormData();
  form.append("clip", rxClipFile);
  form.append("url", rxUrl.value.trim());
  form.append("quality", el("rx-quality").value);
  // Video vertical
  form.append("blur_intensity", el("rx-blur_intensity").value);
  form.append("bg_brightness", el("rx-bg_brightness").value);
  form.append("main_clip_scale", el("rx-main_clip_scale").value);
  form.append("enhance_intensity", el("rx-enhance_intensity").value);
  form.append("main_clip_position", el("rx-main_clip_position").value);
  // Mezcla
  form.append("original_volume", el("rx-original_volume").value);
  form.append("new_volume", el("rx-new_volume").value);
  form.append("fade", el("rx-fade").checked ? "1" : "0");
  form.append("speed_match", el("rx-speed_match").checked ? "1" : "0");

  rxResetOutputs();
  rxProcessBtn.disabled = true;
  rxProgressWrap.classList.remove("hidden");
  rxSetProgress(0, "Iniciando…");

  runJob(RX_API, form, {
    onProgress: rxSetProgress,
    onDone: rxShowResult,
    onError: rxShowError,
  });
});

// runJob llama onProgress(progress, stage?) — el status de reel_express manda
// también la etapa actual para mostrarla.
function rxSetProgress(pct, stage) {
  rxProgressFill.style.width = `${pct}%`;
  const label = stage || "Procesando…";
  rxProgressLabel.textContent = `${label} ${pct}%`;
}

function rxShowResult(jobId) {
  rxProgressWrap.classList.add("hidden");
  rxDownloadBtn.href = `${RX_API}/download/${jobId}`;
  rxResultWrap.classList.remove("hidden");
  rxProcessBtn.disabled = false;
}

function rxShowError(msg) {
  rxProgressWrap.classList.add("hidden");
  rxErrorMsg.textContent = msg;
  rxErrorWrap.classList.remove("hidden");
  rxProcessBtn.disabled = false;
}

function rxResetOutputs() {
  rxProgressWrap.classList.add("hidden");
  rxResultWrap.classList.add("hidden");
  rxErrorWrap.classList.add("hidden");
}

// --- Reset ---
el("rx-reset-btn").addEventListener("click", () => {
  rxClipFile = null;
  rxClipInput.value = "";
  rxClipName.textContent = "";
  rxUrl.value = "";
  rxEditor.classList.add("hidden");
  rxResetOutputs();
  updateReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("rx-error-reset-btn").addEventListener("click", () => {
  rxErrorWrap.classList.add("hidden");
});
