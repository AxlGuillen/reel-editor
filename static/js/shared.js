// ReelForge — helpers compartidos entre módulos (Vanilla JS).

const el = (id) => document.getElementById(id);

// Envía el form al endpoint /process de un módulo y hace polling de su status.
// callbacks: { onProgress(pct, stage?), onDone(jobId), onError(msg) }
// `stage` es opcional (lo manda reel_express); el resto de módulos lo ignora.
// Lee la respuesta como JSON; si el server devolvió HTML u otra cosa (p. ej.
// un 500), no revienta: arma un mensaje claro con el código HTTP.
async function _readJson(res) {
  try {
    return await res.json();
  } catch {
    return { error: `El servidor devolvió una respuesta inesperada (HTTP ${res.status}). Revisá la consola del servidor.` };
  }
}

function runJob(apiBase, formData, { onProgress, onDone, onError }) {
  fetch(`${apiBase}/process`, { method: "POST", body: formData })
    .then(async (res) => {
      const data = await _readJson(res);
      if (!res.ok) throw new Error(data.error || `Error al iniciar el proceso (HTTP ${res.status})`);
      poll(data.job_id);
    })
    .catch((err) => onError(err.message));

  function poll(jobId) {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/status/${jobId}`);
        const data = await _readJson(res);
        if (!res.ok) throw new Error(data.error || `Error consultando status (HTTP ${res.status})`);

        onProgress(data.progress, data.stage);

        if (data.status === "done") {
          clearInterval(timer);
          onDone(jobId);
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
}

/** Dibuja la placa del marcador en un canvas 9:16 (compartido por módulos). */
function drawHudPlate(ctx, video, vw, vh, cw, ch, get, pfx = "") {
  const left = +get(pfx + "hud_left").value, right = +get(pfx + "hud_right").value;
  const top = +get(pfx + "hud_top").value, bottom = +get(pfx + "hud_bottom").value;
  const sx = vw * (left / 100), sy = vh * (top / 100);
  const sw = vw * ((right - left) / 100), sh = vh * ((bottom - top) / 100);
  if (sw < 2 || sh < 2) return;
  const s = cw / 1080;                 // escala px lienzo → px canvas
  const border = 4 * s, radius = 18 * s;
  const dw = cw * (+get(pfx + "hud_scale").value / 100);
  const dh = dw * (sh / sw);
  const px = (cw - dw - 2 * border) / 2;
  // La placa acompaña el ajuste fino del clip (mismo desplazamiento que en el
  // processor), para que mover el video no obligue a reacomodar el marcador.
  const offEl = get(pfx + "main_clip_offset");
  const offPx = offEl ? (+offEl.value / 100) * ch : 0;
  const py = (ch - dh - 2 * border) * (+get(pfx + "hud_pos_y").value / 100) + offPx;
  ctx.save();
  // Placa blanca (borde) con sombra suave desplazada hacia abajo.
  ctx.shadowColor = "rgba(0,0,0,0.55)";
  ctx.shadowBlur = 14 * s;
  ctx.shadowOffsetY = 6 * s;
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.roundRect(px, py, dw + 2 * border, dh + 2 * border, radius);
  ctx.fill();
  ctx.shadowColor = "transparent";
  // Recorte del video, con las esquinas redondeadas por dentro del borde.
  ctx.beginPath();
  ctx.roundRect(px + border, py + border, dw, dh, Math.max(0, radius - border));
  ctx.clip();
  ctx.drawImage(video, sx, sy, sw, sh, px + border, py + border, dw, dh);
  ctx.restore();
}

// --- Selector de archivos con carpeta inicial ---
// Un <input type="file"> no puede elegir en qué carpeta abre. La File System
// Access API (Chrome/Edge) sí: `startIn` fija la carpeta inicial y `id` hace
// que el navegador RECUERDE la última carpeta usada por cada tipo de selector.
// Así el audio abre en Descargas, y los clips en Videos la primera vez y en
// la carpeta que hayas usado (p. ej. Overwolf/Insights Capture) las siguientes.
// En navegadores sin la API (Firefox) cae al input clásico.
const FILE_PICKERS = {
  clip: {
    id: "reelforge-clips", startIn: "videos",
    types: [{ description: "Video", accept: {
      "video/mp4": [".mp4"], "video/quicktime": [".mov"],
      "video/x-matroska": [".mkv"], "video/x-msvideo": [".avi"] } }],
  },
  audio: {
    id: "reelforge-audio", startIn: "downloads",
    types: [{ description: "Audio o video", accept: {
      "audio/mpeg": [".mp3"], "audio/wav": [".wav"], "audio/mp4": [".m4a"],
      "audio/aac": [".aac"], "audio/ogg": [".ogg"], "video/mp4": [".mp4"],
      "video/quicktime": [".mov"], "video/x-matroska": [".mkv"] } }],
  },
};
function openFilePicker(kind, fallbackInput, onFile) {
  const opts = FILE_PICKERS[kind];
  if (!window.showOpenFilePicker || !opts) { fallbackInput.click(); return; }
  window.showOpenFilePicker({ ...opts, multiple: false })
    .then(([handle]) => handle.getFile())
    .then((file) => { if (file) onFile(file); })
    .catch((err) => {
      // AbortError = el usuario canceló. Cualquier otro fallo: input clásico.
      if (err && err.name !== "AbortError") fallbackInput.click();
    });
}

// --- Librería de clips (carpeta local de capturas, listada por el server) ---
// El archivo NO se sube: el módulo manda `library_clip` (el nombre) y el server
// lo procesa in situ. Para el preview, el <video> lee /api/library/.../file
// por Range. Un "clip de librería" es { name, url, fromLibrary: true }.
function fmtBytes(n) {
  if (n >= 1e9) return (n / 1e9).toFixed(2) + " GB";
  if (n >= 1e6) return (n / 1e6).toFixed(0) + " MB";
  return Math.round(n / 1e3) + " KB";
}
function fmtDuration(s) {
  if (s == null) return "";
  s = Math.round(s);
  const m = Math.floor(s / 60), r = s % 60;
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}:${String(r).padStart(2, "0")}`;
}
function fmtDate(ts) {
  return new Date(ts * 1000).toLocaleString([], {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
let _libModal = null;
function openClipLibrary(onPick) {
  if (!_libModal) {
    _libModal = document.createElement("div");
    _libModal.className = "lib-modal hidden";
    _libModal.innerHTML = `
      <div class="lib-backdrop"></div>
      <div class="lib-dialog card" role="dialog" aria-label="Librería de clips">
        <div class="lib-head">
          <div>
            <h2>Capturas</h2>
            <p class="hint lib-folder"></p>
          </div>
          <button type="button" class="lib-close" aria-label="Cerrar">&times;</button>
        </div>
        <div class="lib-grid"></div>
      </div>`;
    document.body.appendChild(_libModal);
    const close = () => _libModal.classList.add("hidden");
    _libModal.querySelector(".lib-backdrop").addEventListener("click", close);
    _libModal.querySelector(".lib-close").addEventListener("click", close);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !_libModal.classList.contains("hidden")) close();
    });
  }
  const grid = _libModal.querySelector(".lib-grid");
  const folderEl = _libModal.querySelector(".lib-folder");
  grid.innerHTML = '<p class="hint lib-empty">Leyendo la carpeta…</p>';
  _libModal.classList.remove("hidden");

  fetch("/api/library/clips")
    .then((r) => r.json())
    .then((data) => {
      folderEl.textContent = data.folder;
      if (!data.available) {
        grid.innerHTML = '<p class="hint lib-empty">La carpeta no existe en esta compu.</p>';
        return;
      }
      if (!data.clips.length) {
        grid.innerHTML = '<p class="hint lib-empty">No hay videos en la carpeta.</p>';
        return;
      }
      grid.innerHTML = "";
      data.clips.forEach((c) => {
        // div y no <button>: Chrome no deja que un botón crezca con su
        // contenido en flex/grid y recortaba el nombre.
        const tile = document.createElement("div");
        tile.className = "lib-tile" + (c.ok ? "" : " broken");
        tile.title = c.ok ? c.name : `${c.name} — archivo dañado o incompleto`;
        tile.innerHTML = `
          <img loading="lazy" alt="">
          <span class="lib-dur">${c.ok ? fmtDuration(c.duration) : "dañado"}</span>
          <span class="lib-name">${c.name.replace(/\.[^.]+$/, "")}</span>
          <span class="lib-meta">${fmtDate(c.mtime)} · ${fmtBytes(c.size)}</span>`;
        const img = tile.querySelector("img");
        if (c.ok) img.src = c.thumb;
        img.addEventListener("error", () => { img.removeAttribute("src"); });
        if (!c.ok) { grid.appendChild(tile); return; }
        tile.tabIndex = 0;
        tile.setAttribute("role", "button");
        const pick = () => {
          _libModal.classList.add("hidden");
          onPick({ name: c.name, url: c.url, fromLibrary: true });
        };
        tile.addEventListener("click", pick);
        tile.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); }
        });
        grid.appendChild(tile);
      });
    })
    .catch(() => {
      grid.innerHTML = '<p class="hint lib-empty">No se pudo leer la carpeta.</p>';
    });
}
// Botón "Elegir de capturas" dentro de una drop zone: abre la galería sin
// disparar el click de la zona (que abriría el selector de archivos).
function wireLibraryButton(zone, onPick) {
  const btn = zone.querySelector(".drop-lib-btn");
  if (!btn) return;
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    openClipLibrary(onPick);
  });
}
// URL para el <video> del preview: blob para uploads, la del server si viene
// de la librería. Devuelve también el nombre a mostrar.
function previewSrcFor(file) {
  return file.fromLibrary ? file.url : URL.createObjectURL(file);
}
// Adjunta el clip al FormData con el campo que corresponda.
function appendClip(form, field, file) {
  if (file.fromLibrary) form.append("library_clip", file.name);
  else form.append(field, file);
}

