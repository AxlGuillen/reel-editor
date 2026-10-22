(function () {
// ReelForge — anime_edit: two phases (analyze → pick shots and variants).
const AE_API = "/api/anime-edit";
let aeVideo = null;          // File or {name, fromLibrary}
let aeMusic = null;
let aeJob = null;
let aeSel = new Set();
let aeHero = null;

el("ae-video-btn").addEventListener("click", () =>
  openFilePicker("clip", el("ae-video"), (f) => setVideo(f)));
el("ae-video").addEventListener("change", (e) => e.target.files[0] && setVideo(e.target.files[0]));
el("ae-lib-btn").addEventListener("click", () => openClipLibrary((clip) => setVideo(clip)));
el("ae-music-btn").addEventListener("click", () =>
  openFilePicker("audio", el("ae-music"), (f) => setMusic(f)));
el("ae-music").addEventListener("change", (e) => e.target.files[0] && setMusic(e.target.files[0]));
["dur", "int"].forEach((k) => el(`ae-${k}`).addEventListener("input", () => {
  el(`ae-${k}-val`).textContent = k === "int" ? (+el("ae-int").value).toFixed(1) : el("ae-dur").value;
}));

function setVideo(v) { aeVideo = v; el("ae-video-name").textContent = v.name; el("ae-video-url").value = ""; }
function setMusic(m) { aeMusic = m; el("ae-music-name").textContent = m.name; el("ae-music-url").value = ""; }

// Options: variants and looks.
fetch(`${AE_API}/presets`).then((r) => r.json()).then((d) => {
  el("ae-variants").innerHTML = d.presets.map((p, i) =>
    `<label class="control-check" title="${p.description}"><input type="checkbox" value="${p.id}" ${i === 0 ? "checked" : ""}> ${p.id}</label>`).join("");
});
fetch("/api/editing/options").then((r) => r.json()).then((d) => {
  for (const l of d.looks) el("ae-look").insertAdjacentHTML("beforeend", `<option value="${l.id}">${l.id} — ${l.description}</option>`);
  if (!d.rife) el("ae-interpolate").parentElement.title = "RIFE no está instalado: la cámara lenta repite cuadros";
  if (!d.esrgan) el("ae-upscale").parentElement.title = "Real-ESRGAN no está instalado";
});

function showProgress(pct, stage) {
  el("ae-progress-wrap").classList.remove("hidden");
  el("ae-progress-fill").style.width = `${pct}%`;
  el("ae-progress-label").textContent = `${stage || "Procesando…"} ${pct}%`;
}

function showError(msg) {
  el("ae-progress-wrap").classList.add("hidden");
  el("ae-process-btn").disabled = false;
  el("ae-finish-btn").disabled = false;
  el("ae-error-msg").textContent = msg;
  el("ae-error-wrap").classList.remove("hidden");
}

// Own poller: phase 1 ends with status "done" but without `finished`.
function waitFor(jobId, phase, onDone) {
  const t = setInterval(async () => {
    try {
      const st = await (await fetch(`${AE_API}/status/${jobId}`)).json();
      if (st.status === "error") { clearInterval(t); return showError(st.error); }
      showProgress(st.progress, st.stage);
      const ready = st.status === "done" && (phase === 1 ? st.stage === "esperando_seleccion" : st.finished);
      if (ready) { clearInterval(t); el("ae-progress-wrap").classList.add("hidden"); onDone(st); }
    } catch (e) { clearInterval(t); showError(e.message); }
  }, 1000);
}

el("ae-process-btn").addEventListener("click", async () => {
  const form = new FormData();
  const vurl = el("ae-video-url").value.trim();
  if (vurl) form.append("video_url", vurl);
  else if (aeVideo && aeVideo.fromLibrary) form.append("library_clip", aeVideo.name);
  else if (aeVideo) form.append("video", aeVideo);
  else return showError("Elegí el video o pegá su link.");
  const murl = el("ae-music-url").value.trim();
  if (el("ae-music-from-video").checked) form.append("music_from_video", "1");
  else if (murl) form.append("music_url", murl);
  else if (aeMusic) form.append("music", aeMusic);
  else return showError("Elegí la canción, pegá su link o usá el audio del video.");
  form.append("lyrics", el("ae-lyrics").checked ? "1" : "0");
  form.append("language", el("ae-language").value);
  ["ae-error-wrap", "ae-phase2", "ae-result"].forEach((id) => el(id).classList.add("hidden"));
  el("ae-process-btn").disabled = true;
  showProgress(0, "Subiendo…");
  const res = await fetch(`${AE_API}/process`, { method: "POST", body: form });
  const data = await _readJson(res);
  if (!res.ok) return showError(data.error);
  aeJob = data.job_id;
  waitFor(aeJob, 1, showShots);
});

function showShots(st) {
  el("ae-process-btn").disabled = false;
  aeSel = new Set(st.proposal);
  aeHero = st.hero;
  const bm = st.beatmap;
  el("ae-stats").innerHTML = `<span>Planos <b>${st.shots.length}</b></span><span>BPM <b>${bm.bpm}</b></span>` +
    `<span>Drop <b>${bm.drop != null ? bm.drop.toFixed(1) + " s" : "—"}</b></span><span>Palabras <b>${bm.words}</b></span>`;
  renderSections(el("ae-sections"), bm);
  const grid = el("ae-grid");
  grid.innerHTML = "";
  for (const p of st.shots) {
    const d = document.createElement("div");
    d.className = "ed-shot";
    d.dataset.id = p.id;
    d.title = `${p.start.toFixed(2)}–${p.end.toFixed(2)} s · movimiento ${p.motion}`;
    d.innerHTML = `<img loading="lazy" src="${AE_API}/thumb/${aeJob}/${p.id}"><span class="ed-num">${p.id}</span>` +
      `<span class="ed-score">${p.score}</span><button type="button" class="ed-star" title="Héroe">★</button>`;
    grid.appendChild(d);
  }
  paintSelection();
  el("ae-phase2").classList.remove("hidden");
  el("ae-phase2").scrollIntoView({ behavior: "smooth" });
}

function paintSelection() {
  for (const d of el("ae-grid").children) {
    const id = +d.dataset.id;
    d.classList.toggle("sel", aeSel.has(id));
    d.classList.toggle("hero", id === aeHero);
  }
}

el("ae-grid").addEventListener("click", (e) => {
  const d = e.target.closest(".ed-shot");
  if (!d) return;
  const id = +d.dataset.id;
  if (e.target.classList.contains("ed-star")) { aeHero = id; aeSel.add(id); }
  else if (aeSel.has(id)) aeSel.delete(id);
  else aeSel.add(id);
  paintSelection();
});

el("ae-finish-btn").addEventListener("click", async () => {
  const variants = [...el("ae-variants").querySelectorAll("input:checked")].map((i) => i.value);
  if (!variants.length) return showError("Elegí al menos una variante.");
  if (!aeSel.size) return showError("Elegí al menos un plano.");
  const body = {
    job_id: aeJob, selection: [...aeSel].sort((a, b) => a - b), hero: aeHero, variants,
    format: el("ae-format").value, duration_s: +el("ae-dur").value, intensity: +el("ae-int").value,
    look: el("ae-look").value, text: el("ae-text").value, fps: +el("ae-fps").value,
    title: el("ae-title").value, music_start: el("ae-music-start").value.trim() || "auto",
    interpolate: el("ae-interpolate").checked, upscale: el("ae-upscale").checked,
  };
  ["ae-error-wrap", "ae-result"].forEach((id) => el(id).classList.add("hidden"));
  el("ae-finish-btn").disabled = true;
  showProgress(0, "Preparando el edit…");
  const res = await fetch(`${AE_API}/finish`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  const data = await _readJson(res);
  if (!res.ok) return showError(data.error);
  waitFor(aeJob, 2, showResult);
});

function showResult(st) {
  el("ae-finish-btn").disabled = false;
  const r = st.result;
  el("ae-folder").textContent = r.folder;
  el("ae-videos").innerHTML = r.variants.map((v, i) => `
    <div class="ed-variant">
      <video class="ed-video" controls preload="metadata" src="${AE_API}/download/${aeJob}/${i + 1}"></video>
      <p><b>${v.variant}</b> · ${v.duration.toFixed(1)} s · ${v.cuts} cortes${v.rife ? " · RIFE" : ""}</p>
      <a class="btn-secondary" href="${AE_API}/download/${aeJob}/${i + 1}" download>Descargar</a>
    </div>`).join("");
  el("ae-result").classList.remove("hidden");
  el("ae-result").scrollIntoView({ behavior: "smooth" });
}
})();
