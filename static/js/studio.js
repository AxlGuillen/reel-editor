(function () {
// ReelForge — Studio: the slides of a Presentations deck over a base video,
// placed by their words and seen live. Times are never computed here: every
// change goes to /api/studio/place, which runs montage's own resolver, so the
// preview and the render use the same numbers. The preview shows each active
// slide in an iframe of the deck in "layer mode" (?capa=<id>&alfa=1), cropped
// and placed with the geometry the server computes from montage's graph, and
// synced with postMessage {tipo: 'seek', t} on every animation frame.
const API = "/api/studio";
const MAX_VIEWS = 5;                  // loaded iframes kept (active + next + recent)
const PRELOAD_S = 4;                  // load the next slide this many seconds ahead
const LEAD_S = 0.1;                   // montage's lead: a slide comes in 0.1 s before its word
const BOXES = {                       // the "Caja" presets (montage's keys)
  cumple: { width: 706, x: "center", bottom: 14 },
  band: "banda",
};

const st = {
  pres: "", presOrigin: "",
  session: null,                      // /open's answer
  decks: [], deck: null,              // {desk, deck, titulo, url}
  manifest: [], script: null, prompt: null,
  words: [],                          // [{i, w, start, end, anchor}]
  montaje: null, layers: [], specs: [], unplaced: [],
  selected: null,
  views: new Map(),                   // layer key (id, or id@start for the rest slide) → its iframe(s)
  pps: 110,
  mode: "preview",                    // preview | render
  pendingMeasure: null,               // {id, t0} after a move, until it is on screen
  renderJob: null,
};

const video = el("st-video");
const renderVideo = el("st-render");

// ----------------------------------------------------------------- setup

fetch(`${API}/config`).then((r) => r.json()).then((cfg) => {
  st.pres = cfg.presentations_url;
  st.presOrigin = new URL(st.pres).origin;
  loadDecks();
});

function loadDecks() {
  const sel = el("st-deck");
  fetch(`${st.pres}/api/decks`)
    .then((r) => { if (!r.ok) throw new Error(); return r.json(); })
    .then((decks) => {
      st.decks = decks;
      sel.innerHTML = '<option value="">Elegí un deck…</option>' + decks.map((d) =>
        `<option value="${d.desk}/${d.deck}">${esc(d.titulo)} · ${d.desk}/${d.deck} · ${d.laminas} láminas</option>`).join("");
      sel.disabled = false;
      el("st-deck-info").textContent = "";
      if (st.montaje) selectDeck(st.montaje.deck.desk, st.montaje.deck.deck);
    })
    .catch(() => {
      sel.innerHTML = '<option value="">Presentations no contesta</option>';
      el("st-deck-info").innerHTML = `No contesta <code>${esc(st.pres)}</code>. En el repo de Presentations: ` +
        `<code>node tools/servidor-local.mjs</code>. <a href="#" id="st-deck-retry">Reintentar</a>`;
      el("st-deck-retry").addEventListener("click", (e) => { e.preventDefault(); loadDecks(); });
    });
}

el("st-video-btn").addEventListener("click", () =>
  openFilePicker("clip", el("st-video-file"), (f) => openBase({ file: f })));
el("st-video-file").addEventListener("change", (e) => { if (e.target.files[0]) openBase({ file: e.target.files[0] }); });
el("st-lib-btn").addEventListener("click", () => openClipLibrary((clip) => openBase({ library: clip.name })));
el("st-job-btn").addEventListener("click", () => {
  const id = el("st-job").value.trim();
  if (id) openBase({ job: id });
});

function openBase({ file, library, job }) {
  hideError();
  const form = new FormData();
  if (file) form.append("video", file);
  if (library) form.append("library_clip", library);
  if (job) form.append("job_id", job);
  progress(0, file ? "Subiendo el video…" : "Abriendo el video…");
  // XHR and not fetch: a 600 MB upload needs its progress.
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `${API}/open`);
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) progress(Math.round(e.loaded / e.total * 100), "Subiendo el video…"); };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* HTML error */ }
    if (xhr.status !== 200) return showError(data.error || `No se pudo abrir el video (HTTP ${xhr.status}).`);
    hideProgress();
    onOpened(data);
  };
  xhr.onerror = () => showError("No se pudo subir el video.");
  xhr.send(form);
}

