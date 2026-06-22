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
let subFontFamily = "sans-serif";
let subTranscribeJobId = null;   // job que tiene el video subido en el server
let subSegments = [];            // segmentos detectados (con words originales)

// --- Cargar fuente custom para el preview ---
fetch("/api/font").then((r) => r.json()).then((info) => {
  if (!info.available) return;
  const ff = new FontFace("WMBrand", "url(/assets/font)");
  ff.load().then((f) => { document.fonts.add(f); subFontFamily = "WMBrand"; }).catch(() => {});
});

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
          subProgressWrap.classList.add("hidden");
          subSegments = data.segments || [];
          subSubsEditor.classList.remove("hidden");  // visible antes de medir altura
          renderSegments();
          subTranscribeBtn.disabled = false;
          subRetranscribeBtn.disabled = false;
          subSubsEditor.scrollIntoView({ behavior: "smooth" });
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

// --- Render del listado editable de segmentos ---
function renderSegments() {
  subSegmentsBox.innerHTML = "";
  subSegments.forEach((seg, i) => {
    const row = document.createElement("div");
    row.className = "sub-seg";
    const ts = `${fmtTime(seg.start)} → ${fmtTime(seg.end)}`;
    row.innerHTML = `
      <div class="sub-seg-meta"><span class="sub-seg-num">${i + 1}</span>
        <span class="sub-seg-time">${ts}</span></div>
      <textarea class="sub-seg-input text-input" rows="1"></textarea>`;
    const input = row.querySelector("textarea");
    input.value = seg.text;
    input.addEventListener("input", () => {
      seg.text = input.value;
      autoGrow(input);
    });
    subSegmentsBox.appendChild(row);
    autoGrow(input);   // ajustar al alto del contenido al renderizar
  });
}

function fmtTime(s) {
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, "0")}`;
}

// Ajusta el alto del textarea a su contenido, así el texto largo se ve completo
// (con wrap) sin scroll horizontal.
function autoGrow(ta) {
  ta.style.height = "auto";
  ta.style.height = `${ta.scrollHeight}px`;
}

// --- Fase 2: Generar video (render) ---
subRenderBtn.addEventListener("click", () => {
  if (!subTranscribeJobId || !subSegments.length) return;

  // Para cada segmento: si el texto cambió, NO mandamos words (el backend
  // redistribuye); si quedó igual, mandamos words originales (timing exacto).
  const segments = subSegments.map((seg) => {
    const edited = seg.text.trim() !== originalText(seg);
    return {
      start: seg.start,
      end: seg.end,
      text: seg.text,
      words: edited ? null : seg.words,
    };
  });

  const payload = {
    job_id: subTranscribeJobId,
    segments,
    font_size: el("sub-font-size").value,
    position_y: el("sub-position-y").value,
    words_per_line: el("sub-words").value,
    highlight_color: el("sub-highlight-color").value,
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
});

function originalText(seg) {
  return (seg.words || []).map((w) => w.word).join(" ").trim();
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
  const fs = +el("sub-font-size").value;
  const posY = +el("sub-position-y").value;
  const color = el("sub-highlight-color").value;
  const words = +el("sub-words").value;

  const y = (posY / 1920) * SUB_H;

  subCtx.font = `bold ${fs * SUB_SCALE}px ${subFontFamily}`;
  subCtx.textAlign = "center";
  subCtx.textBaseline = "bottom";
  subCtx.lineJoin = "round";

  const samples = ["Ejemplo", "de", "subtítulo", "en", "vivo", "aquí"];
  const group = samples.slice(0, words);
  const activeIdx = Math.min(1, group.length - 1);

  const border = Math.max(2, fs * 0.07) * SUB_SCALE;
  subCtx.lineWidth = border * 2;
  subCtx.strokeStyle = "black";

  const totalText = group.join(" ");
  const totalW = subCtx.measureText(totalText).width;
  let curX = (SUB_W - totalW) / 2;

  group.forEach((word, i) => {
    const wordW = subCtx.measureText(word).width;
    const spaceW = i < group.length - 1 ? subCtx.measureText(" ").width : 0;
    const cx = curX + wordW / 2;
    subCtx.fillStyle = i === activeIdx ? color : "white";
    subCtx.strokeText(word, cx, y);
    subCtx.fillText(word, cx, y);
    curX += wordW + spaceW;
  });
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
el("sub-highlight-color").addEventListener("input", () => {
  el("sub-color-preview").style.background = el("sub-highlight-color").value;
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
