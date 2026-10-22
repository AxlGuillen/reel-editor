(function () {
// ReelForge — beatmap: análisis del ritmo de una canción.
const BM_API = "/api/beatmap";
let bmFile = null;

el("bm-audio-btn").addEventListener("click", () =>
  openFilePicker("audio", el("bm-audio"), (f) => setFile(f)));
el("bm-audio").addEventListener("change", (e) => e.target.files[0] && setFile(e.target.files[0]));

function setFile(f) {
  bmFile = f;
  el("bm-audio-name").textContent = f.name;
  el("bm-url").value = "";
}

// Barra de secciones (compartida con anime_edit): ancho proporcional y el drop.
window.renderSecciones = function (cont, resumen) {
  cont.innerHTML = "";
  const dur = resumen.duracion || 1;
  for (const s of resumen.secciones || []) {
    const d = document.createElement("div");
    d.className = `ed-sec-${s.tipo}`;
    d.style.width = `${((s.fin - s.inicio) / dur) * 100}%`;
    d.title = `${s.tipo} · ${s.inicio.toFixed(1)}–${s.fin.toFixed(1)} s`;
    d.textContent = s.tipo;
    cont.appendChild(d);
  }
  if (resumen.drop != null) {
    const m = document.createElement("span");
    m.className = "ed-drop";
    m.style.left = `${(resumen.drop / dur) * 100}%`;
    m.title = `drop · ${resumen.drop.toFixed(2)} s`;
    cont.appendChild(m);
  }
};

el("bm-process-btn").addEventListener("click", () => {
  const url = el("bm-url").value.trim();
  if (!bmFile && !url) return showError("Elegí una canción o pegá un link.");
  const form = new FormData();
  if (url) form.append("musica_url", url);
  else form.append("audio", bmFile);
  form.append("motor", el("bm-motor").value);
  form.append("idioma", el("bm-idioma").value);
  form.append("letra", el("bm-letra").checked ? "1" : "0");
  ["bm-result-wrap", "bm-error-wrap"].forEach((id) => el(id).classList.add("hidden"));
  el("bm-progress-wrap").classList.remove("hidden");
  el("bm-process-btn").disabled = true;
  runJob(BM_API, form, {
    onProgress: (pct, stage) => {
      el("bm-progress-fill").style.width = `${pct}%`;
      el("bm-progress-label").textContent = `${stage || "Analizando…"} ${pct}%`;
    },
    onDone: showResult,
    onError: showError,
  });
});

async function showResult(jobId) {
  el("bm-progress-wrap").classList.add("hidden");
  el("bm-process-btn").disabled = false;
  const st = await (await fetch(`${BM_API}/status/${jobId}`)).json();
  const r = st.resumen;
  el("bm-stats").innerHTML =
    `<span>BPM <b>${r.bpm}</b></span><span>Compases <b>${r.compases}</b></span>` +
    `<span>Drop <b>${r.drop != null ? r.drop.toFixed(2) + " s" : "—"}</b></span>` +
    `<span>Motor <b>${r.motor}</b></span><span>Palabras <b>${r.palabras}</b></span>`;
  renderSecciones(el("bm-secciones"), r);
  el("bm-video").src = `${BM_API}/download/${jobId}`;
  el("bm-download-btn").href = `${BM_API}/download/${jobId}`;
  el("bm-json-btn").href = `${BM_API}/json/${jobId}`;
  el("bm-result-wrap").classList.remove("hidden");
}

function showError(msg) {
  el("bm-progress-wrap").classList.add("hidden");
  el("bm-process-btn").disabled = false;
  el("bm-error-msg").textContent = msg;
  el("bm-error-wrap").classList.remove("hidden");
}
})();
