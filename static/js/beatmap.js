(function () {
// ReelForge — beatmap: rhythm analysis of a song.
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

// Section bar (shared with anime_edit): proportional widths and the drop.
const SECTION_LABELS = { intro: "intro", verse: "verso", chorus: "coro", bridge: "puente", outro: "outro" };
window.renderSections = function (cont, summary) {
  cont.innerHTML = "";
  const dur = summary.duration || 1;
  for (const s of summary.sections || []) {
    const d = document.createElement("div");
    const label = SECTION_LABELS[s.type] || s.type;
    d.className = `ed-sec-${s.type}`;
    d.style.width = `${((s.end - s.start) / dur) * 100}%`;
    d.title = `${label} · ${s.start.toFixed(1)}–${s.end.toFixed(1)} s`;
    d.textContent = label;
    cont.appendChild(d);
  }
  if (summary.drop != null) {
    const m = document.createElement("span");
    m.className = "ed-drop";
    m.style.left = `${(summary.drop / dur) * 100}%`;
    m.title = `drop · ${summary.drop.toFixed(2)} s`;
    cont.appendChild(m);
  }
};

el("bm-process-btn").addEventListener("click", () => {
  const url = el("bm-url").value.trim();
  if (!bmFile && !url) return showError("Elegí una canción o pegá un link.");
  const form = new FormData();
  if (url) form.append("music_url", url);
  else form.append("audio", bmFile);
  form.append("engine", el("bm-engine").value);
  form.append("language", el("bm-language").value);
  form.append("lyrics", el("bm-lyrics").checked ? "1" : "0");
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
  const r = st.summary;
  el("bm-stats").innerHTML =
    `<span>BPM <b>${r.bpm}</b></span><span>Compases <b>${r.bars}</b></span>` +
    `<span>Drop <b>${r.drop != null ? r.drop.toFixed(2) + " s" : "—"}</b></span>` +
    `<span>Motor <b>${r.engine}</b></span><span>Palabras <b>${r.words}</b></span>`;
  renderSections(el("bm-sections"), r);
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
