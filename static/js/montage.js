(function () {
// ReelForge — montage: animated layers over a vertical video.
const MT_API = "/api/montage";
let mtVideo = null;
let mtLayers = [];

el("mt-video-btn").addEventListener("click", () =>
  openFilePicker("clip", el("mt-video"), (f) => { mtVideo = f; el("mt-video-name").textContent = f.name; }));
el("mt-video").addEventListener("change", (e) => {
  if (e.target.files[0]) { mtVideo = e.target.files[0]; el("mt-video-name").textContent = mtVideo.name; }
});
el("mt-layers-btn").addEventListener("click", () => el("mt-layers").click());
el("mt-layers").addEventListener("change", (e) => {
  mtLayers = [...e.target.files];
  el("mt-layers-name").textContent = mtLayers.map((f) => f.name).join(", ");
});
["width", "bottom"].forEach((k) => el(`mt-${k}`).addEventListener("input", () => {
  el(`mt-${k}-val`).textContent = el(`mt-${k}`).value;
}));

el("mt-process-btn").addEventListener("click", () => {
  if (!mtVideo) return showError("Elegí el video base.");
  const layers = el("mt-layers-json").value;
  try { JSON.parse(layers); } catch { return showError("El JSON de las capas no es válido."); }
  const form = new FormData();
  form.append("video", mtVideo);
  mtLayers.forEach((f) => form.append("layer_files", f));
  if (el("mt-folder").value.trim()) form.append("layers_folder", el("mt-folder").value.trim());
  form.append("layers", layers);
  form.append("box", JSON.stringify({ mode: el("mt-mode").value, width: +el("mt-width").value,
    bottom: +el("mt-bottom").value }));
  if (el("mt-subs").checked) form.append("subtitles", "1");
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
    onError: showError,
  });
});

function showError(msg) {
  el("mt-progress-wrap").classList.add("hidden");
  el("mt-process-btn").disabled = false;
  el("mt-error-msg").textContent = msg;
  el("mt-error-wrap").classList.remove("hidden");
}
})();
