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
  const py = (ch - dh - 2 * border) * (+get(pfx + "hud_pos_y").value / 100);
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
