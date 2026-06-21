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
