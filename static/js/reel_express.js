(function () {
// ReelForge — módulo reel_express (pipeline: vertical + audio + mezcla + watermark).
const RX_API = "/api/reel-express";

const rxClipZone = el("rx-clip-zone");
const rxClipInput = el("rx-clip-input");
const rxClipName = el("rx-clip-name");
const rxEditor = el("rx-editor");
const rxVideo = el("rx-video");
const rxUrl = el("rx-url");
const rxProcessBtn = el("rx-process-btn");

// Fuente de audio (link | archivo)
const rxSourceUrl = el("rx-source-url");
const rxSourceFile = el("rx-source-file");
const rxAudioZone = el("rx-audio-zone");
const rxAudioInput = el("rx-audio-input");
const rxAudioName = el("rx-audio-name");

// Preview vertical (canvas) — resolución interna 9:16 (mitad de 1080x1920).
const rxCanvas = el("rx-canvas");
const rxCtx = rxCanvas.getContext("2d");
const RX_SCALE = 0.5;
const RX_W = 1080 * RX_SCALE;
const RX_H = 1920 * RX_SCALE;
const RX_MARGIN = 40;

const rxProgressWrap = el("rx-progress-wrap");
const rxProgressFill = el("rx-progress-fill");
const rxProgressLabel = el("rx-progress-label");
const rxResultWrap = el("rx-result-wrap");
const rxDownloadBtn = el("rx-download-btn");
const rxErrorWrap = el("rx-error-wrap");
const rxErrorMsg = el("rx-error-msg");

// Subtítulos
const rxAddSubs = el("rx-add-subs");
const rxSubsControls = el("rx-subs-controls");
const rxSubsEditor = el("rx-subs-editor");
const rxSegmentsBox = el("rx-segments");
const rxFinishBtn = el("rx-finish-btn");

let rxClipFile = null;
let rxAudioFile = null;
let rxSource = "url";                  // "url" | "file"
let rxRafId = null;
let rxFontFamily = "sans-serif";
let rxSelectedWm = "";                 // nombre del watermark o "" (ninguno)
let rxWmImg = null;                    // Image del watermark seleccionado
let rxPrepJobId = null;                // job de la fase 1 (tiene el video base)
let rxSegments = [];                   // segmentos transcritos (editables)

// --- Cargar la fuente custom para el preview (si hay una en assets/fonts/) ---
fetch("/api/font").then((r) => r.json()).then((info) => {
  if (!info.available) return;
  const ff = new FontFace("WMBrand", "url(/assets/font)");
  ff.load().then((f) => { document.fonts.add(f); rxFontFamily = "WMBrand"; }).catch(() => {});
});

// --- Drag & drop genérico para una zona ---
function wireDropZone(zone, input, onFile, pickerKind) {
  zone.addEventListener("click", () => openFilePicker(pickerKind, input, onFile));
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

// Marca una drop-zone como "cargada" (archivo seleccionado): swap del contenido
// por un check + el nombre. Guarda el HTML original para poder restaurarlo.
function setZoneLoaded(zone, name) {
  const inner = zone.querySelector(".drop-inner");
  if (!zone.dataset.origInner) zone.dataset.origInner = inner.innerHTML;
  zone.classList.add("loaded");
  inner.innerHTML =
    '<p class="drop-icon"><svg class="icon"><use href="#i-check"/></svg></p>' +
    '<p class="drop-loaded-name"></p>' +
    '<p class="drop-loaded-hint">Click para cambiar</p>';
  inner.querySelector(".drop-loaded-name").textContent = name;
}
function clearZoneLoaded(zone) {
  const inner = zone.querySelector(".drop-inner");
  if (zone.dataset.origInner) inner.innerHTML = zone.dataset.origInner;
  zone.classList.remove("loaded");
}

// --- Carga del clip ---
wireDropZone(rxClipZone, rxClipInput, loadClip, "clip");
wireLibraryButton(rxClipZone, loadClip);
function loadClip(file) {
  rxClipFile = file;
  rxVideo.src = previewSrcFor(file);
  rxClipName.textContent = file.name;
  rxClipZone.classList.add("hidden");   // ya no hace falta el input; se cambia con el nombre
  rxEditor.classList.remove("hidden");
  rxResetOutputs();
  updateReady();
  startPreview();
  rxEditor.scrollIntoView({ behavior: "smooth" });
}
// El nombre del clip funciona como "cambiar clip" (reabre el selector).
rxClipName.style.cursor = "pointer";
rxClipName.title = "Click para cambiar el clip";
rxClipName.addEventListener("click", () => openFilePicker("clip", rxClipInput, loadClip));

// --- Carga del audio (fuente archivo) ---
wireDropZone(rxAudioZone, rxAudioInput, (file) => {
  rxAudioFile = file;
  setZoneLoaded(rxAudioZone, file.name);
  rxResetOutputs();
  updateReady();
}, "audio");

// --- Toggle de fuente de audio ---
el("rx-tab-url").addEventListener("click", () => setSource("url"));
el("rx-tab-file").addEventListener("click", () => setSource("file"));
function setSource(src) {
  rxSource = src;
  el("rx-tab-url").classList.toggle("active", src === "url");
  el("rx-tab-file").classList.toggle("active", src === "file");
  rxSourceUrl.classList.toggle("hidden", src !== "url");
  rxSourceFile.classList.toggle("hidden", src !== "file");
  updateReady();
}

function updateReady() {
  const audioReady = rxSource === "url" ? !!rxUrl.value.trim() : !!rxAudioFile;
  rxProcessBtn.disabled = !(rxClipFile && audioReady);
  updateBlockStatus();
}

// --- Bloques colapsables del panel de ajustes ---
// Cada bloque recuerda si lo dejaste abierto o cerrado (localStorage) y su
// cabecera muestra un resumen del estado, así se puede plegar lo que no se
// usa sin perder de vista qué va a hacer el pipeline.
const RX_BLOCKS_KEY = "reelforge-rx-blocks";
const RX_BLOCKS_DEFAULT_OPEN = { video: true, hud: false, audio: true, text: false, subs: true, advanced: false };
const rxBlocks = [...document.querySelectorAll("#rx-editor .rx-block")];
(function initBlocks() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(RX_BLOCKS_KEY) || "{}"); } catch (_) {}
  rxBlocks.forEach((d) => {
    const key = d.dataset.block;
    d.open = key in saved ? !!saved[key] : !!RX_BLOCKS_DEFAULT_OPEN[key];
    d.addEventListener("toggle", () => {
      const state = {};
      rxBlocks.forEach((b) => (state[b.dataset.block] = b.open));
      try { localStorage.setItem(RX_BLOCKS_KEY, JSON.stringify(state)); } catch (_) {}
    });
  });
  // Cualquier cambio en el panel refresca los resúmenes.
  const panel = document.querySelector("#rx-editor .controls-panel");
  panel.addEventListener("input", updateBlockStatus);
  panel.addEventListener("change", updateBlockStatus);
  updateBlockStatus();
})();
function setBlockStatus(key, text, tone) {
  const s = el("rx-status-" + key);
  if (!s) return;
  s.textContent = text;
  s.className = "rx-block-status" + (tone ? " " + tone : "");
}
function updateBlockStatus() {
  const pos = { center: "centro", top: "arriba", bottom: "abajo" }[el("rx-main_clip_position").value];
  const off = +el("rx-main_clip_offset").value;
  setBlockStatus("video", el("rx-convert-vertical").checked
    ? `Vertical · ${pos}${off ? ` ${off > 0 ? "+" : ""}${off}%` : ""}` : "Sin convertir (ya es 9:16)");

  const hudOn = el("rx-hud-enabled").checked && el("rx-convert-vertical").checked;
  setBlockStatus("hud", hudOn ? "Placa activa" : "Apagado", hudOn ? "on" : "");

  if (rxSource === "url") {
    const url = rxUrl.value.trim();
    setBlockStatus("audio", url ? "Link listo" : "Falta el link", url ? "on" : "warn");
  } else {
    setBlockStatus("audio", rxAudioFile ? rxAudioFile.name : "Falta el archivo", rxAudioFile ? "on" : "warn");
  }

  const hasText = !!(el("rx-primary-text").value.trim() || el("rx-secondary-text").value.trim());
  const hasWm = typeof rxSelectedWm === "string" && rxSelectedWm !== "";
  const textParts = [hasText && "texto", hasWm && "marca"].filter(Boolean);
  setBlockStatus("text", textParts.length ? textParts.join(" + ") : "Nada", textParts.length ? "on" : "");

  if (!rxAddSubs.checked) setBlockStatus("subs", "Sin subtítulos");
  else setBlockStatus("subs", el("rx-skip-review").checked ? "Karaoke · sin revisar" : "Karaoke · revisás antes", "on");

  const adv = [["rx-blur_intensity", "50"], ["rx-bg_brightness", "0.5"], ["rx-main_clip_scale", "1.55"],
    ["rx-enhance_intensity", "85"], ["rx-original_volume", "0"], ["rx-new_volume", "100"]]
    .some(([id, def]) => el(id).value !== def) || !el("rx-fade").checked || !el("rx-speed_match").checked;
  setBlockStatus("advanced", adv ? "Modificados" : "Por defecto", adv ? "on" : "");
}
rxUrl.addEventListener("input", updateReady);