function onOpened(data) {
  st.session = data;
  st.words = []; st.layers = []; st.specs = []; st.selected = null;
  clearViews();
  video.src = data.video_url;
  renderVideo.removeAttribute("src");
  setMode("preview");
  el("st-ab").classList.add("hidden");
  el("st-result-wrap").classList.add("hidden");
  el("st-video-info").textContent = `${data.name} · ${fmtClock(data.duration)}` +
    (data.montaje ? " · tenía un montaje guardado: lo cargué" : "");
  el("st-transcribe-btn").disabled = false;
  el("st-segments-btn").disabled = false;
  el("st-studio").classList.remove("hidden");
  st.montaje = data.montaje || null;
  if (st.montaje) {
    applyBoxToControls(st.montaje.caja);
    if (st.decks.length) selectDeck(st.montaje.deck.desk, st.montaje.deck.deck);
  }
  transcriptInfo(data.segments);
  if (data.segments) postSegments(data.segments);
  else refresh();
}

function transcriptInfo(segments) {
  const n = (segments || []).reduce((a, s) => a + (s.words || []).length, 0);
  el("st-transcript-info").textContent = segments
    ? `${n} palabras con tiempos.`
    : "Este video no tiene transcripción todavía: «Transcribir» la hace con Whisper (con los nombres del guion como pista).";
}

// ------------------------------------------------------------------ deck

el("st-deck").addEventListener("change", (e) => {
  const [desk, deck] = e.target.value.split("/");
  if (desk && deck) selectDeck(desk, deck, true);
});

async function selectDeck(desk, deck, fresh = false) {
  el("st-deck").value = `${desk}/${deck}`;
  st.deck = st.decks.find((d) => d.desk === desk && d.deck === deck) || { desk, deck, url: `/${desk}/${deck}/` };
  el("st-deck-info").textContent = "Leyendo las láminas (sin renderizar)…";
  try {
    const [man, script] = await Promise.all([
      fetch(`${st.pres}/api/decks/${desk}/${deck}/laminas`).then(async (r) => {
        const d = await r.json(); if (!r.ok) throw new Error(d.error || "No se pudieron leer las láminas."); return d; }),
      fetch(`${API}/script?desk=${encodeURIComponent(desk)}&deck=${encodeURIComponent(deck)}`).then((r) => r.json()),
    ]);
    st.manifest = man;
    st.script = script.script || null;
    st.prompt = script.prompt || null;
    el("st-deck-info").textContent = `${man.length} láminas · ` + (st.script
      ? `guion marcado con ${script.marks.length} marcas: define dónde entra cada una`
      : "sin guion marcado: se usan las palabras de cada lámina (data-palabras)");
  } catch (err) {
    el("st-deck-info").textContent = err.message;
    return;
  }
  clearViews();
  const same = st.montaje && st.montaje.deck.desk === desk && st.montaje.deck.deck === deck;
  if (fresh || !same) st.montaje = newMontaje();
  refresh({ auto: !st.montaje.capas.length });
}

function newMontaje() {
  const box = currentBox();
  return {
    version: 1, base: { archivo: st.session.name },
    deck: { desk: st.deck.desk, deck: st.deck.deck, servidor: st.pres },
    ...(box ? { caja: box } : {}),
    capas: [], subtitulos: false, render: { formato: "webm", fps: 30 },
  };
}

// ----------------------------------------------------------- transcript

el("st-transcribe-btn").addEventListener("click", () => {
  if (!st.session) return;
  const form = new FormData();
  form.append("job_id", st.session.studio_id);
  if (st.prompt) form.append("initial_prompt", st.prompt);
  el("st-transcribe-btn").disabled = true;
  progress(2, "Transcribiendo…");
  ensureSession()
    .then(() => fetch("/api/subtitles/transcribe", { method: "POST", body: form }))
    .then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.error); return d.job_id; })
    .then((id) => pollJob(`/api/subtitles/status/${id}`, (d) => progress(d.progress, d.stage || "Transcribiendo…")))
    .then((d) => { hideProgress(); return postSegments(d.segments); })
    .catch((err) => showError(err.message))
    .finally(() => { el("st-transcribe-btn").disabled = false; });
});

el("st-segments-btn").addEventListener("click", () => el("st-segments-file").click());
el("st-segments-file").addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (!f) return;
  f.text().then((txt) => {
    let data;
    try { data = JSON.parse(txt); } catch { return showError("El archivo no es un JSON."); }
    postSegments(data);
  });
  e.target.value = "";
});

function postSegments(segments) {
  return api("/segments", { studio_id: st.session.studio_id, segments }).then((d) => {
    st.words = d.words;
    transcriptInfo(segments);
    renderWords();
    refresh({ auto: !!st.montaje && !st.montaje.capas.length });
  }).catch((err) => showError(err.message));
}

// ------------------------------------------------------------- placement

el("st-auto-btn").addEventListener("click", () => {
  if (st.montaje && (!st.montaje.capas.length ||
      confirm("¿Colocar todas las láminas otra vez por el guion? Se pierden los cambios a mano."))) refresh({ auto: true });
});

