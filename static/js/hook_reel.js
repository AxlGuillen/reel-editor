(function () {
// ReelForge — módulo hook_reel ("Reel Frase"): video avatar + clip de cierre
// con música que sube en el corte. Dos fases cuando hay subtítulos.
const HK_API = "/api/hook-reel";

// Archivos seleccionados (la música puede ser link o archivo)
let hkVideo1 = null;
let hkVideo2 = null;
let hkMusic = null;
let hkSource = "url";                 // "url" | "file"
const hkMusicUrl = el("hk-music-url");

let hkPrepJobId = null;   // job de la fase 1 (tiene el reel base + ctx)
let hkSegments = [];      // segmentos transcritos (editables)

const hkAddSubs = el("hk-add-subs");
const hkSubsControls = el("hk-subs-controls");
const hkSubsEditor = el("hk-subs-editor");
const hkSegmentsBox = el("hk-segments");
const hkFinishBtn = el("hk-finish-btn");
const hkProcessBtn = el("hk-process-btn");

const hkProgressWrap = el("hk-progress-wrap");
const hkProgressFill = el("hk-progress-fill");
const hkProgressLabel = el("hk-progress-label");
const hkResultWrap = el("hk-result-wrap");
const hkDownloadBtn = el("hk-download-btn");
const hkErrorWrap = el("hk-error-wrap");
const hkErrorMsg = el("hk-error-msg");

// --- Drag & drop genérico para una zona ---
function wireDropZone(zone, input, onFile) {
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    if (e.target.files.length) onFile(e.target.files[0]);
  });
  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.add("dragover"); })
  );
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.classList.remove("dragover"); })
  );
  zone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) onFile(file);
  });
}

wireDropZone(el("hk-video1-zone"), el("hk-video1-input"), (f) => {
  hkVideo1 = f; el("hk-video1-name").textContent = f.name; hkResetOutputs(); updateReady();
});
wireDropZone(el("hk-video2-zone"), el("hk-video2-input"), (f) => {
  hkVideo2 = f; el("hk-video2-name").textContent = f.name; hkResetOutputs(); updateReady();
});
hkMusicUrl.addEventListener("input", () => { hkResetOutputs(); updateReady(); });
wireDropZone(el("hk-music-zone"), el("hk-music-input"), (f) => {
  hkMusic = f; el("hk-music-name").textContent = f.name; hkResetOutputs(); updateReady();
});

// --- Toggle de fuente de música (link | archivo) ---
el("hk-tab-url").addEventListener("click", () => setSource("url"));
el("hk-tab-file").addEventListener("click", () => setSource("file"));
function setSource(src) {
  hkSource = src;
  el("hk-tab-url").classList.toggle("active", src === "url");
  el("hk-tab-file").classList.toggle("active", src === "file");
  el("hk-source-url").classList.toggle("hidden", src !== "url");
  el("hk-source-file").classList.toggle("hidden", src !== "file");
  updateReady();
}

function updateReady() {
  const musicReady = hkSource === "url" ? !!hkMusicUrl.value.trim() : !!hkMusic;
  hkProcessBtn.disabled = !(hkVideo1 && hkVideo2 && musicReady);
}

// --- Sliders: labels en vivo ---
const hkLabels = {
  "hk-music-low": "hk-music-low-val",
  "hk-music-full": "hk-music-full-val",
  "hk-ramp": "hk-ramp-val",
  "hk-sub-font-size": "hk-sub-font-size-val",
  "hk-sub-position-y": "hk-sub-position-y-val",
  "hk-sub-words": "hk-sub-words-val",
  "hk-blur_intensity": "hk-blur-val",
  "hk-bg_brightness": "hk-brightness-val",
  "hk-main_clip_scale": "hk-scale-val",
  "hk-enhance_intensity": "hk-enhance-val",
};
Object.entries(hkLabels).forEach(([inputId, labelId]) => {
  const input = el(inputId);
  input.addEventListener("input", () => (el(labelId).textContent = input.value));
});

// --- Subtítulos: toggle + color preview ---
hkAddSubs.addEventListener("change", () => {
  hkSubsControls.classList.toggle("hidden", !hkAddSubs.checked);
});
el("hk-sub-highlight-color").addEventListener("input", () => {
  el("hk-sub-color-preview").style.background = el("hk-sub-highlight-color").value;
});

