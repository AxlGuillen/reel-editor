(function () {
// ReelForge — módulo downloader (descarga de assets con yt-dlp).
const DL_API = "/api/downloader";

const dlUrl = el("dl-url");
const dlQuality = el("dl-quality");
const dlProcessBtn = el("dl-process-btn");

const dlProgressWrap = el("dl-progress-wrap");
const dlProgressFill = el("dl-progress-fill");
const dlProgressLabel = el("dl-progress-label");
const dlResultWrap = el("dl-result-wrap");
const dlResultTitle = el("dl-result-title");
const dlDownloadBtn = el("dl-download-btn");
const dlErrorWrap = el("dl-error-wrap");
const dlErrorMsg = el("dl-error-msg");

let dlFormat = "audio";

// Opciones de calidad por formato (value -> label).
const DL_QUALITIES = {
  audio: [["320", "320 kbps"], ["192", "192 kbps"], ["128", "128 kbps"]],
  video: [["max", "Máxima"], ["1080", "1080p"], ["720", "720p"], ["480", "480p"]],
};
const DL_DEFAULT = { audio: "192", video: "1080" };

function populateQuality() {
  dlQuality.innerHTML = "";
  DL_QUALITIES[dlFormat].forEach(([value, label]) => {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    if (value === DL_DEFAULT[dlFormat]) opt.selected = true;
    dlQuality.appendChild(opt);
  });
}
populateQuality();

// --- Toggle de formato ---
document.querySelectorAll(".dl-fmt").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".dl-fmt").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    dlFormat = btn.dataset.fmt;
    populateQuality();
  });
});

// --- Cookies: selector de fuente + gestión del cookies.txt subido ---
const dlCookies = el("dl-cookies");
const dlCookiesFile = el("dl-cookies-file");
const dlCookiesStatus = el("dl-cookies-status");
const dlCookiesDel = el("dl-cookies-del");

// Recordar la última fuente elegida (p. ej. "file") entre sesiones.
try {
  const saved = localStorage.getItem("reelforge-dl-cookies");
  if (saved !== null && [...dlCookies.options].some((o) => o.value === saved)) {
    dlCookies.value = saved;
  }
} catch (e) {}

function paintCookiesUI() {
  dlCookiesFile.classList.toggle("hidden", dlCookies.value !== "file");
  if (dlCookies.value === "file") refreshCookiesStatus();
}

async function refreshCookiesStatus() {
  try {
    const res = await fetch(`${DL_API}/cookies`);
    const data = await res.json();
    if (data.exists) {
      const fecha = new Date(data.mtime * 1000).toLocaleDateString();
      dlCookiesStatus.textContent = `✓ cookies.txt cargado (${fecha})`;
      dlCookiesDel.classList.remove("hidden");
    } else {
      dlCookiesStatus.textContent = "Sin archivo cargado";
      dlCookiesDel.classList.add("hidden");
    }
  } catch (e) {
    dlCookiesStatus.textContent = "";
  }
}

dlCookies.addEventListener("change", () => {
  try { localStorage.setItem("reelforge-dl-cookies", dlCookies.value); } catch (e) {}
  paintCookiesUI();
});
paintCookiesUI();

el("dl-cookies-upload").addEventListener("click", () => el("dl-cookies-input").click());
el("dl-cookies-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("cookies", file);
  try {
    const res = await fetch(`${DL_API}/cookies`, { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    refreshCookiesStatus();
  } catch (err) {
    dlCookiesStatus.textContent = err.message;
  }
  e.target.value = "";
});

dlCookiesDel.addEventListener("click", async () => {
  if (!confirm("¿Borrar el cookies.txt guardado?")) return;
  await fetch(`${DL_API}/cookies`, { method: "DELETE" });
  refreshCookiesStatus();
});

// --- Process ---
dlProcessBtn.addEventListener("click", () => {
  const url = dlUrl.value.trim();
  if (!url) {
    dlUrl.focus();
    return;
  }

  const form = new FormData();
  form.append("url", url);
  form.append("format", dlFormat);
  form.append("quality", dlQuality.value);
  form.append("cookies_browser", el("dl-cookies").value);

  dlResetOutputs();
  dlProcessBtn.disabled = true;
  dlProgressWrap.classList.remove("hidden");
  dlSetProgress(0);

  runJob(DL_API, form, {
    onProgress: dlSetProgress,
    onDone: dlShowResult,
    onError: dlShowError,
  });
});

dlUrl.addEventListener("keydown", (e) => {
  if (e.key === "Enter") dlProcessBtn.click();
});

function dlSetProgress(pct) {
  dlProgressFill.style.width = `${pct}%`;
  dlProgressLabel.textContent = `Descargando… ${pct}%`;
}

async function dlShowResult(jobId) {
  dlProgressWrap.classList.add("hidden");
  // Recuperamos el título para mostrarlo.
  try {
    const res = await fetch(`${DL_API}/status/${jobId}`);
    const data = await res.json();
    dlResultTitle.textContent = data.title ? `“${data.title}”` : "";
  } catch (_) {
    dlResultTitle.textContent = "";
  }
  dlDownloadBtn.href = `${DL_API}/download/${jobId}`;
  dlResultWrap.classList.remove("hidden");
  dlProcessBtn.disabled = false;
}

function dlShowError(msg) {
  dlProgressWrap.classList.add("hidden");
  dlErrorMsg.textContent = msg;
  dlErrorWrap.classList.remove("hidden");
  dlProcessBtn.disabled = false;
}

function dlResetOutputs() {
  dlProgressWrap.classList.add("hidden");
  dlResultWrap.classList.add("hidden");
  dlErrorWrap.classList.add("hidden");
}

// --- Reset ---
el("dl-reset-btn").addEventListener("click", () => {
  dlUrl.value = "";
  dlResetOutputs();
  dlUrl.focus();
});
el("dl-error-reset-btn").addEventListener("click", () => {
  dlErrorWrap.classList.add("hidden");
});
})();
