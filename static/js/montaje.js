(function () {
// ReelForge — montaje: capas animadas sobre un video vertical.
const MT_API = "/api/montaje";
let mtVideo = null;
let mtCapas = [];

el("mt-video-btn").addEventListener("click", () =>
  openFilePicker("clip", el("mt-video"), (f) => { mtVideo = f; el("mt-video-name").textContent = f.name; }));
el("mt-video").addEventListener("change", (e) => {
  if (e.target.files[0]) { mtVideo = e.target.files[0]; el("mt-video-name").textContent = mtVideo.name; }
});
el("mt-capas-btn").addEventListener("click", () => el("mt-capas").click());
el("mt-capas").addEventListener("change", (e) => {
  mtCapas = [...e.target.files];
  el("mt-capas-name").textContent = mtCapas.map((f) => f.name).join(", ");
});
["ancho", "abajo"].forEach((k) => el(`mt-${k}`).addEventListener("input", () => {
  el(`mt-${k}-val`).textContent = el(`mt-${k}`).value;
}));

el("mt-process-btn").addEventListener("click", () => {
  if (!mtVideo) return error("Elegí el video base.");
  const capas = el("mt-capas-json").value;
  try { JSON.parse(capas); } catch { return error("El JSON de las capas no es válido."); }
  const form = new FormData();
  form.append("video", mtVideo);
  mtCapas.forEach((f) => form.append("capas_archivos", f));
  if (el("mt-carpeta").value.trim()) form.append("carpeta_capas", el("mt-carpeta").value.trim());
  form.append("capas", capas);
  form.append("caja", JSON.stringify({ modo: el("mt-modo").value, ancho: +el("mt-ancho").value,
    abajo: +el("mt-abajo").value }));
  if (el("mt-subs").checked) form.append("subtitulos", "1");
  ["mt-result-wrap", "mt-error-wrap"].forEach((id) => el(id).classList.add("hidden"));
  el("mt-progress-wrap").classList.remove("hidden");
  el("mt-process-btn").disabled = true;
  runJob(MT_API, form, {
    onProgress: (pct, stage) => {
      el("mt-progress-fill").style.width = `${pct}%`;
      el("mt-progress-label").textContent = `${stage || "Montando…"} ${pct}%`;
    },
    onDone: (jobId) => {
      el("mt-progress-wrap").classList.add("hidden");
      el("mt-process-btn").disabled = false;
      el("mt-preview").src = `${MT_API}/download/${jobId}`;
      el("mt-download-btn").href = `${MT_API}/download/${jobId}`;
      el("mt-result-wrap").classList.remove("hidden");
    },
    onError: error,
  });
});

function error(msg) {
  el("mt-progress-wrap").classList.add("hidden");
  el("mt-process-btn").disabled = false;
  el("mt-error-msg").textContent = msg;
  el("mt-error-wrap").classList.remove("hidden");
}
})();
