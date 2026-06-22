(function () {
// ReelForge — módulo subtitles (transcripción + karaoke con faster-whisper).
const SUB_API = "/api/subtitles";

const subVideoZone = el("sub-video-zone");
const subVideoInput = el("sub-video-input");
const subEditor = el("sub-editor");
const subVideo = el("sub-video");
const subVideoName = el("sub-video-name");
const subProcessBtn = el("sub-process-btn");

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
  subResetOutputs();
  updateReady();
  startPreview();
  subEditor.scrollIntoView({ behavior: "smooth" });
}

function updateReady() {
  subProcessBtn.disabled = !subVideoFile;
}

// --- Preview en canvas: frame del video + texto de muestra en posición/color ---
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

  // Frame del video
  if (subVideo.readyState >= 2 && subVideo.videoWidth) {
    // Dibujar cubriendo el canvas (mismo comportamiento que un video vertical).
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

// Dibuja una muestra del caption con la configuración actual para que el
// usuario pueda calar tamaño y posición antes de procesar.
function drawSampleCaption() {
  const fs = +el("sub-font-size").value;
  const posY = +el("sub-position-y").value;
  const color = el("sub-highlight-color").value;   // #rrggbb
  const words = +el("sub-words").value;

  // La posición en el canvas es proporcional: posY es px en 1920, escalamos.
  const y = (posY / 1920) * SUB_H;

  subCtx.font = `bold ${fs * SUB_SCALE}px ${subFontFamily}`;
  subCtx.textAlign = "center";
  subCtx.textBaseline = "bottom";
  subCtx.lineJoin = "round";

  // Grupo de palabras de muestra según words_per_line.
  const samples = ["Ejemplo", "de", "subtítulo", "en", "vivo", "aquí"];
  const group = samples.slice(0, words);
  const activeIdx = 1;   // segunda palabra siempre activa en el preview

  const border = Math.max(2, fs * 0.07) * SUB_SCALE;
  subCtx.lineWidth = border * 2;
  subCtx.strokeStyle = "black";

  // Calcular posición X de cada palabra para dibujarlas en línea.
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

// --- Process ---
subProcessBtn.addEventListener("click", () => {
  if (!subVideoFile) return;

  const form = new FormData();
  form.append("video", subVideoFile);
  form.append("font_size", el("sub-font-size").value);
  form.append("position_y", el("sub-position-y").value);
  form.append("words_per_line", el("sub-words").value);
  form.append("highlight_color", el("sub-highlight-color").value);
  form.append("language", el("sub-language").value);
  form.append("model", el("sub-model").value);

  subResetOutputs();
  subProcessBtn.disabled = true;
  subProgressWrap.classList.remove("hidden");
  subSetProgress(0, "Iniciando…");

  runJob(SUB_API, form, {
    onProgress: subSetProgress,
    onDone: subShowResult,
    onError: subShowError,
  });
});

function subSetProgress(pct, stage) {
  subProgressFill.style.width = `${pct}%`;
  subProgressLabel.textContent = stage ? `${stage} ${pct}%` : `Procesando… ${pct}%`;
}

function subShowResult(jobId) {
  subProgressWrap.classList.add("hidden");
  subDownloadBtn.href = `${SUB_API}/download/${jobId}`;
  subResultWrap.classList.remove("hidden");
  subProcessBtn.disabled = false;
}

function subShowError(msg) {
  subProgressWrap.classList.add("hidden");
  subErrorMsg.textContent = msg;
  subErrorWrap.classList.remove("hidden");
  subProcessBtn.disabled = false;
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
  subResetOutputs();
  updateReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("sub-error-reset-btn").addEventListener("click", () => {
  subErrorWrap.classList.add("hidden");
});
})();
