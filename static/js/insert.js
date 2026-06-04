// ReelForge — módulo insert (insertar un mini-clip en marcadores del original).
const INS_API = "/api/insert";

const insVideoZone = el("ins-video-zone");
const insVideoInput = el("ins-video-input");
const insEditor = el("ins-editor");
const insVideo = el("ins-video");
const insVideoName = el("ins-video-name");

const insClipZone = el("ins-clip-zone");
const insClipInput = el("ins-clip-input");
const insClipName = el("ins-clip-name");

const insTrack = el("ins-track");
const insPlayhead = el("ins-playhead");
const insCurTime = el("ins-cur-time");
const insDurTime = el("ins-dur-time");
const insMarkerList = el("ins-marker-list");
const insProcessBtn = el("ins-process-btn");

const insProgressWrap = el("ins-progress-wrap");
const insProgressFill = el("ins-progress-fill");
const insProgressLabel = el("ins-progress-label");
const insResultWrap = el("ins-result-wrap");
const insDownloadBtn = el("ins-download-btn");
const insErrorWrap = el("ins-error-wrap");
const insErrorMsg = el("ins-error-msg");

let insVideoFile = null;
let insClipFile = null;
let insMarkers = []; // segundos (float), ordenados

// --- Drag & drop genérico (local para no depender del orden de carga) ---
function insWireDropZone(zone, input, onFile) {
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

insWireDropZone(insVideoZone, insVideoInput, (file) => {
  insVideoFile = file;
  insVideo.src = URL.createObjectURL(file);
  insVideoName.textContent = file.name;
  insMarkers = [];
  renderMarkers();
  insEditor.classList.remove("hidden");
  insResetOutputs();
  updateInsReady();
  insEditor.scrollIntoView({ behavior: "smooth" });
});

insWireDropZone(insClipZone, insClipInput, (file) => {
  insClipFile = file;
  insClipName.textContent = file.name;
  insResetOutputs();
  updateInsReady();
});

// --- Helpers de tiempo ---
function fmtTime(s) {
  if (!isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

function parseTime(str) {
  // Acepta "m:ss", "mm:ss" o segundos sueltos ("83").
  str = str.trim();
  if (!str) return null;
  if (str.includes(":")) {
    const [m, s] = str.split(":");
    const mins = parseInt(m, 10);
    const secs = parseFloat(s);
    if (isNaN(mins) || isNaN(secs)) return null;
    return mins * 60 + secs;
  }
  const v = parseFloat(str);
  return isNaN(v) ? null : v;
}

// --- Timeline ---
insVideo.addEventListener("loadedmetadata", () => {
  insDurTime.textContent = fmtTime(insVideo.duration);
});
insVideo.addEventListener("timeupdate", () => {
  const frac = insVideo.duration ? insVideo.currentTime / insVideo.duration : 0;
  insPlayhead.style.left = `${frac * 100}%`;
  insCurTime.textContent = fmtTime(insVideo.currentTime);
});

insTrack.addEventListener("click", (e) => {
  if (!insVideo.duration) return;
  const rect = insTrack.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  addMarker(frac * insVideo.duration);
});

el("ins-play-btn").addEventListener("click", () => {
  if (insVideo.paused) {
    insVideo.play();
    el("ins-play-btn").textContent = "⏸ Pausa";
  } else {
    insVideo.pause();
    el("ins-play-btn").textContent = "▶ Play";
  }
});
insVideo.addEventListener("ended", () => {
  el("ins-play-btn").textContent = "▶ Play";
});

el("ins-mark-here").addEventListener("click", () => {
  if (insVideoFile) addMarker(insVideo.currentTime);
});

el("ins-add-time").addEventListener("click", () => {
  const t = parseTime(el("ins-time-input").value);
  if (t === null) {
    el("ins-time-input").focus();
    return;
  }
  addMarker(t);
  el("ins-time-input").value = "";
});
el("ins-time-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") el("ins-add-time").click();
});

function addMarker(t) {
  if (insVideo.duration) t = Math.min(t, insVideo.duration);
  t = Math.max(0, Math.round(t * 100) / 100);
  // Evita duplicados accidentales muy cercanos (<0.1s).
  if (insMarkers.some((m) => Math.abs(m - t) < 0.1)) return;
  insMarkers.push(t);
  insMarkers.sort((a, b) => a - b);
  renderMarkers();
  updateInsReady();
}

function removeMarker(t) {
  insMarkers = insMarkers.filter((m) => m !== t);
  renderMarkers();
  updateInsReady();
}

function renderMarkers() {
  // Ticks sobre el track.
  insTrack.querySelectorAll(".ins-marker").forEach((n) => n.remove());
  const dur = insVideo.duration || 0;
  insMarkers.forEach((t) => {
    const tick = document.createElement("div");
    tick.className = "ins-marker";
    tick.style.left = dur ? `${(t / dur) * 100}%` : "0%";
    insTrack.appendChild(tick);
  });

  // Lista de chips.
  insMarkerList.innerHTML = "";
  insMarkers.forEach((t) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = fmtTime(t);
    const del = document.createElement("button");
    del.className = "ins-del";
    del.textContent = "✕";
    del.title = "Quitar marca";
    del.addEventListener("click", () => removeMarker(t));
    li.append(label, del);
    insMarkerList.appendChild(li);
  });
}

function updateInsReady() {
  insProcessBtn.disabled = !(insVideoFile && insClipFile && insMarkers.length > 0);
}

// --- Process ---
insProcessBtn.addEventListener("click", () => {
  if (!insVideoFile || !insClipFile || insMarkers.length === 0) return;
  insVideo.pause();

  const form = new FormData();
  form.append("video", insVideoFile);
  form.append("clip", insClipFile);
  form.append("markers", JSON.stringify(insMarkers));

  insResetOutputs();
  insProcessBtn.disabled = true;
  insProgressWrap.classList.remove("hidden");
  insSetProgress(0);

  runJob(INS_API, form, {
    onProgress: insSetProgress,
    onDone: insShowResult,
    onError: insShowError,
  });
});

function insSetProgress(pct) {
  insProgressFill.style.width = `${pct}%`;
  insProgressLabel.textContent = `Procesando… ${pct}%`;
}

function insShowResult(jobId) {
  insProgressWrap.classList.add("hidden");
  insDownloadBtn.href = `${INS_API}/download/${jobId}`;
  insResultWrap.classList.remove("hidden");
  updateInsReady();
}

function insShowError(msg) {
  insProgressWrap.classList.add("hidden");
  insErrorMsg.textContent = msg;
  insErrorWrap.classList.remove("hidden");
  updateInsReady();
}

function insResetOutputs() {
  insProgressWrap.classList.add("hidden");
  insResultWrap.classList.add("hidden");
  insErrorWrap.classList.add("hidden");
}

// --- Reset ---
el("ins-reset-btn").addEventListener("click", () => {
  insVideo.pause();
  insVideoFile = null;
  insClipFile = null;
  insMarkers = [];
  insVideoInput.value = "";
  insClipInput.value = "";
  insVideo.src = "";
  insVideoName.textContent = "";
  insClipName.textContent = "";
  renderMarkers();
  insEditor.classList.add("hidden");
  insResetOutputs();
  updateInsReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("ins-error-reset-btn").addEventListener("click", () => {
  insErrorWrap.classList.add("hidden");
});