/** Resolves the montaje on the server (montage's resolver) and redraws. */
function refresh({ auto = false, previous = null, moved = null } = {}) {
  updateButtons();
  if (!st.session || !st.montaje || !st.manifest.length) return Promise.resolve();
  // Without a transcript nothing can be placed by its words yet: wait for it.
  if (!st.words.length && !st.montaje.capas.some((c) => c.desde !== undefined)) return Promise.resolve();
  const body = { studio_id: st.session.studio_id, montaje: st.montaje, manifest: st.manifest,
                 auto, script: st.script, words: !st.words.length };
  return api("/place", body).then((d) => {
    st.montaje = d.montaje;
    st.layers = d.layers;
    st.specs = d.specs;
    st.unplaced = d.unplaced;
    if (d.words) { st.words = d.words; renderWords(); }
    if (auto && d.alignment) {
      const a = d.alignment;
      el("st-deck-info").textContent = `${st.manifest.length} láminas · guion alineado: ${a.matched} de ` +
        `${a.script_words} palabras iguales a la transcripción · ${st.layers.length} colocadas`;
    }
    hideError();
    renderBlocks();
    renderUnplaced();
    renderSelected();
    syncViews(true);
    if (moved) {
      // Paused: jump to where the moved slide is fully in, and time how long
      // it takes to be on screen (the "under 1 s" of the brief). Playing: no jump.
      const l = st.layers.find((x) => x.id === moved);
      if (l && video.paused && st.mode === "preview") {
        video.currentTime = Math.min(l.end - 0.05, l.start + l.fade_in + 0.05);
      } else {
        st.pendingMeasure = null;
      }
    }
    save();
  }).catch((err) => {
    if (previous) { st.montaje = previous; }
    st.pendingMeasure = null;
    showError(err.message);
    renderBlocks();
  }).finally(updateButtons);
}

let saveTimer = null;
function save() {
  clearTimeout(saveTimer);
  el("st-saved").textContent = "Guardando…";
  saveTimer = setTimeout(() => {
    api("/save", { studio_id: st.session.studio_id, montaje: st.montaje, manifest: st.manifest })
      .then((d) => { el("st-saved").textContent = "Guardado"; el("st-saved").title = d.path; })
      .catch((err) => { el("st-saved").textContent = "Sin guardar"; showError(err.message); });
  }, 400);
}

// ------------------------------------------------------------------ box

el("st-box").addEventListener("change", () => {
  document.querySelectorAll(".st-box-custom").forEach((n) =>
    n.classList.toggle("hidden", el("st-box").value !== "custom"));
  boxChanged();
});
["width", "bottom"].forEach((k) => el(`st-${k}`).addEventListener("input", () => {
  el(`st-${k}-val`).textContent = el(`st-${k}`).value;
}));
["width", "bottom"].forEach((k) => el(`st-${k}`).addEventListener("change", boxChanged));

function currentBox() {
  const v = el("st-box").value;
  if (v === "deck") return null;
  if (v === "custom") return { width: +el("st-width").value, x: "center", bottom: +el("st-bottom").value };
  return BOXES[v];
}

function applyBoxToControls(caja) {
  let v = "deck";
  if (caja === "banda") v = "band";
  else if (caja && caja.width === 706 && caja.bottom === 14 && (caja.x || "center") === "center") v = "cumple";
  else if (caja) {
    v = "custom";
    el("st-width").value = caja.width || 706; el("st-bottom").value = caja.bottom || 0;
    el("st-width-val").textContent = el("st-width").value; el("st-bottom-val").textContent = el("st-bottom").value;
  }
  el("st-box").value = v;
  document.querySelectorAll(".st-box-custom").forEach((n) => n.classList.toggle("hidden", v !== "custom"));
}

function boxChanged() {
  if (!st.montaje) return;
  const previous = clone(st.montaje);
  const box = currentBox();
  if (box) st.montaje.caja = box; else delete st.montaje.caja;
  refresh({ previous });
}

// -------------------------------------------------------------- timeline

el("st-zoom").addEventListener("input", (e) => {
  const t = video.currentTime;
  st.pps = +e.target.value;
  renderWords(); renderBlocks();
  scrollTo(t, true);
});

function trackWidth() { return Math.ceil((st.session ? st.session.duration : 0) * st.pps) + 40; }

