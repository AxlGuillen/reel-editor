(function () {
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
let insClipDur = 0;  // duración del mini-clip (para el reparto de adelantos)
let insMode = "full";

// Sonda offscreen para leer la duración del mini-clip sin mostrarlo.
const insClipProbe = document.createElement("video");
insClipProbe.preload = "metadata";
insClipProbe.addEventListener("loadedmetadata", () => {
  insClipDur = insClipProbe.duration || 0;
  renderSlices();
});

// Refs del panel "caída progresiva".
const insModeTabs = el("ins-mode-tabs");
const insModeHint = el("ins-mode-hint");
const insProg = el("ins-prog");
const insRevealEnabled = el("ins-reveal-enabled");
const insRevealOpts = el("ins-reveal-opts");
const insManualSlices = el("ins-manual-slices");
const insSlices = el("ins-slices");

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
  insClipDur = 0;
  insClipProbe.src = URL.createObjectURL(file);
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
// El tiempo actual va con décimas: los marcadores se guardan con 2 decimales
// y sin esto no se ve la precisión al navegar de a 1s.
function insRefreshTime() {
  const frac = insVideo.duration ? insVideo.currentTime / insVideo.duration : 0;
  insPlayhead.style.left = `${frac * 100}%`;
  const t = insVideo.currentTime;
  const m = Math.floor(t / 60);
  const s = (t % 60).toFixed(1).padStart(4, "0");
  insCurTime.textContent = isFinite(t) ? `${m}:${s}` : "0:00.0";
}
insVideo.addEventListener("timeupdate", insRefreshTime);
insVideo.addEventListener("seeked", insRefreshTime);

insTrack.addEventListener("click", (e) => {
  if (!insVideo.duration) return;
  const rect = insTrack.getBoundingClientRect();
  const frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
  addMarker(frac * insVideo.duration);
});

function insTogglePlay() {
  if (insVideo.paused) {
    insVideo.play();
    el("ins-play-btn").textContent = "⏸ Pausa";
  } else {
    insVideo.pause();
    el("ins-play-btn").textContent = "▶ Play";
  }
}
el("ins-play-btn").addEventListener("click", insTogglePlay);
insVideo.addEventListener("ended", () => {
  el("ins-play-btn").textContent = "▶ Play";
});

// --- Velocidad del preview (solo visual: no afecta el video generado) ---
const INS_SPEEDS = [1, 1.5, 2, 3];
let insSpeedIdx = 0;
function insSetSpeed(idx) {
  insSpeedIdx = (idx + INS_SPEEDS.length) % INS_SPEEDS.length;
  const rate = INS_SPEEDS[insSpeedIdx];
  insVideo.playbackRate = rate;
  el("ins-speed-btn").textContent = `${rate}×`;
}
el("ins-speed-btn").addEventListener("click", () => insSetSpeed(insSpeedIdx + 1));

// --- Navegación fina por el timeline ---
function insSeek(delta) {
  if (!insVideo.duration) return;
  const t = Math.min(insVideo.duration, Math.max(0, insVideo.currentTime + delta));
  insVideo.currentTime = t;
}
el("ins-back5").addEventListener("click", () => insSeek(-5));
el("ins-back1").addEventListener("click", () => insSeek(-1));
el("ins-fwd1").addEventListener("click", () => insSeek(1));
el("ins-fwd5").addEventListener("click", () => insSeek(5));

// Atajos de teclado: solo con el módulo visible y fuera de un campo de texto.
document.addEventListener("keydown", (e) => {
  const view = el("view-insert");
  if (!view || view.classList.contains("hidden") || !insVideoFile) return;
  const tag = (e.target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return;

  switch (e.key) {
    case " ":       e.preventDefault(); insTogglePlay(); break;
    case "ArrowLeft":  e.preventDefault(); insSeek(e.shiftKey ? -5 : -1); break;
    case "ArrowRight": e.preventDefault(); insSeek(e.shiftKey ? 5 : 1); break;
    case "m": case "M": e.preventDefault(); addMarker(insVideo.currentTime); break;
  }
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
  renderSlices();
  updateInsReady();
}

function removeMarker(t) {
  insMarkers = insMarkers.filter((m) => m !== t);
  renderMarkers();
  renderSlices();
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

// --- Modo (clip completo / caída progresiva) ---
insModeTabs.querySelectorAll(".rx-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    insMode = btn.dataset.mode;
    insModeTabs.querySelectorAll(".rx-tab").forEach((b) =>
      b.classList.toggle("active", b === btn));
    insProg.classList.toggle("hidden", insMode !== "progressive");
    insModeHint.innerHTML = insMode === "progressive"
      ? "Va metiendo <strong>pedazos que avanzan</strong> del mini-clip en cada marca; al final, la caída completa."
      : "Inserta el mini-clip <strong>completo</strong> en cada marca (con su audio).";
    renderSlices();
  });
});

// --- Mini-clip a vertical: toggle + labels de los sliders ---
const insClipVertical = el("ins-clip-vertical");
insClipVertical.addEventListener("change", () => {
  el("ins-vertical-adv").classList.toggle("hidden", !insClipVertical.checked);
});
el("ins-vertical-adv").classList.add("hidden");
Object.entries({
  "ins-blur_intensity": "ins-blur-val",
  "ins-bg_brightness": "ins-brightness-val",
  "ins-main_clip_scale": "ins-scale-val",
  "ins-enhance_intensity": "ins-enhance-val",
}).forEach(([inputId, labelId]) => {
  const input = el(inputId);
  input.addEventListener("input", () => (el(labelId).textContent = input.value));
});

insRevealEnabled.addEventListener("change", () => {
  insRevealOpts.classList.toggle("hidden", !insRevealEnabled.checked);
});
el("ins-reveal-speed").addEventListener("change", () => {
  el("ins-reveal-speed-val").textContent =
    `${parseFloat(el("ins-reveal-speed").value)}×`;
});
el("ins-reveal-where").addEventListener("change", () => {
  el("ins-reveal-at-wrap").classList.toggle(
    "hidden", el("ins-reveal-where").value !== "at");
});
insManualSlices.addEventListener("change", () => {
  insSlices.classList.toggle("hidden", !insManualSlices.checked);
  renderSlices();
});

// Reparto de los adelantos: muestra el auto y, si el usuario ajusta, inputs editables.
function renderSlices() {
  const hint = el("ins-slices-hint");
  const n = insMarkers.length;
  if (insMode !== "progressive" || !n || !insClipDur) {
    insSlices.innerHTML = "";
    hint.textContent = "";
    return;
  }
  const auto = insClipDur / n;
  hint.textContent =
    `Auto: ${insClipDur.toFixed(1)}s ÷ ${n} = ${auto.toFixed(2)}s por adelanto.`;

  if (!insManualSlices.checked) {
    insSlices.innerHTML = "";
    return;
  }
  // Preserva lo tipeado si la cantidad de marcadores no cambió.
  const prev = [...insSlices.querySelectorAll("input")].map((i) => i.value);
  const keep = prev.length === n;
  insSlices.innerHTML = "";
  for (let k = 0; k < n; k++) {
    const lbl = document.createElement("label");
    lbl.className = "control";
    const span = document.createElement("span");
    span.textContent = `Adelanto ${k + 1} (s)`;
    const inp = document.createElement("input");
    inp.type = "number";
    inp.min = "0.1";
    inp.step = "0.1";
    inp.className = "ins-time-input ins-slice-input";
    inp.value = keep ? prev[k] : auto.toFixed(2);
    lbl.append(span, inp);
    insSlices.appendChild(lbl);
  }
}

// --- Process ---
insProcessBtn.addEventListener("click", () => {
  if (!insVideoFile || !insClipFile || insMarkers.length === 0) return;
  insVideo.pause();

  const form = new FormData();
  form.append("video", insVideoFile);
  form.append("clip", insClipFile);
  form.append("markers", JSON.stringify(insMarkers));
  form.append("mode", insMode);

  form.append("clip_vertical", insClipVertical.checked ? "1" : "0");
  if (insClipVertical.checked) {
    form.append("blur_intensity", el("ins-blur_intensity").value);
    form.append("bg_brightness", el("ins-bg_brightness").value);
    form.append("main_clip_scale", el("ins-main_clip_scale").value);
    form.append("enhance_intensity", el("ins-enhance_intensity").value);
    form.append("main_clip_position", el("ins-main_clip_position").value);
  }

  if (insMode === "progressive") {
    form.append("reveal_enabled", insRevealEnabled.checked ? "1" : "0");
    form.append("reveal_speed", el("ins-reveal-speed").value);
    form.append("clip_audio", el("ins-clip-audio").value);
    if (el("ins-reveal-where").value === "at") {
      const at = parseTime(el("ins-reveal-at").value);
      if (at !== null) form.append("reveal_at", at);
    }
    if (insManualSlices.checked) {
      const durs = [...insSlices.querySelectorAll("input")]
        .map((i) => parseFloat(i.value));
      if (durs.length === insMarkers.length && durs.every((d) => d > 0)) {
        form.append("slice_durations", JSON.stringify(durs));
      }
    }
  }

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
  insSetSpeed(0);
  el("ins-play-btn").textContent = "▶ Play";
  insVideoFile = null;
  insClipFile = null;
  insMarkers = [];
  insVideoInput.value = "";
  insClipInput.value = "";
  insVideo.src = "";
  insVideoName.textContent = "";
  insClipName.textContent = "";
  insClipDur = 0;
  renderMarkers();
  renderSlices();
  insEditor.classList.add("hidden");
  insResetOutputs();
  updateInsReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("ins-error-reset-btn").addEventListener("click", () => {
  insErrorWrap.classList.add("hidden");
});
})();