// --- Preview vertical en vivo (replica vertical_convert + texto + marca) ---
function startPreview() {
  rxCanvas.width = RX_W;
  rxCanvas.height = RX_H;
  cancelAnimationFrame(rxRafId);
  const loop = () => {
    drawCanvas();
    rxRafId = requestAnimationFrame(loop);
  };
  loop();
}

function drawCanvas() {
  rxCtx.clearRect(0, 0, RX_W, RX_H);
  drawVertical();
  drawTextBlock();
  drawWatermark();
  drawSampleSubtitle();
}

// Subtítulo de muestra (espeja el preview del módulo subtitles), solo si los
// subtítulos están activos. Sirve para ver la posición/estilo en vivo.
function drawSampleSubtitle() {
  if (!rxAddSubs.checked) return;
  const fs = +el("rx-sub-font-size").value;
  const posY = +el("rx-sub-position-y").value;
  const color = el("rx-sub-highlight-color").value;
  const words = +el("rx-sub-words").value;

  const y = (posY / 1920) * RX_H;
  rxCtx.font = `bold ${fs * RX_SCALE}px ${rxFontFamily}`;
  rxCtx.textAlign = "center";
  rxCtx.textBaseline = "bottom";
  rxCtx.lineJoin = "round";

  const samples = ["Ejemplo", "de", "subtítulo", "en", "vivo", "aquí"];
  const group = samples.slice(0, words);
  const activeIdx = Math.min(1, group.length - 1);

  const border = Math.max(2, fs * 0.07) * RX_SCALE;
  rxCtx.lineWidth = border * 2;
  rxCtx.strokeStyle = "black";

  const totalW = rxCtx.measureText(group.join(" ")).width;
  let curX = (RX_W - totalW) / 2;
  group.forEach((word, i) => {
    const wordW = rxCtx.measureText(word).width;
    const spaceW = i < group.length - 1 ? rxCtx.measureText(" ").width : 0;
    const cx = curX + wordW / 2;
    rxCtx.fillStyle = i === activeIdx ? color : "white";
    rxCtx.strokeText(word, cx, y);
    rxCtx.fillText(word, cx, y);
    curX += wordW + spaceW;
  });
}