function renderWords() {
  const track = el("st-track");
  track.style.width = `${trackWidth()}px`;
  const ruler = el("st-ruler");
  const step = st.pps >= 80 ? 1 : 5;
  let html = "";
  for (let s = 0; s <= (st.session ? st.session.duration : 0); s += step) {
    const major = s % 5 === 0;
    html += `<span class="st-tick${major ? " major" : ""}" style="left:${s * st.pps}px">${major ? fmtClock(s, false) : ""}</span>`;
  }
  ruler.innerHTML = html;
  const anchors = anchoredWords();
  el("st-words").innerHTML = st.words.map((w) =>
    `<span class="st-word${w.i % 2 ? " low" : ""}${anchors.has(w.i) ? " anchor" : ""}" data-i="${w.i}" ` +
    `style="left:${w.start * st.pps}px" title="${fmtClock(w.start)} · ${esc(w.anchor.palabra)} (${w.anchor.n})">${esc(w.w)}</span>`).join("");
}

/** Transcript word index → slide id, for the anchors of the montaje. */
function anchoredWords() {
  const out = new Map();
  for (const l of st.layers) {
    if (l.rest) continue;
    const w = wordAt(l.start + LEAD_S);
    const capa = capaOf(l.id);
    if (w && capa && capa.ancla) out.set(w.i, l.id);
  }
  return out;
}

function renderBlocks() {
  const lane = el("st-blocks");
  lane.innerHTML = st.layers.map((l, k) => {
    const capa = capaOf(l.id) || {};
    const fadeW = Math.min(l.fade_in * st.pps, (l.end - l.start) * st.pps);
    if (l.rest) {
      return `<div class="st-block rest" data-key="${l.key}" style="left:${l.start * st.pps}px;` +
        `width:${Math.max(6, (l.end - l.start) * st.pps)}px" title="Lámina de reposo: llena el hueco sola">` +
        `<span class="st-block-label">reposo</span></div>`;
    }
    return `<div class="st-block${st.selected === l.id ? " selected" : ""}${k % 2 ? " alt" : ""}" data-id="${l.id}" ` +
      `style="left:${l.start * st.pps}px;width:${Math.max(6, (l.end - l.start) * st.pps)}px">` +
      `<span class="st-fade" style="width:${fadeW}px"></span>` +
      `<span class="st-block-label">${esc(l.id)}</span>` +
      `<span class="st-block-meta">${capa.ancla ? "«" + esc(capa.ancla.palabra) + "»" : fmtClock(l.start)}</span>` +
      `<span class="st-handle${capa.hasta !== undefined ? " pinned" : ""}" title="Arrastrá para fijar el final; doble clic: automático"></span></div>`;
  }).join("");
  renderWordsAnchors();
}

function renderWordsAnchors() {
  const anchors = anchoredWords();
  document.querySelectorAll("#st-words .st-word").forEach((n) => n.classList.toggle("anchor", anchors.has(+n.dataset.i)));
}

function renderUnplaced() {
  const wrap = el("st-unplaced-wrap");
  wrap.classList.toggle("hidden", !st.unplaced.length);
  el("st-unplaced").innerHTML = st.unplaced.map((id) =>
    `<button type="button" class="st-chip" data-id="${id}" title="Anclarla a la palabra donde está el cursor">＋ ${esc(id)}</button>`).join("");
}

el("st-unplaced").addEventListener("click", (e) => {
  const b = e.target.closest(".st-chip");
  if (!b) return;
  const w = wordAt(video.currentTime + LEAD_S);
  if (!w) return showError("Primero hace falta la transcripción.");
  const previous = clone(st.montaje);
  st.montaje.capas.push({ lamina: b.dataset.id, ancla: w.anchor });
  st.selected = b.dataset.id;
  st.pendingMeasure = { id: b.dataset.id, t0: performance.now() };
  refresh({ previous, moved: b.dataset.id });
});

function renderSelected() {
  const box = el("st-selected");
  const capa = st.selected && capaOf(st.selected);
  const l = st.selected && st.layers.find((x) => x.id === st.selected);
  if (!capa || !l) {
    box.innerHTML = '<p class="hint">Tocá una lámina en la línea de tiempo para ver dónde está anclada. ' +
      "Arrastrala sobre otra palabra para moverla; arrastrá su borde derecho para fijar dónde termina.</p>";
    return;
  }
  const until = capa.hasta === undefined ? "automático (reglas de montage)"
    : typeof capa.hasta === "number" ? `${fmtClock(capa.hasta)}` : `«${esc(capa.hasta.palabra)}» (${capa.hasta.n})`;
  const own = capa.caja === "banda" ? "banda" : capa.caja ? "propia" : "la del montaje";
  box.innerHTML = `
    <span class="st-step-title">${esc(capa.lamina)}</span>
    <dl class="st-dl">
      <dt>Entra</dt><dd>${capa.ancla ? `«${esc(capa.ancla.palabra)}» (${capa.ancla.n}ª vez)` : fmtClock(capa.desde)} · <span class="st-mono">${fmtClock(l.start)}</span></dd>
      <dt>Sale</dt><dd>${until} · <span class="st-mono">${fmtClock(l.end)}</span></dd>
      <dt>Caja</dt><dd>${own}</dd>
    </dl>
    <div class="st-sel-actions">
      ${capa.hasta !== undefined ? '<button type="button" class="btn-secondary" data-act="auto-end">Final automático</button>' : ""}
      <button type="button" class="btn-secondary" data-act="band">${capa.caja === "banda" ? "Caja del montaje" : "En banda"}</button>
      <button type="button" class="btn-secondary" data-act="remove">Quitar</button>
    </div>`;
}

