// ReelForge — helpers compartidos entre módulos (Vanilla JS).

const el = (id) => document.getElementById(id);

// Envía el form al endpoint /process de un módulo y hace polling de su status.
// callbacks: { onProgress(pct, stage?), onDone(jobId), onError(msg) }
// `stage` es opcional (lo manda reel_express); el resto de módulos lo ignora.
function runJob(apiBase, formData, { onProgress, onDone, onError }) {
  fetch(`${apiBase}/process`, { method: "POST", body: formData })
    .then(async (res) => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Error al iniciar el proceso");
      poll(data.job_id);
    })
    .catch((err) => onError(err.message));

  function poll(jobId) {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${apiBase}/status/${jobId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Error consultando status");

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