// Espeja vertical_convert.js: fondo "cover" con blur + brillo, y el clip
// principal escalado por ancho y posicionado, con realce opcional.
// Con la conversión desactivada, el clip (ya 9:16) se dibuja tal cual.
function drawVertical() {
  const vw = rxVideo.videoWidth;
  const vh = rxVideo.videoHeight;
  if (rxVideo.readyState < 2 || !vw || !vh) {
    rxCtx.fillStyle = "#000";
    rxCtx.fillRect(0, 0, RX_W, RX_H);
    return;
  }

  // Sin conversión: el clip llena el lienzo como lo hará la salida.
  if (!el("rx-convert-vertical").checked) {
    const cover = Math.max(RX_W / vw, RX_H / vh);
    const dw = vw * cover;
    const dh = vh * cover;
    rxCtx.filter = "none";
    rxCtx.drawImage(rxVideo, (RX_W - dw) / 2, (RX_H - dh) / 2, dw, dh);
    return;
  }

  const blur = +el("rx-blur_intensity").value;
  const brightness = +el("rx-bg_brightness").value;
  const scale = +el("rx-main_clip_scale").value;
  const position = el("rx-main_clip_position").value;
  const enhance = +el("rx-enhance_intensity").value;

  // Fondo: cubre el lienzo (overscan leve para que el blur no deje bordes).
  const blurPx = blur * (RX_W / 1080);
  rxCtx.filter = `blur(${blurPx}px) brightness(${brightness})`;
  const cover = Math.max(RX_W / vw, RX_H / vh) * 1.08;
  const bw = vw * cover;
  const bh = vh * cover;
  rxCtx.drawImage(rxVideo, (RX_W - bw) / 2, (RX_H - bh) / 2, bw, bh);

  // Clip principal: ajustado por ancho, con realce opcional.
  const fgW = RX_W * scale;
  const fgH = fgW * (vh / vw);
  const fgX = (RX_W - fgW) / 2;
  let fgY;
  if (position === "top") fgY = 0;
  else if (position === "bottom") fgY = RX_H - fgH;
  else fgY = (RX_H - fgH) / 2;
  fgY += (+el("rx-main_clip_offset").value / 100) * RX_H;

  if (enhance > 0) {
    const i = enhance / 100;
    const bright = (1 + i * 0.12).toFixed(2);
    const contrast = (1 + i * 0.10).toFixed(2);
    const sat = (1 + i * 0.50).toFixed(2);
    rxCtx.filter = `brightness(${bright}) contrast(${contrast}) saturate(${sat})`;
  } else {
    rxCtx.filter = "none";
  }
  rxCtx.drawImage(rxVideo, fgX, fgY, fgW, fgH);
  rxCtx.filter = "none";

  // Marcador (HUD): placa redondeada (drawHudPlate en shared.js).
  if (el("rx-hud-enabled").checked) {
    drawHudPlate(rxCtx, rxVideo, vw, vh, RX_W, RX_H, (id) => el(id), "rx-");
  }
}