el("st-selected").addEventListener("click", (e) => {
  const act = e.target.dataset && e.target.dataset.act;
  if (!act || !st.selected) return;
  const previous = clone(st.montaje);
  const capa = capaOf(st.selected);
  if (act === "auto-end") delete capa.hasta;
  if (act === "band") { if (capa.caja === "banda") delete capa.caja; else capa.caja = "banda"; }
  if (act === "remove") { st.montaje.capas = st.montaje.capas.filter((c) => c !== capa); st.selected = null; }
  refresh({ previous });
});

// Click on the ruler or the words: seek. Click on a block: select it.
el("st-track").addEventListener("pointerdown", (e) => {
  const block = e.target.closest(".st-block");
  if (block && block.classList.contains("rest")) return;
  if (block) return startDrag(e, block);
  const x = e.clientX - el("st-track").getBoundingClientRect().left;
  seek(x / st.pps);
});

function startDrag(e, block) {
  e.preventDefault();
  const id = block.dataset.id;
  const resize = e.target.classList.contains("st-handle");
  const l = st.layers.find((x) => x.id === id);
  const x0 = e.clientX;
  const left0 = l.start * st.pps, width0 = (l.end - l.start) * st.pps;
  let target = null, moved = false;
  st.selected = id;
  document.querySelectorAll(".st-block").forEach((n) => n.classList.toggle("selected", n === block));
  renderSelected();
  block.setPointerCapture(e.pointerId);
  block.classList.add("dragging");
  const onMove = (ev) => {
    const dx = ev.clientX - x0;
    if (Math.abs(dx) > 3) moved = true;
    if (!moved) return;
    if (resize) {
      block.style.width = `${Math.max(6, width0 + dx)}px`;
      target = wordAt((left0 + width0 + dx) / st.pps);
    } else {
      block.style.left = `${left0 + dx}px`;
      target = wordAt((left0 + dx) / st.pps + LEAD_S);
    }
    document.querySelectorAll(".st-word.target").forEach((n) => n.classList.remove("target"));
    if (target) {
      const n = document.querySelector(`.st-word[data-i="${target.i}"]`);
      if (n) n.classList.add("target");
    }
  };
  const onUp = () => {
    block.removeEventListener("pointermove", onMove);
    block.classList.remove("dragging");
    document.querySelectorAll(".st-word.target").forEach((n) => n.classList.remove("target"));
    if (!moved || !target) { renderBlocks(); return; }
    // Dropped on a word: anchored to THAT word (phrase + occurrence), not to the second.
    const previous = clone(st.montaje);
    const capa = capaOf(id);
    if (resize) capa.hasta = target.anchor;
    else { capa.ancla = target.anchor; delete capa.desde; }
    st.pendingMeasure = { id, t0: performance.now() };
    refresh({ previous, moved: id });
  };
  block.addEventListener("pointermove", onMove);
  block.addEventListener("pointerup", onUp, { once: true });
  block.addEventListener("pointercancel", onUp, { once: true });
}

el("st-blocks").addEventListener("dblclick", (e) => {
  if (!e.target.classList.contains("st-handle")) return;
  const capa = capaOf(e.target.closest(".st-block").dataset.id);
  if (capa.hasta === undefined) return;
  const previous = clone(st.montaje);
  delete capa.hasta;
  refresh({ previous });
});

/** The transcript word closest to second t (by its start). */
function wordAt(t) {
  if (!st.words.length) return null;
  let lo = 0, hi = st.words.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (st.words[mid].start < t) lo = mid + 1; else hi = mid;
  }
  const a = st.words[Math.max(0, lo - 1)], b = st.words[lo];
  return Math.abs(a.start - t) <= Math.abs(b.start - t) ? a : b;
}

// --------------------------------------------------------------- preview

el("st-play").addEventListener("click", () => {
  const v = activeVideo();
  if (v.paused) v.play(); else v.pause();
});
[video, renderVideo].forEach((v) => {
  v.addEventListener("play", () => { el("st-play").textContent = "❚❚"; });
  v.addEventListener("pause", () => { el("st-play").textContent = "▶"; });
});
document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || el("st-studio").classList.contains("hidden") ||
      el("view-studio").classList.contains("hidden") || /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
  e.preventDefault();
  el("st-play").click();
});

