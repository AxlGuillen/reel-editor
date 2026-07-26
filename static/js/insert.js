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
const insClipList = el("ins-clip-list");

const insTrack = el("ins-track");
const insPlayhead = el("ins-playhead");
const insCurTime = el("ins-cur-time");
const insDurTime = el("ins-dur-time");
const insActiveHint = el("ins-active-hint");
const insProcessBtn = el("ins-process-btn");

const insProgressWrap = el("ins-progress-wrap");
const insProgressFill = el("ins-progress-fill");
const insProgressLabel = el("ins-progress-label");
const insResultWrap = el("ins-result-wrap");
const insDownloadBtn = el("ins-download-btn");
const insErrorWrap = el("ins-error-wrap");
const insErrorMsg = el("ins-error-msg");

let insVideoFile = null;
// Mini-clips: { file, name, markers: number[], vertical: bool, dur: number }.
// Cada uno lleva SUS marcas; el activo es al que se le agregan las nuevas.
let insClips = [];
let insActive = 0;
let insMode = "full";

// Color por clip para los ticks del timeline (se cicla si hay más).
const INS_COLORS = ["#6c5ce7", "#00b894", "#e17055", "#0984e3",
                    "#e84393", "#fdcb6e", "#00cec9", "#d63031"];
const insColor = (i) => INS_COLORS[i % INS_COLORS.length];

// Duración del mini-clip activo (la usa el reparto de adelantos del progresivo).
function insClipDurActive() {
  return insClips[insActive] ? insClips[insActive].dur || 0 : 0;
}

// Refs del panel "caída progresiva".
const insModeTabs = el("ins-mode-tabs");
const insModeHint = el("ins-mode-hint");
const insProg = el("ins-prog");
const insOvl = el("ins-ovl");
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
  insClips.forEach((c) => (c.markers = []));   // el timeline cambió
  renderClips();
  insEditor.classList.remove("hidden");
  insResetOutputs();
  updateInsReady();
  insEditor.scrollIntoView({ behavior: "smooth" });
});

// Acepta varios archivos de una (el input es multiple y el drop puede traer N).
insClipZone.addEventListener("click", () => insClipInput.click());
insClipInput.addEventListener("change", (e) => {
  [...e.target.files].forEach(addClip);
  e.target.value = "";
});
["dragenter", "dragover"].forEach((evt) =>
  insClipZone.addEventListener(evt, (e) => {
    e.preventDefault();
    insClipZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  insClipZone.addEventListener(evt, (e) => {
    e.preventDefault();
    insClipZone.classList.remove("dragover");
  })
);
insClipZone.addEventListener("drop", (e) => {
  [...e.dataTransfer.files].forEach(addClip);
});

function addClip(file) {
  if (insMode === "progressive" && insClips.length >= 1) {
    alert("La caída progresiva trabaja con un solo mini-clip.");
    return;
  }
  const clip = { file, name: file.name, markers: [], vertical: false, dur: 0 };
  insClips.push(clip);
  insActive = insClips.length - 1;

  // Sonda offscreen para leer la duración sin mostrar el video.
  const probe = document.createElement("video");
  probe.preload = "metadata";
  probe.addEventListener("loadedmetadata", () => {
    clip.dur = probe.duration || 0;
    renderClips();
    renderSlices();
  });
  probe.src = URL.createObjectURL(file);

  renderClips();
  insResetOutputs();
  updateInsReady();
}

function removeClip(idx) {
  insClips.splice(idx, 1);
  if (insActive >= insClips.length) insActive = Math.max(0, insClips.length - 1);
  renderClips();
  renderMarkers();
  renderSlices();
  updateInsReady();
}

// Tarjeta por clip: nombre, marcas propias, toggle de vertical y borrar.
function renderClips() {
  insClipList.innerHTML = "";
  insClips.forEach((clip, i) => {
    const card = document.createElement("div");
    card.className = "ins-clip-card" + (i === insActive ? " active" : "");
    card.style.borderLeftColor = insColor(i);
    card.addEventListener("click", (e) => {
      if (e.target.closest("button, input")) return;
      insActive = i;
      renderClips();
    });

    const head = document.createElement("div");
    head.className = "ins-clip-head";
    const dot = document.createElement("span");
    dot.className = "ins-clip-dot";
    dot.style.background = insColor(i);
    const name = document.createElement("span");
    name.className = "ins-clip-name";
    name.textContent = clip.name;
    name.title = clip.name;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "ins-del";
    del.textContent = "✕";
    del.title = "Quitar este mini-clip";
    del.addEventListener("click", () => removeClip(i));
    head.append(dot, name, del);

    const meta = document.createElement("p");
    meta.className = "ins-clip-meta";
    meta.textContent = clip.dur
      ? `${clip.dur.toFixed(1)}s · ${clip.markers.length} marca(s)`
      : `${clip.markers.length} marca(s)`;

    // Marcas de este clip, cada una removible.
    const chips = document.createElement("ul");
    chips.className = "ins-marker-list";
    clip.markers.forEach((t) => {
      const li = document.createElement("li");
      const lbl = document.createElement("span");
      lbl.textContent = fmtTime(t);
      const x = document.createElement("button");
      x.type = "button";
      x.className = "ins-del";
      x.textContent = "✕";
      x.addEventListener("click", () => {
        clip.markers = clip.markers.filter((m) => m !== t);
        renderClips();
        renderMarkers();
        renderSlices();
        updateInsReady();
      });
      li.append(lbl, x);
      chips.appendChild(li);
    });

    const vert = document.createElement("label");
    vert.className = "control-check ins-clip-vert";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = clip.vertical;
    cb.addEventListener("change", () => {
      clip.vertical = cb.checked;
      el("ins-vertical-adv").classList.toggle(
        "hidden", !insClips.some((c) => c.vertical));
    });
    const txt = document.createElement("span");
    txt.textContent = "Convertir a vertical (viene 16:9)";
    vert.append(cb, txt);

    card.append(head, meta, chips, vert);
    insClipList.appendChild(card);
  });

  const activo = insClips[insActive];
  insActiveHint.innerHTML = activo
    ? `Las marcas se agregan a <strong>${activo.name}</strong>.`
    : "Subí un mini-clip para empezar a marcar.";
  renderMarkers();
}

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

// La marca se agrega SIEMPRE al clip activo (el seleccionado en la lista).
function addMarker(t) {
  const clip = insClips[insActive];
  if (!clip) return;
  if (insVideo.duration) t = Math.min(t, insVideo.duration);
  t = Math.max(0, Math.round(t * 100) / 100);
  // Evita duplicados accidentales muy cercanos (<0.1s) en el mismo clip.
  if (clip.markers.some((m) => Math.abs(m - t) < 0.1)) return;
  clip.markers.push(t);
  clip.markers.sort((a, b) => a - b);
  renderClips();
  renderSlices();
  updateInsReady();
}

// Ticks sobre el track, coloreados según el clip al que pertenecen.
function renderMarkers() {
  insTrack.querySelectorAll(".ins-marker").forEach((n) => n.remove());
  const dur = insVideo.duration || 0;
  insClips.forEach((clip, i) => {
    clip.markers.forEach((t) => {
      const tick = document.createElement("div");
      tick.className = "ins-marker";
      tick.style.left = dur ? `${(t / dur) * 100}%` : "0%";
      tick.style.background = insColor(i);
      tick.title = `${clip.name} @ ${fmtTime(t)}`;
      if (i !== insActive) tick.style.opacity = ".55";
      insTrack.appendChild(tick);
    });
  });
}

function insTotalMarkers() {
  return insClips.reduce((n, c) => n + c.markers.length, 0);
}

function updateInsReady() {
  insProcessBtn.disabled = !(insVideoFile && insClips.length && insTotalMarkers() > 0);
}

// --- Modo (clip completo / superpuesto / caída progresiva) ---
const INS_MODE_HINTS = {
  full: "Inserta cada mini-clip <strong>completo</strong> en sus marcas (con su audio).",
  overlay: "El original se <strong>congela</strong> de fondo y el mini-clip va " +
           "<strong>superpuesto</strong> encima, tipo recuadro.",
  progressive: "Va metiendo <strong>pedazos que avanzan</strong> del mini-clip en " +
               "cada marca; al final, la caída completa. Usa <strong>un solo</strong> mini-clip.",
};
insModeTabs.querySelectorAll(".rx-tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    insMode = btn.dataset.mode;
    insModeTabs.querySelectorAll(".rx-tab").forEach((b) =>
      b.classList.toggle("active", b === btn));
    insProg.classList.toggle("hidden", insMode !== "progressive");
    insOvl.classList.toggle("hidden", insMode !== "overlay");
    insModeHint.innerHTML = INS_MODE_HINTS[insMode] || INS_MODE_HINTS.full;
    // El progresivo trabaja con un solo clip: avisamos antes de que falle el backend.
    if (insMode === "progressive" && insClips.length > 1) {
      alert("La caída progresiva usa un solo mini-clip. Dejé el primero; " +
            "quitá o volvé a agregar los demás en modo 'Clip completo'.");
      insClips = insClips.slice(0, 1);
      insActive = 0;
      renderClips();
      updateInsReady();
    }
    renderSlices();
  });
});