// Espeja watermark.js: un renglón por línea, con nuestro propio interlineado.
function drawTextBlock() {
  const primary = el("rx-primary-text").value.trim();
  const secondary = el("rx-secondary-text").value.trim();
  if (!primary && !secondary) return;

  const fs = +el("rx-text-size").value;
  const textY = +el("rx-text-y").value;
  const lineH = fs * 1.3;
  const gap = fs * 0.3;
  const border = Math.max(2, fs * 0.07);

  const pLines = primary ? primary.split("\n") : [];
  const sLines = secondary ? secondary.split("\n") : [];
  const both = pLines.length && sLines.length;
  const blockH = pLines.length * lineH + (both ? gap : 0) + sLines.length * lineH;
  const blockTop = (textY / 100) * Math.max(0, 1920 - blockH);

  rxCtx.textAlign = "center";
  rxCtx.textBaseline = "top";
  rxCtx.font = `${fs * RX_SCALE}px ${rxFontFamily}`;
  rxCtx.lineWidth = border * RX_SCALE * 2;
  rxCtx.strokeStyle = "black";
  rxCtx.lineJoin = "round";

  const drawLines = (lines, color, startY) => {
    rxCtx.fillStyle = color;
    lines.forEach((line, i) => {
      const x = RX_W / 2;
      const y = (startY + i * lineH) * RX_SCALE;
      rxCtx.strokeText(line, x, y);
      rxCtx.fillText(line, x, y);
    });
  };
  if (pLines.length) drawLines(pLines, "white", blockTop);
  if (sLines.length) {
    drawLines(sLines, "#ff3b3b", blockTop + (pLines.length ? pLines.length * lineH + gap : 0));
  }
}