function activeVideo() { return st.mode === "render" ? renderVideo : video; }

function seek(t) {
  const v = activeVideo();
  v.currentTime = Math.max(0, Math.min(t, (st.session ? st.session.duration : 0) - 0.01));
}

el("st-ab").addEventListener("click", (e) => {
  const b = e.target.closest(".st-ab-btn");
  if (b) setMode(b.dataset.ab);
});

function setMode(mode) {
  const from = activeVideo();
  const playing = !from.paused;
  from.pause();
  st.mode = mode;
  const to = activeVideo();
  if (from !== to && from.currentSrc) to.currentTime = from.currentTime;
  video.classList.toggle("hidden", mode === "render");
  el("st-layers").classList.toggle("hidden", mode === "render");
  renderVideo.classList.toggle("hidden", mode !== "render");
  video.muted = mode === "render";
  renderVideo.muted = mode !== "render";
  document.querySelectorAll(".st-ab-btn").forEach((n) => n.classList.toggle("active", n.dataset.ab === mode));
  if (playing) to.play();
}

/** Iframes for the slides that are active now or come in soon; the rest go. */
function syncViews(geometryChanged = false) {
  const t = video.currentTime;
  const wanted = new Set();
  st.layers.forEach((l, k) => {
    if (l.start <= t + PRELOAD_S && l.end > t - 0.5) wanted.add(l.key);
    if (l.start > t && (!st.layers[k - 1] || st.layers[k - 1].start <= t)) wanted.add(l.key);
  });
  const keys = new Set(st.layers.map((l) => l.key));
  for (const [key, v] of st.views) {
    if (!keys.has(key)) dropView(key);
    else if (geometryChanged) placeView(v, st.layers.find((l) => l.key === key));
  }
  for (const key of wanted) if (!st.views.has(key)) makeView(st.layers.find((l) => l.key === key));
  if (st.views.size > MAX_VIEWS) {
    [...st.views.entries()].filter(([id]) => !wanted.has(id))
      .sort((a, b) => a[1].used - b[1].used)
      .slice(0, st.views.size - MAX_VIEWS)
      .forEach(([id]) => dropView(id));
  }
}

function layerUrl(id) {
  const base = st.deck.url || `/${st.deck.desk}/${st.deck.deck}/`;
  return `${st.pres}${base}?capa=${encodeURIComponent(id)}&alfa=1`;
}

function makeFrame(id) {
  const f = document.createElement("iframe");
  f.className = "st-frame";
  f.width = 1080; f.height = 1920;
  f.setAttribute("scrolling", "no");
  f.setAttribute("tabindex", "-1");
  f.title = id;
  f.src = layerUrl(id);
  return f;
}

function makeView(layer) {
  const v = { id: layer.id, key: layer.key, ready: false, sent: null, used: performance.now(), frames: [],
              loaded: new Set() };
  v.wrap = document.createElement("div");
  v.wrap.className = "st-layer";
  v.frame = makeFrame(layer.id);
  v.wrap.appendChild(v.frame);
  v.frames.push(v.frame);
  st.views.set(layer.key, v);
  placeView(v, layer);
  el("st-layers").appendChild(v.wrap);
}

/** Crops the deck's 1080x1920 frame to the slide's panel (its `recorte` in the
 *  manifest, or 1080x1370 from y=275: what laminas.mjs cuts) and puts it where
 *  montage's FFmpeg graph puts it. */
function placeView(v, layer) {
  const g = layer.geometry;
  Object.assign(v.wrap.style, { left: `${g.x}px`, top: `${g.y}px`, width: `${g.w}px`, height: `${g.h}px` });
  v.frame.style.transform = `translate(${-g.crop_x * g.scale}px, ${-g.crop_y * g.scale}px) scale(${g.scale})`;
  if (g.band && !v.bgWrap) {
    // Montage's band: the slide over a full-width copy of itself, scaled to
    // cover, blurred (gblur sigma 22) and darkened (eq brightness -0.3). CSS
    // approximates it: the render does it exact.
    v.bgWrap = document.createElement("div");
    v.bgWrap.className = "st-layer st-band";
    v.bgFrame = makeFrame(layer.id);
    v.bgWrap.appendChild(v.bgFrame);
    v.frames.push(v.bgFrame);
    v.ready = false;
    el("st-layers").appendChild(v.bgWrap);
  } else if (!g.band && v.bgWrap) {
    v.bgWrap.remove();
    v.frames = [v.frame];
    v.bgWrap = v.bgFrame = null;
    v.ready = v.loaded.has(v.frame);
  }
  if (v.bgWrap) {
    const s = Math.max(1080 / g.crop_w, g.h / g.crop_h);
    const dx = -g.crop_x * s - (g.crop_w * s - 1080) / 2, dy = -g.crop_y * s - (g.crop_h * s - g.h) / 2;
    Object.assign(v.bgWrap.style, { left: "0px", top: `${g.band_y}px`, width: "1080px", height: `${g.h}px` });
    v.bgFrame.style.transform = `translate(${dx}px, ${dy}px) scale(${s})`;
  }
  v.sent = null;
  el("st-band-note").classList.toggle("hidden", !st.layers.some((l) => l.geometry.band));
}

