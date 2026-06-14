// ReelForge — módulo audio_merge (unir varios audios en orden).
const AM_API = "/api/audio-merge";

const amDropZone = el("am-drop-zone");
const amInput = el("am-input");
const amEditor = el("am-editor");
const amList = el("am-list");
const amGap = el("am-gap");
const amProcessBtn = el("am-process-btn");
const amPlayer = el("am-player");

const amProgressWrap = el("am-progress-wrap");
const amProgressFill = el("am-progress-fill");
const amProgressLabel = el("am-progress-label");
const amResultWrap = el("am-result-wrap");
const amDownloadBtn = el("am-download-btn");
const amErrorWrap = el("am-error-wrap");
const amErrorMsg = el("am-error-msg");

// Lista de archivos en el orden de unión. Cada item: { file, url }.
let amFiles = [];

// --- Carga (click + drag & drop), permite agregar varios e incrementalmente ---
amDropZone.addEventListener("click", () => amInput.click());
amInput.addEventListener("change", (e) => {
  if (e.target.files.length) addFiles(e.target.files);
  amInput.value = ""; // permite volver a elegir el mismo archivo
});
["dragenter", "dragover"].forEach((evt) =>
  amDropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    amDropZone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  amDropZone.addEventListener(evt, (e) => {
    e.preventDefault();
    amDropZone.classList.remove("dragover");
  })
);
amDropZone.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
});

function addFiles(fileList) {
  for (const file of fileList) {
    amFiles.push({ file, url: URL.createObjectURL(file) });
  }
  amEditor.classList.remove("hidden");
  amResetOutputs();
  renderList();
  amEditor.scrollIntoView({ behavior: "smooth" });
}

// --- Render de la lista ordenable ---
function renderList() {
  amList.innerHTML = "";
  amFiles.forEach((item, i) => {
    const li = document.createElement("li");
    li.className = "am-item";
    li.innerHTML = `
      <span class="am-pos">${i + 1}</span>
      <span class="am-name">${escapeHtml(item.file.name)}</span>
      <span class="am-actions">
        <button title="Escuchar" data-act="play" data-i="${i}">▶</button>
        <button title="Subir" data-act="up" data-i="${i}" ${i === 0 ? "disabled" : ""}>▲</button>
        <button title="Bajar" data-act="down" data-i="${i}" ${i === amFiles.length - 1 ? "disabled" : ""}>▼</button>
        <button title="Quitar" data-act="del" data-i="${i}" class="am-del">✕</button>
      </span>`;
    amList.appendChild(li);
  });
  amProcessBtn.disabled = amFiles.length < 2;
}

amList.addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const i = +btn.dataset.i;
  const act = btn.dataset.act;
  if (act === "play") playItem(i);
  else if (act === "up") swap(i, i - 1);
  else if (act === "down") swap(i, i + 1);
  else if (act === "del") removeItem(i);
});

function swap(a, b) {
  if (b < 0 || b >= amFiles.length) return;
  [amFiles[a], amFiles[b]] = [amFiles[b], amFiles[a]];
  amResetOutputs();
  renderList();
}

function removeItem(i) {
  URL.revokeObjectURL(amFiles[i].url);
  amFiles.splice(i, 1);
  amResetOutputs();
  if (amFiles.length === 0) {
    amEditor.classList.add("hidden");
  }
  renderList();
}

function playItem(i) {
  amPlayer.src = amFiles[i].url;
  amPlayer.play();
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

// --- Slider de silencio ---
amGap.addEventListener("input", () => {
  el("am-gap-val").textContent = (+amGap.value).toFixed(1);
});

// --- Process ---
amProcessBtn.addEventListener("click", () => {
  if (amFiles.length < 2) return;
  amPlayer.pause();

  const form = new FormData();
  amFiles.forEach((item) => form.append("audios", item.file));
  form.append("gap", amGap.value);

  amResetOutputs();
  amProcessBtn.disabled = true;
  amProgressWrap.classList.remove("hidden");
  amSetProgress(0);

  runJob(AM_API, form, {
    onProgress: amSetProgress,
    onDone: amShowResult,
    onError: amShowError,
  });
});

function amSetProgress(pct) {
  amProgressFill.style.width = `${pct}%`;
  amProgressLabel.textContent = `Procesando… ${pct}%`;
}

function amShowResult(jobId) {
  amProgressWrap.classList.add("hidden");
  amDownloadBtn.href = `${AM_API}/download/${jobId}`;
  amResultWrap.classList.remove("hidden");
  amProcessBtn.disabled = false;
}

function amShowError(msg) {
  amProgressWrap.classList.add("hidden");
  amErrorMsg.textContent = msg;
  amErrorWrap.classList.remove("hidden");
  amProcessBtn.disabled = false;
}

function amResetOutputs() {
  amProgressWrap.classList.add("hidden");
  amResultWrap.classList.add("hidden");
  amErrorWrap.classList.add("hidden");
}

// --- Reset ---
el("am-reset-btn").addEventListener("click", () => {
  amPlayer.pause();
  amFiles.forEach((item) => URL.revokeObjectURL(item.url));
  amFiles = [];
  amEditor.classList.add("hidden");
  amResetOutputs();
  renderList();
  window.scrollTo({ top: 0, behavior: "smooth" });
});
el("am-error-reset-btn").addEventListener("click", () => {
  amErrorWrap.classList.add("hidden");
});
