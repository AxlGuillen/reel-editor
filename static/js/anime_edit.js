(function () {
// ReelForge — anime_edit: dos fases (analizar → elegir planos y variantes).
const AE_API = "/api/anime-edit";
let aeVideo = null;          // File o {name, fromLibrary}
let aeMusica = null;
let aeJob = null;
let aeSel = new Set();
let aeHeroe = null;

el("ae-video-btn").addEventListener("click", () =>
  openFilePicker("clip", el("ae-video"), (f) => setVideo(f)));
el("ae-video").addEventListener("change", (e) => e.target.files[0] && setVideo(e.target.files[0]));
el("ae-lib-btn").addEventListener("click", () => openClipLibrary((clip) => setVideo(clip)));
el("ae-musica-btn").addEventListener("click", () =>
  openFilePicker("audio", el("ae-musica"), (f) => setMusica(f)));
el("ae-musica").addEventListener("change", (e) => e.target.files[0] && setMusica(e.target.files[0]));
["dur", "int"].forEach((k) => el(`ae-${k}`).addEventListener("input", () => {
  el(`ae-${k}-val`).textContent = k === "int" ? (+el("ae-int").value).toFixed(1) : el("ae-dur").value;
}));

function setVideo(v) { aeVideo = v; el("ae-video-name").textContent = v.name; el("ae-video-url").value = ""; }
function setMusica(m) { aeMusica = m; el("ae-musica-name").textContent = m.name; el("ae-musica-url").value = ""; }

// Opciones: variantes y looks.
fetch(`${AE_API}/presets`).then((r) => r.json()).then((d) => {
  el("ae-variantes").innerHTML = d.presets.map((p, i) =>
    `<label class="control-check" title="${p.descripcion}"><input type="checkbox" value="${p.id}" ${i === 0 ? "checked" : ""}> ${p.id}</label>`).join("");
});
fetch("/api/edicion/opciones").then((r) => r.json()).then((d) => {
  for (const l of d.looks) el("ae-look").insertAdjacentHTML("beforeend", `<option value="${l.id}">${l.id} — ${l.descripcion}</option>`);
  if (!d.rife) el("ae-interpolar").parentElement.title = "RIFE no está instalado: la cámara lenta repite cuadros";
  if (!d.esrgan) el("ae-escalar").parentElement.title = "Real-ESRGAN no está instalado";
});

function progreso(pct, stage) {
  el("ae-progress-wrap").classList.remove("hidden");
  el("ae-progress-fill").style.width = `${pct}%`;
  el("ae-progress-label").textContent = `${stage || "Procesando…"} ${pct}%`;
}

function error(msg) {
  el("ae-progress-wrap").classList.add("hidden");
  el("ae-process-btn").disabled = false;
  el("ae-finish-btn").disabled = false;
  el("ae-error-msg").textContent = msg;
  el("ae-error-wrap").classList.remove("hidden");
}

// Poller propio: la fase 1 termina con status "done" pero sin `finished`.
function esperar(jobId, fase, onDone) {
  const t = setInterval(async () => {
    try {
      const st = await (await fetch(`${AE_API}/status/${jobId}`)).json();
      if (st.status === "error") { clearInterval(t); return error(st.error); }
      progreso(st.progress, st.stage);
      const listo = st.status === "done" && (fase === 1 ? st.stage === "esperando_seleccion" : st.finished);
      if (listo) { clearInterval(t); el("ae-progress-wrap").classList.add("hidden"); onDone(st); }
    } catch (e) { clearInterval(t); error(e.message); }
  }, 1000);
}

el("ae-process-btn").addEventListener("click", async () => {
  const form = new FormData();
  const vurl = el("ae-video-url").value.trim();
  if (vurl) form.append("video_url", vurl);
  else if (aeVideo && aeVideo.fromLibrary) form.append("library_clip", aeVideo.name);
  else if (aeVideo) form.append("video", aeVideo);
  else return error("Elegí el video o pegá su link.");
  const murl = el("ae-musica-url").value.trim();
  if (el("ae-musica-video").checked) form.append("musica_del_video", "1");
  else if (murl) form.append("musica_url", murl);
  else if (aeMusica) form.append("musica", aeMusica);
  else return error("Elegí la canción, pegá su link o usá el audio del video.");
  form.append("letra", el("ae-letra").checked ? "1" : "0");
  form.append("idioma", el("ae-idioma").value);
  ["ae-error-wrap", "ae-fase2", "ae-result"].forEach((id) => el(id).classList.add("hidden"));
  el("ae-process-btn").disabled = true;
  progreso(0, "Subiendo…");
  const res = await fetch(`${AE_API}/process`, { method: "POST", body: form });
  const data = await _readJson(res);
  if (!res.ok) return error(data.error);
  aeJob = data.job_id;
  esperar(aeJob, 1, mostrarPlanos);
});

function mostrarPlanos(st) {
  el("ae-process-btn").disabled = false;
  aeSel = new Set(st.propuesta);
  aeHeroe = st.heroe;
  const bm = st.beatmap;
  el("ae-stats").innerHTML = `<span>Planos <b>${st.planos.length}</b></span><span>BPM <b>${bm.bpm}</b></span>` +
    `<span>Drop <b>${bm.drop != null ? bm.drop.toFixed(1) + " s" : "—"}</b></span><span>Palabras <b>${bm.palabras}</b></span>`;
  renderSecciones(el("ae-secciones"), bm);
  const grid = el("ae-grid");
  grid.innerHTML = "";
  for (const p of st.planos) {
    const d = document.createElement("div");
    d.className = "ed-plano";
    d.dataset.id = p.id;
    d.title = `${p.desde.toFixed(2)}–${p.hasta.toFixed(2)} s · movimiento ${p.movimiento}`;
    d.innerHTML = `<img loading="lazy" src="${AE_API}/thumb/${aeJob}/${p.id}"><span class="ed-num">${p.id}</span>` +
      `<span class="ed-score">${p.puntaje}</span><button type="button" class="ed-star" title="Héroe">★</button>`;
    grid.appendChild(d);
  }
  pintar();
  el("ae-fase2").classList.remove("hidden");
  el("ae-fase2").scrollIntoView({ behavior: "smooth" });
}

function pintar() {
  for (const d of el("ae-grid").children) {
    const id = +d.dataset.id;
    d.classList.toggle("sel", aeSel.has(id));
    d.classList.toggle("heroe", id === aeHeroe);
  }
}

el("ae-grid").addEventListener("click", (e) => {
  const d = e.target.closest(".ed-plano");
  if (!d) return;
  const id = +d.dataset.id;
  if (e.target.classList.contains("ed-star")) { aeHeroe = id; aeSel.add(id); }
  else if (aeSel.has(id)) aeSel.delete(id);
  else aeSel.add(id);
  pintar();
});

el("ae-finish-btn").addEventListener("click", async () => {
  const variantes = [...el("ae-variantes").querySelectorAll("input:checked")].map((i) => i.value);
  if (!variantes.length) return error("Elegí al menos una variante.");
  if (!aeSel.size) return error("Elegí al menos un plano.");
  const body = {
    job_id: aeJob, seleccion: [...aeSel].sort((a, b) => a - b), heroe: aeHeroe, variantes,
    formato: el("ae-formato").value, duracion_s: +el("ae-dur").value, intensidad: +el("ae-int").value,
    look: el("ae-look").value, texto: el("ae-texto").value, fps: +el("ae-fps").value,
    titulo: el("ae-titulo").value, musica_desde: el("ae-desde").value.trim() || "auto",
    interpolar: el("ae-interpolar").checked, escalar: el("ae-escalar").checked,
  };
  ["ae-error-wrap", "ae-result"].forEach((id) => el(id).classList.add("hidden"));
  el("ae-finish-btn").disabled = true;
  progreso(0, "Preparando el edit…");
  const res = await fetch(`${AE_API}/finish`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  const data = await _readJson(res);
  if (!res.ok) return error(data.error);
  esperar(aeJob, 2, mostrarResultado);
});

function mostrarResultado(st) {
  el("ae-finish-btn").disabled = false;
  const r = st.resultado;
  el("ae-carpeta").textContent = r.carpeta;
  el("ae-videos").innerHTML = r.variantes.map((v, i) => `
    <div class="ed-variante">
      <video class="ed-video" controls preload="metadata" src="${AE_API}/download/${aeJob}/${i + 1}"></video>
      <p><b>${v.variante}</b> · ${v.duracion.toFixed(1)} s · ${v.cortes} cortes${v.rife ? " · RIFE" : ""}</p>
      <a class="btn-secondary" href="${AE_API}/download/${aeJob}/${i + 1}" download>Descargar</a>
    </div>`).join("");
  el("ae-result").classList.remove("hidden");
  el("ae-result").scrollIntoView({ behavior: "smooth" });
}
})();