// --- Fase 1: generar ---
hkProcessBtn.addEventListener("click", () => {
  const musicReady = hkSource === "url" ? !!hkMusicUrl.value.trim() : !!hkMusic;
  if (!(hkVideo1 && hkVideo2 && musicReady)) return;

  const form = new FormData();
  form.append("video1", hkVideo1);
  form.append("video2", hkVideo2);
  form.append("audio_source", hkSource);
  if (hkSource === "url") {
    form.append("music_url", hkMusicUrl.value.trim());
    form.append("music_quality", el("hk-music-quality").value);
  } else {
    form.append("music", hkMusic);
  }
  form.append("music_start", el("hk-music-start").value);
  form.append("seg2_duration", el("hk-seg2-duration").value);
  form.append("output_name", el("hk-output-name").value);
  form.append("music_low_volume", el("hk-music-low").value);
  form.append("music_full_volume", el("hk-music-full").value);
  form.append("ramp", el("hk-ramp").value);
  form.append("seg1_vertical", el("hk-seg1-vertical").checked ? "1" : "0");
  form.append("seg2_vertical", el("hk-seg2-vertical").checked ? "1" : "0");
  // Vertical (avanzado)
  form.append("blur_intensity", el("hk-blur_intensity").value);
  form.append("bg_brightness", el("hk-bg_brightness").value);
  form.append("main_clip_scale", el("hk-main_clip_scale").value);
  form.append("enhance_intensity", el("hk-enhance_intensity").value);
  form.append("main_clip_position", el("hk-main_clip_position").value);
  // Subtítulos
  form.append("add_subtitles", hkAddSubs.checked ? "1" : "0");
  if (hkAddSubs.checked) {
    form.append("language", el("hk-sub-language").value);
    form.append("model", el("hk-sub-model").value);
    form.append("font_size", el("hk-sub-font-size").value);
    form.append("position_y", el("hk-sub-position-y").value);
    form.append("words_per_line", el("hk-sub-words").value);
    form.append("highlight_color", el("hk-sub-highlight-color").value);
  }

  hkResetOutputs();
  hkSubsEditor.classList.add("hidden");
  hkProcessBtn.disabled = true;
  hkProgressWrap.classList.remove("hidden");
  hkSetProgress(0, "Iniciando…");

  fetch(`${HK_API}/process`, { method: "POST", body: form })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      hkPrepJobId = data.job_id;
      hkPollJob(data.job_id, {
        onProgress: hkSetProgress,
        onDone: (jobId, d) => {
          if (hkAddSubs.checked) {
            hkProgressWrap.classList.add("hidden");
            hkSegments = d.segments || [];
            hkSubsEditor.classList.remove("hidden");
            SubtitleEditor.render(hkSegmentsBox, hkSegments);
            hkProcessBtn.disabled = false;
            hkSubsEditor.scrollIntoView({ behavior: "smooth" });
          } else {
            hkShowResult(jobId);
          }
        },
        onError: hkShowError,
      });
    })
    .catch((err) => hkShowError(err.message));
});

// Poller propio (status/<id> trae segments + stage).
function hkPollJob(jobId, { onProgress, onDone, onError }) {
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`${HK_API}/status/${jobId}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      onProgress(data.progress, data.stage);
      if (data.status === "done") {
        clearInterval(timer);
        onDone(jobId, data);
      } else if (data.status === "error") {
        clearInterval(timer);
        onError(data.error || "Falló el procesamiento");
      }
    } catch (err) {
      clearInterval(timer);
      onError(err.message);
    }
  }, 1000);
}

// --- Fase 2: con los subtítulos editados, generar el reel final ---
hkFinishBtn.addEventListener("click", () => {
  if (!hkPrepJobId || !hkSegments.length) return;

  const payload = {
    job_id: hkPrepJobId,
    segments: SubtitleEditor.collect(hkSegments),
    output_name: el("hk-output-name").value,
    font_size: el("hk-sub-font-size").value,
    position_y: el("hk-sub-position-y").value,
    words_per_line: el("hk-sub-words").value,
    highlight_color: el("hk-sub-highlight-color").value,
  };

  hkResetOutputs();
  hkFinishBtn.disabled = true;
  hkProgressWrap.classList.remove("hidden");
  hkSetProgress(0, "Generando…");

  fetch(`${HK_API}/finish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      hkPollJob(data.job_id, {
        onProgress: hkSetProgress,
        onDone: (jobId) => { hkShowResult(jobId); hkFinishBtn.disabled = false; },
        onError: (msg) => { hkShowError(msg); hkFinishBtn.disabled = false; },
      });
    })
    .catch((err) => { hkShowError(err.message); hkFinishBtn.disabled = false; });
});

function hkSetProgress(pct, stage) {
  hkProgressFill.style.width = `${pct}%`;
  hkProgressLabel.textContent = `${stage || "Procesando…"} ${pct}%`;
}

function hkShowResult(jobId) {
  hkProgressWrap.classList.add("hidden");
  hkSubsEditor.classList.add("hidden");
  hkDownloadBtn.href = `${HK_API}/download/${jobId}`;
  hkResultWrap.classList.remove("hidden");
  hkProcessBtn.disabled = false;
}

function hkShowError(msg) {
  hkProgressWrap.classList.add("hidden");
  hkErrorMsg.textContent = msg;
  hkErrorWrap.classList.remove("hidden");
  hkProcessBtn.disabled = false;
}

function hkResetOutputs() {
  hkProgressWrap.classList.add("hidden");
  hkResultWrap.classList.add("hidden");
  hkErrorWrap.classList.add("hidden");
}

el("hk-error-reset-btn").addEventListener("click", () => {
  hkErrorWrap.classList.add("hidden");
});
el("hk-reset-btn").addEventListener("click", () => {
  hkVideo1 = hkVideo2 = hkMusic = null;
  hkMusicUrl.value = "";
  el("hk-output-name").value = "";
  ["hk-video1-input", "hk-video2-input", "hk-music-input"].forEach((id) => (el(id).value = ""));
  ["hk-video1-name", "hk-video2-name", "hk-music-name"].forEach((id) => (el(id).textContent = ""));
  hkSubsEditor.classList.add("hidden");
  hkPrepJobId = null;
  hkSegments = [];
  hkResetOutputs();
  updateReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
})();
