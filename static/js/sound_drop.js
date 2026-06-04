// ReelForge — módulo sound_drop (audio de fondo sobre video vertical).
const SD_API = "/api/sound-drop";

const sdVideoZone = el("sd-video-zone");
const sdVideoInput = el("sd-video-input");
const sdEditor = el("sd-editor");
const sdVideo = el("sd-video");
const sdVideoName = el("sd-video-name");

const sdAudioZone = el("sd-audio-zone");
const sdAudioInput = el("sd-audio-input");
const sdAudio = el("sd-audio");
const sdAudioName = el("sd-audio-name");
const sdPreviewBtn = el("sd-preview-btn");

const sdOriginalVol = el("sd-original-volume");
const sdNewVol = el("sd-new-volume");
const sdProcessBtn = el("sd-process-btn");

const sdProgressWrap = el("sd-progress-wrap");
const sdProgressFill = el("sd-progress-fill");
const sdProgressLabel = el("sd-progress-label");
const sdResultWrap = el("sd-result-wrap");
const sdDownloadBtn = el("sd-download-btn");
const sdErrorWrap = el("sd-error-wrap");
const sdErrorMsg = el("sd-error-msg");

let sdVideoFile = null;
let sdAudioFile = null;

// --- Drag & drop genérico para una zona ---
function wireDropZone(zone, input, onFile) {
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    if (e.target.files.length) onFile(e.target.files[0]);
  });
  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      zone.classList.remove("dragover");
    })
  );
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) onFile(file);
  });
}

wireDropZone(sdVideoZone, sdVideoInput, (file) => {
  sdVideoFile = file;
  sdVideo.src = URL.createObjectURL(file);
  sdVideoName.textContent = file.name;
  sdEditor.classList.remove("hidden");
  sdResetOutputs();
  updateReady();
  sdEditor.scrollIntoView({ behavior: "smooth" });
});

wireDropZone(sdAudioZone, sdAudioInput, (file) => {
  sdAudioFile = file;
  sdAudio.src = URL.createObjectURL(file);
  sdAudioName.textContent = file.name;
  sdResetOutputs();
  updateReady();
});

function updateReady() {
  const ready = !!(sdVideoFile && sdAudioFile);
  sdProcessBtn.disabled = !ready;
  sdPreviewBtn.disabled = !ready;
}

// --- Preview de mezcla (Web Audio API) ---
// Conecta el audio del video y el audio nuevo, cada uno con su GainNode, para
// escuchar la mezcla con los volúmenes exactos en tiempo real. El speed match
// no se previsualiza (se aplica al exportar).
let audioCtx, vGain, aGain, graphReady = false, isPlaying = false;

function ensureGraph() {
  if (graphReady) return;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const vSrc = audioCtx.createMediaElementSource(sdVideo);
  const aSrc = audioCtx.createMediaElementSource(sdAudio);
  vGain = audioCtx.createGain();
  aGain = audioCtx.createGain();
  vSrc.connect(vGain).connect(audioCtx.destination);
  aSrc.connect(aGain).connect(audioCtx.destination);
  graphReady = true;
}

function applyGains() {
  if (!graphReady) return;
  vGain.gain.value = +sdOriginalVol.value / 100;
  aGain.gain.value = +sdNewVol.value / 100;
}

function stopMix() {
  isPlaying = false;
  sdVideo.pause();
  sdAudio.pause();
  sdPreviewBtn.textContent = "▶ Escuchar mezcla";
}

sdPreviewBtn.addEventListener("click", () => {
  if (!sdVideoFile || !sdAudioFile) return;
  if (isPlaying) {
    stopMix();
    return;
  }
  ensureGraph();
  audioCtx.resume();
  applyGains();
  sdVideo.currentTime = 0;
  sdAudio.currentTime = 0;
  sdVideo.play();
  sdAudio.play();
  isPlaying = true;
  sdPreviewBtn.textContent = "⏹ Detener";
});

// Cuando termina el audio nuevo, paramos la mezcla.
sdAudio.addEventListener("ended", stopMix);

// --- Sliders: labels + gains en vivo ---
sdOriginalVol.addEventListener("input", () => {
  el("sd-vo-val").textContent = sdOriginalVol.value;
  applyGains();
});
sdNewVol.addEventListener("input", () => {
  el("sd-vn-val").textContent = sdNewVol.value;
  applyGains();
});

// --- Process ---
sdProcessBtn.addEventListener("click", () => {
  if (!sdVideoFile || !sdAudioFile) return;
  stopMix();

  const form = new FormData();
  form.append("video", sdVideoFile);
  form.append("audio", sdAudioFile);
  form.append("original_volume", sdOriginalVol.value);
  form.append("new_volume", sdNewVol.value);
  form.append("fade", el("sd-fade").checked ? "1" : "0");
  form.append("speed_match", el("sd-speed-match").checked ? "1" : "0");

  sdResetOutputs();
  sdProcessBtn.disabled = true;
  sdProgressWrap.classList.remove("hidden");
  sdSetProgress(0);

  runJob(SD_API, form, {
    onProgress: sdSetProgress,
    onDone: sdShowResult,
    onError: sdShowError,
  });
});

function sdSetProgress(pct) {
  sdProgressFill.style.width = `${pct}%`;
  sdProgressLabel.textContent = `Procesando… ${pct}%`;
}

function sdShowResult(jobId) {
  sdProgressWrap.classList.add("hidden");
  sdDownloadBtn.href = `${SD_API}/download/${jobId}`;
  sdResultWrap.classList.remove("hidden");
  sdProcessBtn.disabled = false;
}

function sdShowError(msg) {
  sdProgressWrap.classList.add("hidden");
  sdErrorMsg.textContent = msg;
  sdErrorWrap.classList.remove("hidden");
  sdProcessBtn.disabled = false;
}

function sdResetOutputs() {
  sdProgressWrap.classList.add("hidden");
  sdResultWrap.classList.add("hidden");
  sdErrorWrap.classList.add("hidden");
}

// --- Reset buttons ---
el("sd-reset-btn").addEventListener("click", () => {
  stopMix();
  sdVideoFile = null;
  sdAudioFile = null;
  sdVideoInput.value = "";
  sdAudioInput.value = "";
  sdVideo.src = "";
  sdAudio.src = "";
  sdVideoName.textContent = "";
  sdAudioName.textContent = "";
  sdEditor.classList.add("hidden");
  sdResetOutputs();
  updateReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("sd-error-reset-btn").addEventListener("click", () => {
  sdErrorWrap.classList.add("hidden");
});
