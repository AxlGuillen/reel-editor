// ReelForge — módulo vertical_convert (conversión 16:9 → 9:16).
const API = "/api/vertical-convert";

const dropZone = el("upload-zone");
const fileInput = el("file-input");
const editor = el("editor");
const preview = el("preview");
const fileName = el("file-name");
const processBtn = el("process-btn");

const progressWrap = el("progress-wrap");
const progressFill = el("progress-fill");
const progressLabel = el("progress-label");

const resultWrap = el("result-wrap");
const downloadBtn = el("download-btn");
const errorWrap = el("error-wrap");
const errorMsg = el("error-msg");

// Preview vertical en vivo (canvas). Resolución interna 9:16 — mitad de
// 1080x1920 para rendir bien en el rAF loop; se muestra escalada por CSS.
const vCanvas = el("vertical-preview");
const vCtx = vCanvas.getContext("2d");
const CANVAS_W = 540;
const CANVAS_H = 960;
let rafId = null;

let selectedFile = null;

// --- Upload: click + drag & drop ---
dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => {
  if (e.target.files.length) loadFile(e.target.files[0]);
});

["dragenter", "dragover"].forEach((evt) =>
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
  })
);
dropZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) loadFile(file);
});

function loadFile(file) {
  selectedFile = file;
  preview.src = URL.createObjectURL(file);
  fileName.textContent = file.name;
  editor.classList.remove("hidden");
  resetOutputs();
  startPreview();
  editor.scrollIntoView({ behavior: "smooth" });
}

// --- Preview vertical en vivo ---
// Replica la lógica de processor.py: fondo "cover" con blur + brillo, y el
// clip principal escalado por ancho y posicionado. Se redibuja cada frame,
// así refleja al instante tanto el video reproduciéndose como los sliders.
function startPreview() {
  vCanvas.width = CANVAS_W;
  vCanvas.height = CANVAS_H;
  cancelAnimationFrame(rafId);
  const loop = () => {
    drawVerticalPreview();
    rafId = requestAnimationFrame(loop);
  };
  loop();
}

function drawVerticalPreview() {
  const vw = preview.videoWidth;
  const vh = preview.videoHeight;
  if (preview.readyState < 2 || !vw || !vh) return;

  const blur = +el("blur_intensity").value;
  const brightness = +el("bg_brightness").value;
  const scale = +el("main_clip_scale").value;
  const position = el("main_clip_position").value;

  // Fondo: cubre todo el lienzo (con un leve overscan para que el blur no
  // deje bordes), aplicando blur y brillo.
  const blurPx = blur * (CANVAS_W / 1080); // sigma de FFmpeg ≈ px en este lienzo
  vCtx.filter = `blur(${blurPx}px) brightness(${brightness})`;
  const cover = Math.max(CANVAS_W / vw, CANVAS_H / vh) * 1.08;
  const bw = vw * cover;
  const bh = vh * cover;
  vCtx.drawImage(preview, (CANVAS_W - bw) / 2, (CANVAS_H - bh) / 2, bw, bh);

  // Clip principal: ajustado por ancho del lienzo, con realce opcional.
  const enhance = +el("enhance_intensity").value;
  const fgW = CANVAS_W * scale;
  const fgH = fgW * (vh / vw);
  const fgX = (CANVAS_W - fgW) / 2;
  let fgY;
  if (position === "top") fgY = 0;
  else if (position === "bottom") fgY = CANVAS_H - fgH;
  else fgY = (CANVAS_H - fgH) / 2;

  if (enhance > 0) {
    const i = enhance / 100;
    const bright   = (1 + i * 0.12).toFixed(2);
    const contrast = (1 + i * 0.10).toFixed(2);
    const sat      = (1 + i * 0.50).toFixed(2);
    vCtx.filter = `brightness(${bright}) contrast(${contrast}) saturate(${sat})`;
  } else {
    vCtx.filter = "none";
  }
  vCtx.drawImage(preview, fgX, fgY, fgW, fgH);
  vCtx.filter = "none";
}

// --- Sliders: live value labels ---
const liveLabels = {
  blur_intensity: "blur-val",
  bg_brightness: "brightness-val",
  main_clip_scale: "scale-val",
  enhance_intensity: "enhance-val",
};
Object.entries(liveLabels).forEach(([inputId, labelId]) => {
  const input = el(inputId);
  input.addEventListener("input", () => (el(labelId).textContent = input.value));
});

// --- Process ---
processBtn.addEventListener("click", () => {
  if (!selectedFile) return;

  const form = new FormData();
  form.append("video", selectedFile);
  form.append("blur_intensity", el("blur_intensity").value);
  form.append("bg_brightness", el("bg_brightness").value);
  form.append("main_clip_scale", el("main_clip_scale").value);
  form.append("main_clip_position", el("main_clip_position").value);
  form.append("enhance_intensity", el("enhance_intensity").value);

  resetOutputs();
  processBtn.disabled = true;
  progressWrap.classList.remove("hidden");
  setProgress(0);

  runJob(API, form, {
    onProgress: setProgress,
    onDone: showResult,
    onError: showError,
  });
});

function setProgress(pct) {
  progressFill.style.width = `${pct}%`;
  progressLabel.textContent = `Procesando… ${pct}%`;
}

function showResult(jobId) {
  progressWrap.classList.add("hidden");
  downloadBtn.href = `${API}/download/${jobId}`;
  resultWrap.classList.remove("hidden");
  processBtn.disabled = false;
}

function showError(msg) {
  progressWrap.classList.add("hidden");
  errorMsg.textContent = msg;
  errorWrap.classList.remove("hidden");
  processBtn.disabled = false;
}

function resetOutputs() {
  progressWrap.classList.add("hidden");
  resultWrap.classList.add("hidden");
  errorWrap.classList.add("hidden");
}

// --- Reset buttons ---
el("reset-btn").addEventListener("click", fullReset);
el("error-reset-btn").addEventListener("click", () => {
  errorWrap.classList.add("hidden");
});

function fullReset() {
  selectedFile = null;
  fileInput.value = "";
  preview.src = "";
  cancelAnimationFrame(rafId);
  vCtx.clearRect(0, 0, CANVAS_W, CANVAS_H);
  editor.classList.add("hidden");
  resetOutputs();
  processBtn.disabled = false;
  window.scrollTo({ top: 0, behavior: "smooth" });
}
