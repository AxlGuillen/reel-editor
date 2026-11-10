(function () {
// ReelForge — motion: the motion map of a video span.
const MO_API = "/api/motion";
const NAMES = { impact: "golpe", smear: "barrido", flash: "destello", cut: "corte", pan: "paneo", calm: "calma" };
let moFile = null;

el("mo-video-btn").addEventListener("click", () =>
  openFilePicker("video", el("mo-video"), (f) => setFile(f)));
el("mo-video").addEventListener("change", (e) => e.target.files[0] && setFile(e.target.files[0]));
el("mo-lib-btn").addEventListener("click", () => openClipLibrary((clip) => setFile(clip)));

function setFile(f) {
  moFile = f;
  el("mo-video-name").textContent = f.name;
  el("mo-path").value = "";
}

el("mo-process-btn").addEventListener("click", () => {
  const path = el("mo-path").value.trim();
  if (!moFile && !path) return showError("Elegí un video o escribí la ruta de un archivo.");
  const form = new FormData();
  if (path) form.append("path", path);
  else appendClip(form, "video", moFile);
  form.append("start", el("mo-start").value.trim());
  form.append("end", el("mo-end").value.trim());
  form.append("width", el("mo-width").value);
  form.append("preview", el("mo-preview").checked ? "1" : "0");
  ["mo-result-wrap", "mo-error-wrap"].forEach((id) => el(id).classList.add("hidden"));
  el("mo-progress-wrap").classList.remove("hidden");
  el("mo-process-btn").disabled = true;
  runJob(MO_API, form, {
    onProgress: (pct, stage) => {
      el("mo-progress-fill").style.width = `${pct}%`;
      el("mo-progress-label").textContent = `${stage || "Analizando…"} ${pct}%`;
    },
    onDone: showResult,
    onError: showError,
  });
});

// The span as a bar: one tick per event, ranges for pans and calm.
function renderTrack(cont, m) {
  cont.innerHTML = "";
  const dur = Math.max(m.end - m.start, 1e-3);
  for (const e of m.events) {
    const d = document.createElement("span");
    d.className = `mo-ev mo-${e.type}`;
    d.style.left = `${((e.t - m.start) / dur) * 100}%`;
    if (e.dur) d.style.width = `${(e.dur / dur) * 100}%`;
    else if (e.type === "impact" || e.type === "smear") d.style.height = `${30 + 70 * e.score}%`;
    d.title = `${NAMES[e.type]} · ${e.t.toFixed(2)} s · cuadro ${e.f}` +
      (e.dir ? ` · hacia ${e.dir}` : "") + (e.score != null ? ` · ${e.score.toFixed(2)}` : "");
    cont.appendChild(d);
  }
}

async function showResult(jobId) {
  el("mo-progress-wrap").classList.add("hidden");
  el("mo-process-btn").disabled = false;
  const st = await (await fetch(`${MO_API}/status/${jobId}`)).json();
  const m = st.motion;
  const c = m.counts || {};
  el("mo-stats").innerHTML =
    `<span>Cuadros <b>${m.frames}</b></span><span>Tomas <b>${m.shots}</b></span>` +
    `<span>Golpes <b>${c.impact || 0}</b></span><span>Barridos <b>${c.smear || 0}</b></span>` +
    `<span>Destellos <b>${c.flash || 0}</b></span><span>Repetidos <b>${Math.round(m.held_ratio * 100)}%</b></span>` +
    `<span>Velocidad <b>${Math.round(m.fps_analysis)} c/s</b></span>`;
  renderTrack(el("mo-track"), m);
  el("mo-sheet").src = `${MO_API}/sheet/${jobId}`;
  el("mo-sheet-btn").href = `${MO_API}/sheet/${jobId}`;
  el("mo-summary").textContent = (st.summary || []).join("\n");
  el("mo-summary-btn").href = `${MO_API}/summary/${jobId}`;
  el("mo-json-btn").href = `${MO_API}/json/${jobId}`;
  const prev = el("mo-video-out"), dl = el("mo-download-btn");
  prev.classList.toggle("hidden", !st.has_preview);
  dl.classList.toggle("hidden", !st.has_preview);
  if (st.has_preview) {
    prev.src = `${MO_API}/download/${jobId}`;
    dl.href = `${MO_API}/download/${jobId}`;
  }
  el("mo-result-wrap").classList.remove("hidden");
}

function showError(msg) {
  el("mo-progress-wrap").classList.add("hidden");
  el("mo-process-btn").disabled = false;
  el("mo-error-msg").textContent = msg;
  el("mo-error-wrap").classList.remove("hidden");
}
})();
