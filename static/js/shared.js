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