function drawWatermark() {
  if (!rxWmImg || !rxWmImg.complete || !rxWmImg.naturalWidth) return;
  const sizePct = +el("rx-wm-size").value;
  const wx = el("rx-wm-x").value;
  const wy = +el("rx-wm-y").value;
  const ww = (1080 * sizePct / 100) * RX_SCALE;
  const wh = ww * (rxWmImg.naturalHeight / rxWmImg.naturalWidth);
  const m = RX_MARGIN * RX_SCALE;
  let x;
  if (wx === "left") x = m;
  else if (wx === "right") x = RX_W - ww - m;
  else x = (RX_W - ww) / 2;
  const y = (wy / 100) * Math.max(0, RX_H - wh);
  rxCtx.drawImage(rxWmImg, x, y, ww, wh);
}

// --- Galería de watermarks (CRUD vía API, compartida con el módulo Watermark) ---
const rxGallery = el("rx-gallery");

function loadWatermarks() {
  fetch("/api/watermarks").then((r) => r.json()).then((data) => {
    renderGallery(data.watermarks || []);
  });
}

function renderGallery(items) {
  rxGallery.innerHTML = "";
  const none = document.createElement("button");
  none.type = "button";
  none.className = "wm-none" + (rxSelectedWm === "" ? " selected" : "");
  none.textContent = "Sin marca";
  none.addEventListener("click", () => selectWatermark("", null));
  rxGallery.appendChild(none);

  items.forEach((item) => {
    const tile = document.createElement("div");
    tile.className = "wm-tile" + (rxSelectedWm === item.name ? " selected" : "");
    tile.innerHTML = `<img src="${item.url}" alt="${item.name}">
      <button type="button" class="wm-del" title="Borrar">✕</button>`;
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
          if (rxSelectedWm === item.name) selectWatermark("", null);
          loadWatermarks();
        });
    });
    rxGallery.appendChild(tile);
  });
}

function selectWatermark(name, img) {
  rxSelectedWm = name;
  rxWmImg = img;
  loadWatermarks();
  updateBlockStatus();
}

