(function () {
// ReelForge — módulo subtitles (transcripción editable + karaoke).
// Flujo: subir video → Transcribir → corregir texto → Generar video.
const SUB_API = "/api/subtitles";

const subVideoZone = el("sub-video-zone");
const subVideoInput = el("sub-video-input");
const subEditor = el("sub-editor");
const subVideo = el("sub-video");
const subVideoName = el("sub-video-name");
const subTranscribeBtn = el("sub-transcribe-btn");

const subSubsEditor = el("sub-subs-editor");
const subSegmentsBox = el("sub-segments");
const subRenderBtn = el("sub-render-btn");
const subRetranscribeBtn = el("sub-retranscribe-btn");

// Preview canvas — resolución interna 9:16 (mitad de 1080×1920).
const subCanvas = el("sub-canvas");
const subCtx = subCanvas.getContext("2d");
const SUB_SCALE = 0.5;
const SUB_W = 1080 * SUB_SCALE;   // 540
const SUB_H = 1920 * SUB_SCALE;   // 960

const subProgressWrap = el("sub-progress-wrap");
const subProgressFill = el("sub-progress-fill");
const subProgressLabel = el("sub-progress-label");
const subResultWrap = el("sub-result-wrap");
const subDownloadBtn = el("sub-download-btn");
const subErrorWrap = el("sub-error-wrap");
const subErrorMsg = el("sub-error-msg");

let subVideoFile = null;
let subRafId = null;
let subTranscribeJobId = null;   // job que tiene el video subido en el server
let subSegments = [];            // segmentos detectados (con words originales)

// Estilo (fuente, mayúsculas, colores, contorno): bloque compartido.
SubStyle.init("sub-");

// --- Drop zone ---
subVideoZone.addEventListener("click", () => subVideoInput.click());
subVideoInput.addEventListener("change", (e) => {
  if (e.target.files.length) loadVideo(e.target.files[0]);
});
["dragenter", "dragover"].forEach((evt) =>
  subVideoZone.addEventListener(evt, (e) => {
    e.preventDefault();
    subVideoZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  subVideoZone.addEventListener(evt, (e) => {
    e.preventDefault();
    subVideoZone.classList.remove("dragover");
  })
);
subVideoZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) loadVideo(file);
});

function loadVideo(file) {
  subVideoFile = file;
  subVideo.src = URL.createObjectURL(file);
  subVideoName.textContent = file.name;
  subEditor.classList.remove("hidden");
  subSubsEditor.classList.add("hidden");
  subSegments = [];
  subTranscribeJobId = null;
  subResetOutputs();
  updateReady();
  startPreview();
  subEditor.scrollIntoView({ behavior: "smooth" });
}

function updateReady() {
  subTranscribeBtn.disabled = !subVideoFile;
}

// --- Poller genérico (status/<jobId>) ---
function pollJob(jobId, { onProgress, onDone, onError }) {
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`${SUB_API}/status/${jobId}`);
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

// --- Fase 1: Transcribir ---
subTranscribeBtn.addEventListener("click", () => {
  if (!subVideoFile) return;

  const form = new FormData();
  form.append("video", subVideoFile);
  form.append("language", el("sub-language").value);
  form.append("model", el("sub-model").value);

  subResetOutputs();
  subTranscribeBtn.disabled = true;
  subRetranscribeBtn.disabled = true;
  subProgressWrap.classList.remove("hidden");
  subSetProgress(0, "Transcribiendo…");

  fetch(`${SUB_API}/transcribe`, { method: "POST", body: form })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      subTranscribeJobId = data.job_id;
      pollJob(data.job_id, {
        onProgress: subSetProgress,
        onDone: (jobId, data) => {
          subSegments = data.segments || [];
          subTranscribeBtn.disabled = false;
          subRetranscribeBtn.disabled = false;
          if (el("sub-skip-review").checked) {
            // Sin revisión: generar directo con la transcripción tal cual.
            subRunRender(subSegments);
          } else {
            subProgressWrap.classList.add("hidden");
            subSubsEditor.classList.remove("hidden");  // visible antes de medir altura
            SubtitleEditor.render(subSegmentsBox, subSegments);
            subSubsEditor.scrollIntoView({ behavior: "smooth" });
          }
        },
        onError: (msg) => {
          subShowError(msg);
          subTranscribeBtn.disabled = false;
          subRetranscribeBtn.disabled = false;
        },
      });
    })
    .catch((err) => {
      subShowError(err.message);
      subTranscribeBtn.disabled = false;
      subRetranscribeBtn.disabled = false;
    });
});

