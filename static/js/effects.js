(function () {
// ReelForge — effects: the catalog and the gallery (every effect on one take).
const API = "/api/editing";
let fxFile = null;
let catalog = [];

el("fx-video-btn").addEventListener("click", () =>
  openFilePicker("video", el("fx-video"), (f) => setFile(f)));
el("fx-video").addEventListener("change", (e) => e.target.files[0] && setFile(e.target.files[0]));
el("fx-lib-btn").addEventListener("click", () => openClipLibrary((clip) => setFile(clip)));

function setFile(f) {
  fxFile = f;
  el("fx-video-name").textContent = f.name;
  el("fx-path").value = "";
}

function cell(text) {
  const td = document.createElement("td");
  td.textContent = text;
  return td;
}

async function loadCatalog() {
  const data = await (await fetch(`${API}/catalog`)).json();
  catalog = data.effects || [];
  const body = el("fx-catalog"), pick = el("fx-pick");
  body.innerHTML = "";
  pick.innerHTML = "";
  for (const e of catalog) {
    const tr = document.createElement("tr");
    tr.append(cell(e.id), cell(e.stage === "output" ? "salida" : "toma"), cell(e.level),
              cell(e.description), cell([e.trigger.music, e.trigger.video].filter(Boolean).join(" · ")));
    body.appendChild(tr);
    if (e.id === "panel" || e.id === "dedupe") continue;     // need an image / RIFE
    const lab = document.createElement("label");
    lab.className = "control-check fx-chip";
    lab.innerHTML = `<input type="checkbox" value="${e.id}" checked> ${e.id}`;
    pick.appendChild(lab);
  }
}
loadCatalog();

el("fx-process-btn").addEventListener("click", () => {
  const path = el("fx-path").value.trim();
  if (!fxFile && !path) return showError("Elegí un video o escribí la ruta de un archivo.");
  const ids = [...el("fx-pick").querySelectorAll("input:checked")].map((i) => i.value);
  if (!ids.length) return showError("Elegí al menos un efecto.");
  const form = new FormData();
  if (path) form.append("path", path);
  else appendClip(form, "video", fxFile);
  form.append("start", el("fx-start").value.trim());
  form.append("effects", JSON.stringify(ids));
  ["fx-result-wrap", "fx-error-wrap"].forEach((id) => el(id).classList.add("hidden"));
  el("fx-progress-wrap").classList.remove("hidden");
  el("fx-process-btn").disabled = true;
  fetch(`${API}/gallery`, { method: "POST", body: form })
    .then(async (r) => {
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
      poll(d.job_id);
    })
    .catch((err) => showError(err.message));
});

function poll(jobId) {
  const timer = setInterval(async () => {
    try {
      const st = await (await fetch(`${API}/gallery/status/${jobId}`)).json();
      el("fx-progress-fill").style.width = `${st.progress}%`;
      el("fx-progress-label").textContent = `${st.stage || "Armando…"} ${st.progress}%`;
      if (st.status === "error") { clearInterval(timer); showError(st.error); }
      if (st.status === "done") { clearInterval(timer); showResult(jobId, st.gallery); }
    } catch (err) { clearInterval(timer); showError(err.message); }
  }, 1500);
}

function showResult(jobId, g) {
  el("fx-progress-wrap").classList.add("hidden");
  el("fx-process-btn").disabled = false;
  const h = g.hints || {};
  el("fx-hints").textContent = h.angle != null
    ? `Del mapa: ${h.from}, ángulo ${Math.round(h.angle)}°, centro (${h.x.toFixed(2)}, ${h.y.toFixed(2)}).`
    : "Sin golpe cerca del segundo 1: los efectos con dirección van con sus valores por omisión.";
  el("fx-sheet").src = `${API}/gallery/sheet/${jobId}?t=${Date.now()}`;
  const grid = el("fx-grid");
  grid.innerHTML = "";
  for (const name of g.clips) {
    const fig = document.createElement("figure");
    fig.className = "fx-tile";
    const v = document.createElement("video");
    v.src = `${API}/gallery/clip/${jobId}/${encodeURIComponent(name)}`;
    v.muted = true; v.loop = true; v.playsInline = true; v.controls = true;
    const cap = document.createElement("figcaption");
    cap.textContent = name;
    fig.append(v, cap);
    grid.appendChild(fig);
  }
  el("fx-result-wrap").classList.remove("hidden");
}

function showError(msg) {
  el("fx-progress-wrap").classList.add("hidden");
  el("fx-process-btn").disabled = false;
  el("fx-error-msg").textContent = msg;
  el("fx-error-wrap").classList.remove("hidden");
}
})();