// --- Ajustes del fondo vertical: visibles cuando algún clip pide conversión ---
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

el("ins-ovl-size").addEventListener("input", () => {
  el("ins-ovl-size-val").textContent = el("ins-ovl-size").value;
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
  const n = insTotalMarkers();
  const clipDur = insClipDurActive();
  if (insMode !== "progressive" || !n || !clipDur) {
    insSlices.innerHTML = "";
    hint.textContent = "";
    return;
  }
  const auto = clipDur / n;
  hint.textContent =
    `Auto: ${clipDur.toFixed(1)}s ÷ ${n} = ${auto.toFixed(2)}s por adelanto.`;

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
  if (!insVideoFile || !insClips.length || insTotalMarkers() === 0) return;
  insVideo.pause();

  const form = new FormData();
  form.append("video", insVideoFile);
  form.append("mode", insMode);

  // Un archivo por clip (clip_0, clip_1, …) + sus specs en JSON.
  insClips.forEach((clip, i) => form.append(`clip_${i}`, clip.file));
  form.append("clips", JSON.stringify(
    insClips.map((c) => ({ markers: c.markers, vertical: c.vertical }))
  ));

  if (insClips.some((c) => c.vertical)) {
    form.append("blur_intensity", el("ins-blur_intensity").value);
    form.append("bg_brightness", el("ins-bg_brightness").value);
    form.append("main_clip_scale", el("ins-main_clip_scale").value);
    form.append("enhance_intensity", el("ins-enhance_intensity").value);
    form.append("main_clip_position", el("ins-main_clip_position").value);
  }

  if (insMode === "overlay") {
    form.append("overlay_position", el("ins-ovl-position").value);
    form.append("overlay_size", el("ins-ovl-size").value);
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
      if (durs.length === insTotalMarkers() && durs.every((d) => d > 0)) {
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
  insClips = [];
  insActive = 0;
  insVideoInput.value = "";
  insClipInput.value = "";
  insVideo.src = "";
  insVideoName.textContent = "";
  renderClips();
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
