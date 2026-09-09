(function () {
// ReelForge — módulo watermark (texto + marca sobre el video).
const WM_API = "/api/watermark";

const wmVideoZone = el("wm-video-zone");
const wmVideoInput = el("wm-video-input");
const wmEditor = el("wm-editor");
const wmVideo = el("wm-video");
const wmVideoName = el("wm-video-name");
const wmProcessBtn = el("wm-process-btn");

const wmCanvas = el("wm-canvas");
const wmCtx = wmCanvas.getContext("2d");
const WM_SCALE = 0.5;                 // lienzo interno = 1080x1920 * 0.5
const WM_W = 1080 * WM_SCALE;
const WM_H = 1920 * WM_SCALE;
const WM_MARGIN = 40;

const wmProgressWrap = el("wm-progress-wrap");
const wmProgressFill = el("wm-progress-fill");
const wmProgressLabel = el("wm-progress-label");
const wmResultWrap = el("wm-result-wrap");
const wmDownloadBtn = el("wm-download-btn");
const wmErrorWrap = el("wm-error-wrap");
const wmErrorMsg = el("wm-error-msg");

let wmVideoFile = null;
let wmRafId = null;
let wmFontFamily = "sans-serif";
let wmSelected = "";                  // nombre del watermark o "" (ninguno)
let wmImg = null;                     // Image del watermark seleccionado

// --- Cargar la fuente custom para el preview (si hay una en assets/fonts/) ---
fetch("/api/font").then((r) => r.json()).then((info) => {
  if (!info.available) return;
  const ff = new FontFace("WMBrand", "url(/assets/font)");
  ff.load().then((f) => { document.fonts.add(f); wmFontFamily = "WMBrand"; }).catch(() => {});
});

// --- Carga del video (click + drag & drop) ---
wmVideoZone.addEventListener("click", () => wmVideoInput.click());
wmVideoInput.addEventListener("change", (e) => {
  if (e.target.files.length) loadVideo(e.target.files[0]);
});
["dragenter", "dragover"].forEach((evt) =>
  wmVideoZone.addEventListener(evt, (e) => {
    e.preventDefault();
    wmVideoZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  wmVideoZone.addEventListener(evt, (e) => {
    e.preventDefault();
    wmVideoZone.classList.remove("dragover");
  })
);
wmVideoZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) loadVideo(file);
});

function loadVideo(file) {
  wmVideoFile = file;
  wmVideo.src = URL.createObjectURL(file);
  wmVideoName.textContent = file.name;
  wmEditor.classList.remove("hidden");
  wmResetOutputs();
  updateReady();
  startPreview();
  wmEditor.scrollIntoView({ behavior: "smooth" });
}

function updateReady() {
  wmProcessBtn.disabled = !wmVideoFile;
}

// --- Preview en canvas (frame + texto + watermark) ---
function startPreview() {
  wmCanvas.width = WM_W;
  wmCanvas.height = WM_H;
  cancelAnimationFrame(wmRafId);
  const loop = () => {
    drawCanvas();
    wmRafId = requestAnimationFrame(loop);
  };
  loop();
}

function drawCanvas() {
  wmCtx.clearRect(0, 0, WM_W, WM_H);
  if (wmVideo.readyState >= 2 && wmVideo.videoWidth) {
    wmCtx.drawImage(wmVideo, 0, 0, WM_W, WM_H);
  } else {
    wmCtx.fillStyle = "#000";
    wmCtx.fillRect(0, 0, WM_W, WM_H);
  }
  drawTextBlock();
  drawWatermark();
}

function drawTextBlock() {
  const primary = el("wm-primary-text").value.trim();
  const secondary = el("wm-secondary-text").value.trim();
  if (!primary && !secondary) return;

  const fs = +el("wm-text-size").value;
  const textY = +el("wm-text-y").value;
  const lineH = fs * 1.3;
  const gap = fs * 0.3;
  const border = Math.max(2, fs * 0.07);

  const pLines = primary ? primary.split("\n") : [];
  const sLines = secondary ? secondary.split("\n") : [];
  const pHeight = pLines.length * lineH;
  const sHeight = sLines.length * lineH;
  const both = pLines.length && sLines.length;
  const blockH = pHeight + (both ? gap : 0) + sHeight;
  const blockTop = (textY / 100) * Math.max(0, 1920 - blockH);

  wmCtx.textAlign = "center";
  wmCtx.textBaseline = "top";
  wmCtx.font = `${fs * WM_SCALE}px ${wmFontFamily}`;
  wmCtx.lineWidth = border * WM_SCALE * 2;
  wmCtx.strokeStyle = "black";
  wmCtx.lineJoin = "round";

  const drawLines = (lines, color, startY) => {
    wmCtx.fillStyle = color;
    lines.forEach((line, i) => {
      const x = WM_W / 2;
      const y = (startY + i * lineH) * WM_SCALE;
      wmCtx.strokeText(line, x, y);
      wmCtx.fillText(line, x, y);
    });
  };
  if (pLines.length) drawLines(pLines, "white", blockTop);
  if (sLines.length) drawLines(sLines, "#ff3b3b", blockTop + (pLines.length ? pHeight + gap : 0));
}

function drawWatermark() {
  if (!wmImg || !wmImg.complete || !wmImg.naturalWidth) return;
  const sizePct = +el("wm-size").value;
  const wx = el("wm-x").value;
  const wy = +el("wm-y").value;
  const ww = (1080 * sizePct / 100) * WM_SCALE;
  const wh = ww * (wmImg.naturalHeight / wmImg.naturalWidth);
  const m = WM_MARGIN * WM_SCALE;
  let x;
  if (wx === "left") x = m;
  else if (wx === "right") x = WM_W - ww - m;
  else x = (WM_W - ww) / 2;
  const y = (wy / 100) * (WM_H - wh);
  wmCtx.drawImage(wmImg, x, y, ww, wh);
}

// --- Galería de watermarks (CRUD vía API) ---
const wmGallery = el("wm-gallery");

function loadWatermarks() {
  fetch("/api/watermarks").then((r) => r.json()).then((data) => {
    renderGallery(data.watermarks || []);
  });
}

function renderGallery(items) {
  wmGallery.innerHTML = "";
  const none = document.createElement("button");
  none.className = "wm-none" + (wmSelected === "" ? " selected" : "");
  none.textContent = "Sin marca";
  none.addEventListener("click", () => selectWatermark("", null));
  wmGallery.appendChild(none);

  items.forEach((item) => {
    const tile = document.createElement("div");
    tile.className = "wm-tile" + (wmSelected === item.name ? " selected" : "");
    tile.innerHTML = `<img src="${item.url}" alt="${item.name}">
      <button class="wm-del" title="Borrar">✕</button>`;
    tile.querySelector("img").addEventListener("click", () => {
      const img = new Image();
      img.src = item.url;
      selectWatermark(item.name, img);
    });
    tile.querySelector(".wm-del").addEventListener("click", (e) => {
      e.stopPropagation();
      if (!confirm(`¿Borrar "${item.name}"?`)) return;
      fetch(`/api/watermarks/${encodeURIComponent(item.name)}`, { method: "DELETE" })
        .then(() => {
          if (wmSelected === item.name) selectWatermark("", null);
          loadWatermarks();
        });
    });
    wmGallery.appendChild(tile);
  });
}

function selectWatermark(name, img) {
  wmSelected = name;
  wmImg = img;
  loadWatermarks();
}

el("wm-upload-btn").addEventListener("click", () => el("wm-upload-input").click());
el("wm-upload-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  fetch("/api/watermarks", { method: "POST", body: form })
    .then(async (r) => {
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Error al subir");
      const img = new Image();
      img.src = data.url;
      selectWatermark(data.name, img);
    })
    .catch((err) => alert(err.message));
  e.target.value = "";
});