// --- Estilo de subtítulos (compartido: Subtítulos, Reel Express, Reel Frase) ---
// Los tres módulos incluyen el mismo bloque (templates/modules/_sub_style.html)
// con distinto prefijo de ids. SubStyle lo llena con las fuentes y la paleta
// que manda /api/subtitles/styles (lo mismo que usa build_ass), recuerda la
// elección entre módulos y sesiones, arma los params que espera
// SubtitlesParams y dibuja la muestra animada del preview.
const SubStyle = (() => {
  const KEY = "reelforge-sub-style";
  const prefixes = [];
  const faces = {};   // archivo de fuente -> familia CSS ya cargada en el canvas
  const descents = {};  // archivo de fuente -> winDescent/UPM (ver drawSample)
  let opts = null;

  const load = () => {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (_) { return {}; }
  };

  // Paleta/picker y la etiqueta del grosor dependen de otros controles.
  function sync(p) {
    const rotate = el(p + "color-mode").value === "rotate";
    el(p + "single-color-row").classList.toggle("hidden", rotate);
    el(p + "palette-row").classList.toggle("hidden", !rotate);
    el(p + "outline-size-val").textContent = el(p + "outline-size").value;
    el(p + "color-preview").style.background = el(p + "highlight-color").value;
  }

  function apply(p, s) {
    const font = el(p + "font");
    if (s.font && [...font.options].some((o) => o.value === s.font)) font.value = s.font;
    if (typeof s.uppercase === "boolean") el(p + "uppercase").checked = s.uppercase;
    if (s.color_mode) el(p + "color-mode").value = s.color_mode;
    if (s.outline_color) el(p + "outline-color").value = s.outline_color;
    if (s.outline_size != null) el(p + "outline-size").value = s.outline_size;
    sync(p);
  }

  function snapshot(p) {
    return {
      font: el(p + "font").value,
      uppercase: el(p + "uppercase").checked,
      color_mode: el(p + "color-mode").value,
      outline_color: el(p + "outline-color").value,
      outline_size: el(p + "outline-size").value,
    };
  }

  // El estilo es uno solo: lo que elegís en un módulo queda en los otros.
  function save(p) {
    const s = snapshot(p);
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (_) {}
    prefixes.filter((q) => q !== p).forEach((q) => apply(q, s));
  }

  function fill(p) {
    // Con nodos y no con innerHTML: la etiqueta sale del nombre interno de la
    // fuente, que podría traer "<" o "&".
    el(p + "font").replaceChildren(...opts.fonts.map((f) => new Option(f.label, f.file)));
    el(p + "palette").replaceChildren(...opts.palette.map((c) => {
      const chip = document.createElement("span");
      chip.className = "sub-color-chip";
      chip.style.background = c;
      chip.title = c;
      return chip;
    }));
    const out = el(p + "outline-size");
    out.min = opts.outline.min;
    out.max = opts.outline.max;
    apply(p, { font: opts.default_font, outline_size: opts.outline.default, ...load() });
  }

  const ready = fetch("/api/subtitles/styles")
    .then((r) => r.json())
    .then((o) => {
      opts = o;
      // Una familia CSS por archivo y sin "bold" al dibujar: cada archivo ya
      // trae su peso (evita la negrita sintética que engordaba el preview).
      o.fonts.forEach((f, i) => {
        descents[f.file] = f.descent;
        const face = new FontFace(`SubFont${i}`, `url("${f.url}")`);
        face.load().then((ff) => { document.fonts.add(ff); faces[f.file] = `SubFont${i}`; })
          .catch(() => {});
      });
      prefixes.forEach(fill);
    })
    .catch(() => {});

  function init(p) {
    prefixes.push(p);
    ["font", "uppercase", "color-mode", "outline-color"].forEach((id) =>
      el(p + id).addEventListener("change", () => { sync(p); save(p); }));
    el(p + "outline-size").addEventListener("input", () => { sync(p); save(p); });
    el(p + "highlight-color").addEventListener("input", () => sync(p));
    sync(p);
    if (opts) fill(p);
  }

  // Lo que espera SubtitlesParams.from_form (en un FormData o en un JSON).
  function params(p) {
    return {
      font_size: el(p + "font-size").value,
      position_y: el(p + "position-y").value,
      words_per_line: el(p + "words").value,
      highlight_color: el(p + "highlight-color").value,
      sub_font: el(p + "font").value,
      sub_uppercase: el(p + "uppercase").checked ? "on" : "",
      sub_color_mode: el(p + "color-mode").value,
      sub_outline_color: el(p + "outline-color").value,
      sub_outline_size: el(p + "outline-size").value,
    };
  }

  function appendTo(form, p) {
    Object.entries(params(p)).forEach(([k, v]) => form.append(k, v));
  }

  // Muestra animada, espejo de build_ass: la palabra activa avanza cada 0.6 s
  // y, con "colores que rotan", cada palabra nueva toma el siguiente color.
  // Tamaño = em en px (el server convierte el Fontsize del ASS por fuente).
  // Vertical: libass apoya el borde inferior en baseline + winDescent; el
  // "bottom" del canvas usa otra métrica, así que se dibuja sobre la baseline.
  const SAMPLE = ["Ejemplo", "de", "subtítulo", "en", "vivo", "aquí"];
  function drawSample(ctx, cw, ch, p) {
    const s = cw / 1080;
    const fs = +el(p + "font-size").value * s;
    const y = (+el(p + "position-y").value / 1920) * ch;
    const upper = el(p + "uppercase").checked;
    const white = el(p + "outline-color").value === "white";
    const outline = +el(p + "outline-size").value * s;
    const file = el(p + "font").value;
    const fam = faces[file];
    const group = SAMPLE.slice(0, +el(p + "words").value)
      .map((w) => (upper ? w.toUpperCase() : w));
    const tick = Math.floor(performance.now() / 600);
    const active = tick % group.length;
    const palette = (opts && opts.palette) || ["#FFE600"];
    const accent = el(p + "color-mode").value === "rotate"
      ? palette[tick % palette.length]
      : el(p + "highlight-color").value;

    ctx.save();
    const setFont = (size) => {
      ctx.font = fam ? `${size}px "${fam}"` : `bold ${size}px sans-serif`;
    };
    setFont(fs);
    // Mismo ajuste que _fit_group (processor.py): si el grupo no entra en el
    // ancho útil se achica (letras y espacios, NO el contorno, como libass), y
    // entre palabras va el doble del contorno para que no se peguen.
    const gaps = group.length - 1;
    const sumW = (ws) => ws.reduce((a, b) => a + b, 0);
    let widths = group.map((w) => ctx.measureText(w).width);
    let space = ctx.measureText(" ").width;
    const fixed = 2 * outline * (gaps + 1);
    const body = sumW(widths) + gaps * space;
    const fit = body + fixed > 1000 * s && body > 0
      ? Math.max(0.3, (1000 * s - fixed) / body) : 1;
    if (fit < 1) {
      setFont(fs * fit);
      widths = group.map((w) => ctx.measureText(w).width);
      space = ctx.measureText(" ").width;
    }
    const extra = 2 * outline;
    ctx.textAlign = "center";
    const baseline = y - (descents[file] != null ? descents[file] * fs * fit : 0);
    ctx.textBaseline = fam && descents[file] != null ? "alphabetic" : "bottom";
    ctx.lineJoin = "round";
    ctx.lineWidth = outline * 2;   // el trazo es centrado: la mitad queda afuera
    ctx.strokeStyle = white ? "#fff" : "#000";
    let x = (cw - sumW(widths) - gaps * (space + extra)) / 2;
    group.forEach((word, i) => {
      const cx = x + widths[i] / 2;
      const ty = ctx.textBaseline === "alphabetic" ? baseline : y;
      if (outline > 0) ctx.strokeText(word, cx, ty);
      ctx.fillStyle = i === active ? accent : (white ? "#000" : "#fff");
      ctx.fillText(word, cx, ty);
      x += widths[i] + space + extra;
    });
    ctx.restore();
  }

  return { ready, init, params, appendTo, drawSample };
})();