function dropView(key) {
  const v = st.views.get(key);
  if (!v) return;
  v.wrap.remove();
  if (v.bgWrap) v.bgWrap.remove();
  st.views.delete(key);
}

function clearViews() { for (const id of [...st.views.keys()]) dropView(id); }

window.addEventListener("message", (e) => {
  if (e.origin !== st.presOrigin || !e.data || typeof e.data !== "object") return;
  for (const v of st.views.values()) {
    if (v.frames.some((f) => f.contentWindow === e.source)) {
      if (e.data.tipo === "listo") {
        v.loaded.add(v.frames.find((f) => f.contentWindow === e.source));
        v.ready = v.frames.every((f) => v.loaded.has(f));
        v.sent = null;
      }
      if (e.data.tipo === "error") showError(`La lámina «${v.id}» no está en el deck.`);
      return;
    }
  }
});

/** One frame of the preview: fades and z-order like montage's overlays, and
 *  each active slide seeked to (video time − its start). */
function tick() {
  requestAnimationFrame(tick);
  if (!st.session || el("view-studio").classList.contains("hidden")) return;
  const v = activeVideo();
  const t = v.currentTime;
  el("st-time").textContent = fmtClock(t);
  el("st-playhead").style.transform = `translateX(${t * st.pps}px)`;
  if (!v.paused) scrollTo(t);
  if (st.mode !== "preview") return;
  syncViews();
  st.layers.forEach((l, k) => {
    const view = st.views.get(l.key);
    if (!view) return;
    const on = t >= l.start && t <= l.end;
    let alpha = 0;
    if (on) {
      alpha = 1;
      if (l.fade_in) alpha = Math.min(alpha, (t - l.start) / l.fade_in);
      if (l.fade_out) alpha = Math.min(alpha, (l.end - t) / l.fade_out);
      alpha = Math.max(0, Math.min(1, alpha));
    }
    const vis = on && view.ready ? "visible" : "hidden";
    for (const w of [view.wrap, view.bgWrap]) {
      if (!w) continue;
      w.style.visibility = vis;
      w.style.opacity = alpha;
      w.style.zIndex = 2 * k + (w === view.wrap ? 2 : 1);
    }
    if (!view.ready) return;
    // The slide's own time: frozen on its last frame after its animation
    // (the layer-mode clamps it, like tpad=clone in the render).
    const local = Math.max(0, t - l.start);
    if (on || view.sent === null) {
      if (view.sent === null || Math.abs(view.sent - local) > 1e-4) {
        view.frames.forEach((f) => f.contentWindow && f.contentWindow.postMessage({ tipo: "seek", t: on ? local : 0 }, st.presOrigin));
        view.sent = on ? local : 0;
      }
      if (on) view.used = performance.now();
    }
  });
  const m = st.pendingMeasure;
  if (m) {
    const l = st.layers.find((x) => x.id === m.id);
    const view = l && st.views.get(l.key);
    const visible = l && t >= l.start && t <= l.end;
    if (!l || (visible && view && view.ready)) {
      const ms = Math.round(performance.now() - m.t0);
      el("st-latency").textContent = `Movida en ${ms} ms`;
      el("st-latency").classList.remove("hidden");
      el("st-latency").classList.toggle("slow", ms >= 1000);
      st.pendingMeasure = null;
    }
  }
}
requestAnimationFrame(tick);

function scrollTo(t, center = false) {
  const sc = el("st-scroll");
  const x = t * st.pps;
  if (center || x < sc.scrollLeft + 40 || x > sc.scrollLeft + sc.clientWidth - 80) {
    sc.scrollLeft = Math.max(0, x - sc.clientWidth * (center ? 0.5 : 0.25));
  }
}

// Fit the 1080x1920 canvas into the stage.
const stage = el("st-stage");
new ResizeObserver(() => {
  el("st-canvas").style.transform = `scale(${stage.clientWidth / 1080})`;
}).observe(stage);

// ---------------------------------------------------------------- render