el("rx-wm-upload-btn").addEventListener("click", () => el("rx-wm-upload-input").click());
el("rx-wm-upload-input").addEventListener("change", (e) => {
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
const rxLabels = {
  "rx-text-size": "rx-text-size-val",
  "rx-text-y": "rx-text-y-val",
  "rx-wm-size": "rx-wm-size-val",
  "rx-wm-y": "rx-wm-y-val",
  "rx-blur_intensity": "rx-blur-val",
  "rx-bg_brightness": "rx-brightness-val",
  "rx-main_clip_scale": "rx-scale-val",
  "rx-enhance_intensity": "rx-enhance-val",
  "rx-main_clip_offset": "rx-offset-val",
  "rx-hud_left": "rx-hud-left-val",
  "rx-hud_right": "rx-hud-right-val",
  "rx-hud_top": "rx-hud-top-val",
  "rx-hud_bottom": "rx-hud-bottom-val",
  "rx-hud_scale": "rx-hud-scale-val",
  "rx-hud_pos_y": "rx-hud-pos-val",
  "rx-original_volume": "rx-vo-val",
  "rx-new_volume": "rx-vn-val",
};
Object.entries(rxLabels).forEach(([inputId, labelId]) => {
  const input = el(inputId);
  input.addEventListener("input", () => (el(labelId).textContent = input.value));
});

// --- Marcador (HUD): toggle de sus controles ---
el("rx-hud-enabled").addEventListener("change", () => {
  el("rx-hud-controls").classList.toggle(
    "hidden", !el("rx-hud-enabled").checked);
});

// --- Subtítulos: toggle + labels en vivo ---
rxAddSubs.addEventListener("change", () => {
  rxSubsControls.classList.toggle("hidden", !rxAddSubs.checked);
});
el("rx-sub-font-size").addEventListener("input", () => {
  el("rx-sub-font-size-val").textContent = el("rx-sub-font-size").value;
});
el("rx-sub-position-y").addEventListener("input", () => {
  el("rx-sub-position-y-val").textContent = el("rx-sub-position-y").value;
});
el("rx-sub-words").addEventListener("input", () => {
  el("rx-sub-words-val").textContent = el("rx-sub-words").value;
});
el("rx-sub-highlight-color").addEventListener("input", () => {
  el("rx-sub-color-preview").style.background = el("rx-sub-highlight-color").value;
});

// --- Process ---
rxProcessBtn.addEventListener("click", () => {
  const audioReady = rxSource === "url" ? !!rxUrl.value.trim() : !!rxAudioFile;
  if (!rxClipFile || !audioReady) return;

  const form = new FormData();
  appendClip(form, "clip", rxClipFile);
  form.append("audio_source", rxSource);
  if (rxSource === "url") {
    form.append("url", rxUrl.value.trim());
    form.append("quality", el("rx-quality").value);
  } else {
    form.append("audio", rxAudioFile);
  }
  // Video vertical
  form.append("convert_vertical", el("rx-convert-vertical").checked ? "1" : "0");
  form.append("blur_intensity", el("rx-blur_intensity").value);
  form.append("bg_brightness", el("rx-bg_brightness").value);
  form.append("main_clip_scale", el("rx-main_clip_scale").value);
  form.append("enhance_intensity", el("rx-enhance_intensity").value);
  form.append("main_clip_position", el("rx-main_clip_position").value);
  form.append("main_clip_offset", el("rx-main_clip_offset").value);
  form.append("hud_enabled", el("rx-hud-enabled").checked ? "1" : "0");
  ["hud_left", "hud_right", "hud_top", "hud_bottom", "hud_scale", "hud_pos_y"]
    .forEach((k) => form.append(k, el("rx-" + k).value));
  // Mezcla
  form.append("original_volume", el("rx-original_volume").value);
  form.append("new_volume", el("rx-new_volume").value);
  form.append("fade", el("rx-fade").checked ? "1" : "0");
  form.append("speed_match", el("rx-speed_match").checked ? "1" : "0");
  // Texto y marca (vacíos => sin watermark en el backend)
  form.append("primary_text", el("rx-primary-text").value);
  form.append("secondary_text", el("rx-secondary-text").value);
  form.append("text_size", el("rx-text-size").value);
  form.append("text_y", el("rx-text-y").value);
  form.append("watermark", rxSelectedWm);
  form.append("watermark_x", el("rx-wm-x").value);
  form.append("watermark_size", el("rx-wm-size").value);
  form.append("watermark_y", el("rx-wm-y").value);
  // Nombre del archivo final
  form.append("output_name", el("rx-output-name").value);
  // Subtítulos
  form.append("add_subtitles", rxAddSubs.checked ? "1" : "0");
  if (rxAddSubs.checked) {
    // El backend usa skip_review para el camino rápido de un solo encode.
    form.append("skip_review", el("rx-skip-review").checked ? "1" : "0");
    form.append("language", el("rx-sub-language").value);
    form.append("model", el("rx-sub-model").value);
    form.append("font_size", el("rx-sub-font-size").value);
    form.append("position_y", el("rx-sub-position-y").value);
    form.append("words_per_line", el("rx-sub-words").value);
    form.append("highlight_color", el("rx-sub-highlight-color").value);
  }
  rxResetOutputs();
  rxSubsEditor.classList.add("hidden");
  rxProcessBtn.disabled = true;
  rxProgressWrap.classList.remove("hidden");
  rxSetProgress(0, "Iniciando…");

  fetch(`${RX_API}/process`, { method: "POST", body: form })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      rxPrepJobId = data.job_id;
      rxPollJob(data.job_id, {
        onProgress: rxSetProgress,
        onDone: (jobId, d) => {
          // Camino rápido del backend ("No revisar"): la fase 1 ya trae el
          // reel final con los subs quemados; no hay /finish que llamar.
          if (d.finished) {
            rxShowResult(jobId);
            return;
          }
          if (rxAddSubs.checked) {
            rxSegments = d.segments || [];
            if (el("rx-skip-review").checked) {
              // Fallback sin camino rápido (p. ej. sin speed match): generar
              // directo con la transcripción tal cual, como antes.
              rxRunFinish(rxSegments);
            } else {
              // Fase 1 lista: mostrar el editor de subtítulos para revisar/corregir.
              rxProgressWrap.classList.add("hidden");
              rxSubsEditor.classList.remove("hidden");
              SubtitleEditor.render(rxSegmentsBox, rxSegments);
              rxProcessBtn.disabled = false;
              rxSubsEditor.scrollIntoView({ behavior: "smooth" });
            }
          } else {
            rxShowResult(jobId);
          }
        },
        onError: rxShowError,
      });
    })
    .catch((err) => rxShowError(err.message));
});

