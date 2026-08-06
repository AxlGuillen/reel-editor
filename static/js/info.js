(function () {
  const LABELS = {
    python:         "Python",
    flask:          "Flask",
    yt_dlp:         "yt-dlp",
    faster_whisper: "faster-whisper",
    ffmpeg:         "FFmpeg",
    ffprobe:        "ffprobe",
    js_runtime:     "Runtime JS (YouTube)",
  };

  let loaded = false;

  async function loadInfo() {
    const loading = el("info-loading");
    const table   = el("info-table");
    const tbody   = el("info-tbody");
    const btn     = el("info-refresh-btn");

    loading.classList.remove("hidden");
    table.classList.add("hidden");
    btn.style.display = "none";

    try {
      const res  = await fetch("/api/info");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

      tbody.innerHTML = Object.entries(LABELS)
        .map(([key, label]) => {
          const ver = data[key] || "—";
          return `<tr><td>${label}</td><td class="info-version">${ver}</td></tr>`;
        })
        .join("");

      loading.classList.add("hidden");
      table.classList.remove("hidden");
      btn.style.display = "";
      loaded = true;
    } catch (err) {
      loading.textContent = `Error al cargar: ${err.message}`;
      btn.style.display = "";
    }
  }

  el("info-refresh-btn").addEventListener("click", loadInfo);

  // Cargar automáticamente la primera vez que el usuario abre la vista.
  document.querySelector('[data-target="view-info"]').addEventListener("click", () => {
    if (!loaded) loadInfo();
  });
})();