loadWatermarks();

// --- Sliders: labels en vivo ---
el("wm-text-size").addEventListener("input", () => {
  el("wm-text-size-val").textContent = el("wm-text-size").value;
});
el("wm-text-y").addEventListener("input", () => {
  el("wm-text-y-val").textContent = el("wm-text-y").value;
});
el("wm-size").addEventListener("input", () => {
  el("wm-size-val").textContent = el("wm-size").value;
});
el("wm-y").addEventListener("input", () => {
  el("wm-y-val").textContent = el("wm-y").value;
});

// --- Process ---
wmProcessBtn.addEventListener("click", () => {
  if (!wmVideoFile) return;

  const form = new FormData();
  form.append("video", wmVideoFile);
  form.append("primary_text", el("wm-primary-text").value);
  form.append("secondary_text", el("wm-secondary-text").value);
  form.append("text_size", el("wm-text-size").value);
  form.append("text_y", el("wm-text-y").value);
  form.append("watermark", wmSelected);
  form.append("watermark_x", el("wm-x").value);
  form.append("watermark_size", el("wm-size").value);
  form.append("watermark_y", el("wm-y").value);

  wmResetOutputs();
  wmProcessBtn.disabled = true;
  wmProgressWrap.classList.remove("hidden");
  wmSetProgress(0);

  runJob(WM_API, form, {
    onProgress: wmSetProgress,
    onDone: wmShowResult,
    onError: wmShowError,
  });
});

function wmSetProgress(pct) {
  wmProgressFill.style.width = `${pct}%`;
  wmProgressLabel.textContent = `Procesando… ${pct}%`;
}

function wmShowResult(jobId) {
  wmProgressWrap.classList.add("hidden");
  wmDownloadBtn.href = `${WM_API}/download/${jobId}`;
  wmResultWrap.classList.remove("hidden");
  wmProcessBtn.disabled = false;
}

function wmShowError(msg) {
  wmProgressWrap.classList.add("hidden");
  wmErrorMsg.textContent = msg;
  wmErrorWrap.classList.remove("hidden");
  wmProcessBtn.disabled = false;
}

function wmResetOutputs() {
  wmProgressWrap.classList.add("hidden");
  wmResultWrap.classList.add("hidden");
  wmErrorWrap.classList.add("hidden");
}

// --- Reset ---
el("wm-reset-btn").addEventListener("click", () => {
  cancelAnimationFrame(wmRafId);
  wmCtx.clearRect(0, 0, WM_W, WM_H);
  wmVideoFile = null;
  wmVideoInput.value = "";
  wmVideo.src = "";
  wmVideoName.textContent = "";
  wmEditor.classList.add("hidden");
  wmResetOutputs();
  updateReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("wm-error-reset-btn").addEventListener("click", () => {
  wmErrorWrap.classList.add("hidden");
});
})();