// Poller propio (status/<id> trae segments + stage, que runJob no expone).
function rxPollJob(jobId, { onProgress, onDone, onError }) {
  const timer = setInterval(async () => {
    try {
      const res = await fetch(`${RX_API}/status/${jobId}`);
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

// --- Fase 2: generar el reel final con los segmentos dados (editados o crudos) ---
rxFinishBtn.addEventListener("click", () => {
  rxRunFinish(SubtitleEditor.collect(rxSegments));
});

function rxRunFinish(segments) {
  if (!rxPrepJobId || !segments.length) return;

  const payload = {
    job_id: rxPrepJobId,
    segments,
    output_name: el("rx-output-name").value,
    font_size: el("rx-sub-font-size").value,
    position_y: el("rx-sub-position-y").value,
    words_per_line: el("rx-sub-words").value,
    highlight_color: el("rx-sub-highlight-color").value,
  };

  rxResetOutputs();
  rxFinishBtn.disabled = true;
  rxProgressWrap.classList.remove("hidden");
  rxSetProgress(0, "Generando…");

  fetch(`${RX_API}/finish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      rxPollJob(data.job_id, {
        onProgress: rxSetProgress,
        onDone: (jobId) => { rxShowResult(jobId); rxFinishBtn.disabled = false; },
        onError: (msg) => { rxShowError(msg); rxFinishBtn.disabled = false; },
      });
    })
    .catch((err) => { rxShowError(err.message); rxFinishBtn.disabled = false; });
}

// rxPollJob llama onProgress(progress, stage?) — el status de reel_express
// manda también la etapa actual para mostrarla.
function rxSetProgress(pct, stage) {
  rxProgressFill.style.width = `${pct}%`;
  const label = stage || "Procesando…";
  rxProgressLabel.textContent = `${label} ${pct}%`;
}

function rxShowResult(jobId) {
  rxProgressWrap.classList.add("hidden");
  rxSubsEditor.classList.add("hidden");
  rxDownloadBtn.href = `${RX_API}/download/${jobId}`;
  rxResultWrap.classList.remove("hidden");
  rxProcessBtn.disabled = false;
}

function rxShowError(msg) {
  rxProgressWrap.classList.add("hidden");
  rxErrorMsg.textContent = msg;
  rxErrorWrap.classList.remove("hidden");
  rxProcessBtn.disabled = false;
}

function rxResetOutputs() {
  rxProgressWrap.classList.add("hidden");
  rxResultWrap.classList.add("hidden");
  rxErrorWrap.classList.add("hidden");
}

// --- Reset ---
function rxResetAll() {
  cancelAnimationFrame(rxRafId);
  rxCtx.clearRect(0, 0, RX_W, RX_H);
  rxClipFile = null;
  rxAudioFile = null;
  rxClipInput.value = "";
  rxAudioInput.value = "";
  rxVideo.src = "";
  rxClipName.textContent = "";
  rxAudioName.textContent = "";
  clearZoneLoaded(rxAudioZone);
  rxUrl.value = "";
  el("rx-output-name").value = "";
  rxSubsEditor.classList.add("hidden");
  rxPrepJobId = null;
  rxSegments = [];
  rxClipZone.classList.remove("hidden");
  rxEditor.classList.add("hidden");
  rxResetOutputs();
  updateReady();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
// "Hacer otro" (tras el resultado): reset directo.
el("rx-reset-btn").addEventListener("click", rxResetAll);
// "Empezar de nuevo" (siempre visible en el editor): confirma antes, porque
// puede dispararse en plena edición.
el("rx-restart-btn").addEventListener("click", () => {
  if (confirm("¿Empezar de nuevo? Se quitará el clip y el audio cargados.")) rxResetAll();
});
el("rx-error-reset-btn").addEventListener("click", () => {
  rxErrorWrap.classList.add("hidden");
});
})();