subRetranscribeBtn.addEventListener("click", () => {
  subSubsEditor.classList.add("hidden");
  subTranscribeBtn.click();
});

// El listado editable de segmentos lo maneja SubtitleEditor (compartido).

// --- Fase 2: Generar video (render) con los segmentos dados (editados o crudos) ---
subRenderBtn.addEventListener("click", () => {
  // Si el texto cambió, el backend redistribuye los tiempos (ver SubtitleEditor).
  subRunRender(SubtitleEditor.collect(subSegments));
});

function subRunRender(segments) {
  if (!subTranscribeJobId || !segments.length) return;

  const payload = {
    job_id: subTranscribeJobId,
    segments,
    ...SubStyle.params("sub-"),
  };

  subResetOutputs();
  subRenderBtn.disabled = true;
  subProgressWrap.classList.remove("hidden");
  subSetProgress(0, "Generando…");

  fetch(`${SUB_API}/render`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      pollJob(data.job_id, {
        onProgress: subSetProgress,
        onDone: (jobId) => {
          subProgressWrap.classList.add("hidden");
          subDownloadBtn.href = `${SUB_API}/download/${jobId}`;
          subResultWrap.classList.remove("hidden");
          subRenderBtn.disabled = false;
        },
        onError: (msg) => { subShowError(msg); subRenderBtn.disabled = false; },
      });
    })
    .catch((err) => { subShowError(err.message); subRenderBtn.disabled = false; });
}

// --- Preview en canvas ---
function startPreview() {
  subCanvas.width = SUB_W;
  subCanvas.height = SUB_H;
  cancelAnimationFrame(subRafId);
  const loop = () => {
    drawCanvas();
    subRafId = requestAnimationFrame(loop);
  };
  loop();
}

function drawCanvas() {
  subCtx.clearRect(0, 0, SUB_W, SUB_H);
  if (subVideo.readyState >= 2 && subVideo.videoWidth) {
    const vw = subVideo.videoWidth;
    const vh = subVideo.videoHeight;
    const scale = Math.max(SUB_W / vw, SUB_H / vh);
    const dw = vw * scale;
    const dh = vh * scale;
    subCtx.drawImage(subVideo, (SUB_W - dw) / 2, (SUB_H - dh) / 2, dw, dh);
  } else {
    subCtx.fillStyle = "#000";
    subCtx.fillRect(0, 0, SUB_W, SUB_H);
  }
  drawSampleCaption();
}

function drawSampleCaption() {
  SubStyle.drawSample(subCtx, SUB_W, SUB_H, "sub-");
}

// --- Sliders: labels en vivo ---
el("sub-font-size").addEventListener("input", () => {
  el("sub-font-size-val").textContent = el("sub-font-size").value;
});
el("sub-position-y").addEventListener("input", () => {
  el("sub-position-y-val").textContent = el("sub-position-y").value;
});
el("sub-words").addEventListener("input", () => {
  el("sub-words-val").textContent = el("sub-words").value;
});

// --- Presets de posición ---
el("sub-preset-top").addEventListener("click", () => {
  el("sub-position-y").value = 280;
  el("sub-position-y-val").textContent = 280;
});
el("sub-preset-bottom").addEventListener("click", () => {
  el("sub-position-y").value = 1560;
  el("sub-position-y-val").textContent = 1560;
});

// --- Helpers de UI ---
function subSetProgress(pct, stage) {
  subProgressFill.style.width = `${pct}%`;
  subProgressLabel.textContent = stage ? `${stage} ${pct}%` : `Procesando… ${pct}%`;
}

function subShowError(msg) {
  subProgressWrap.classList.add("hidden");
  subErrorMsg.textContent = msg;
  subErrorWrap.classList.remove("hidden");
}

function subResetOutputs() {
  subProgressWrap.classList.add("hidden");
  subResultWrap.classList.add("hidden");
  subErrorWrap.classList.add("hidden");
}

// --- Reset ---
el("sub-reset-btn").addEventListener("click", () => {
  cancelAnimationFrame(subRafId);
  subCtx.clearRect(0, 0, SUB_W, SUB_H);
  subVideoFile = null;
  subVideoInput.value = "";
  subVideo.src = "";
  subVideoName.textContent = "";
  subEditor.classList.add("hidden");
  subSubsEditor.classList.add("hidden");
  subSegments = [];
  subTranscribeJobId = null;
  subResetOutputs();
  updateReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("sub-error-reset-btn").addEventListener("click", () => {
  subErrorWrap.classList.add("hidden");
});
})();