el("st-render-btn").addEventListener("click", async () => {
  if (!st.layers.length) return;
  hideError();
  el("st-result-wrap").classList.add("hidden");
  el("st-render-btn").disabled = true;
  const fmt = (st.montaje.render && st.montaje.render.formato) || "webm";
  const fps = (st.montaje.render && st.montaje.render.fps) || 30;
  try {
    await ensureSession();
    // 1. The slides, from Presentations (only the ones that changed are rendered).
    progress(1, "Pidiendo las láminas a Presentations…");
    const r = await fetch(`${st.pres}/api/render`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ desk: st.deck.desk, deck: st.deck.deck, alfa: fmt, fps,
                           laminas: [...new Set(st.layers.map((l) => l.id))] }),
    });
    const job = await r.json();
    if (!r.ok) throw new Error(job.error || `Presentations respondió ${r.status}.`);
    const done = await pollJob(`${st.pres}/api/render/${job.job_id}`, (d) => {
      const ok = (d.laminas || []).filter((x) => x.estado === "lista" || x.estado === "cacheada").length;
      const cached = (d.laminas || []).filter((x) => x.estado === "cacheada").length;
      progress(Math.round((d.progreso || 0) * 0.5),
        `Láminas: ${ok} de ${(d.laminas || []).length} listas${cached ? ` (${cached} sin cambios)` : ""}…`);
    }, (d) => d.estado === "listo" ? "done" : d.estado === "error" ? "error" : null, (d) => d.error);
    // 2. The montage, with montage's own module (one encode). The session is
    // asked for again: the slides can take minutes and the server may have restarted.
    await ensureSession();
    const form = new FormData();
    form.append("job_id", st.session.studio_id);
    form.append("layers", JSON.stringify(st.specs));
    form.append("layers_folder", done.carpeta);
    if (st.montaje.subtitulos) form.append("subtitles", "1");
    form.append("output_name", `Estudio ${st.session.name.replace(/\.[^.]+$/, "")}`);
    progress(50, "Montando…");
    await new Promise((resolve, reject) => runJob("/api/montage", form, {
      onProgress: (pct, stage) => progress(50 + Math.round(pct / 2), stage || "Montando…"),
      onDone: (id) => { st.renderJob = id; resolve(); },
      onError: (msg) => reject(new Error(msg)),
    }));
    hideProgress();
    const url = `/api/montage/download/${st.renderJob}`;
    renderVideo.src = url;
    el("st-download-btn").href = url;
    el("st-result-wrap").classList.remove("hidden");
    el("st-ab").classList.remove("hidden");
  } catch (err) {
    showError(err.message);
  } finally {
    updateButtons();
  }
});

// --------------------------------------------------------------- helpers

function updateButtons() {
  const ready = !!(st.session && st.montaje && st.manifest.length);
  el("st-auto-btn").disabled = !(ready && st.words.length);
  el("st-render-btn").disabled = !(ready && st.layers.length);
}

/** The session back in the server's memory after a restart (subtitles and
 *  montage take it as a job_id and only look there). */
function ensureSession() {
  return fetch(`${API}/session/${st.session.studio_id}`).then(async (r) => {
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || "La sesión del estudio ya no existe.");
  });
}

function capaOf(id) { return st.montaje && st.montaje.capas.find((c) => c.lamina === id); }

function api(path, body) {
  return fetch(API + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then(async (r) => {
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || `Error del estudio (HTTP ${r.status}).`);
      return d;
    });
}

/** Polls a status URL until it is done; `state` maps the answer to done/error. */
function pollJob(url, onProgress, state = (d) => (d.status === "done" ? "done" : d.status === "error" ? "error" : null),
                 errorOf = (d) => d.error) {
  return new Promise((resolve, reject) => {
    const timer = setInterval(async () => {
      try {
        const r = await fetch(url);
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        onProgress(d);
        const s = state(d);
        if (s === "done") { clearInterval(timer); resolve(d); }
        if (s === "error") { clearInterval(timer); reject(new Error(errorOf(d) || "Falló.")); }
      } catch (err) { clearInterval(timer); reject(err); }
    }, 1000);
  });
}

function progress(pct, label) {
  el("st-progress-wrap").classList.remove("hidden");
  el("st-progress-fill").style.width = `${pct}%`;
  el("st-progress-label").textContent = `${label} ${pct}%`;
}
function hideProgress() { el("st-progress-wrap").classList.add("hidden"); }
function showError(msg) {
  hideProgress();
  el("st-error-msg").textContent = msg;
  el("st-error-wrap").classList.remove("hidden");
}
function hideError() { el("st-error-wrap").classList.add("hidden"); }

function fmtClock(s, cents = true) {
  s = Math.max(0, s || 0);
  const m = Math.floor(s / 60), r = s - m * 60;
  return `${m}:${(cents ? r.toFixed(2) : Math.floor(r).toString()).padStart(cents ? 5 : 2, "0")}`;
}
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function clone(o) { return JSON.parse(JSON.stringify(o)); }
})();
