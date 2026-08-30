// ReelForge — navegación de la sidebar. Muestra una vista de módulo a la vez.
document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".module-view").forEach((v) => v.classList.add("hidden"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.target).classList.remove("hidden");
  });
});

// --- Rail: colapsar / expandir por elección ---
// El estado ya viene aplicado por el script del <head> (evita el salto de
// ancho al cargar); acá solo se refleja en el botón y se persiste al cambiar.
const railToggle = document.getElementById("rail-toggle");
const railLabel = document.getElementById("rail-toggle")
  ?.querySelector(".rail-toggle-label");

// Con el rail colapsado solo se ve el ícono: el tooltip nativo da el nombre
// sin tener que expandir. Se toma del propio texto para no duplicarlo.
document.querySelectorAll(".nav-item").forEach((btn) => {
  const txt = btn.textContent.trim();
  if (txt) btn.title = txt;
});

function paintRail(state) {
  const expanded = state === "expanded";
  if (railLabel) railLabel.textContent = expanded ? "Colapsar" : "Expandir";
  railToggle.title = expanded ? "Colapsar menú" : "Expandir menú";
  railToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
}

paintRail(document.documentElement.getAttribute("data-rail"));

railToggle.addEventListener("click", () => {
  const next =
    document.documentElement.getAttribute("data-rail") === "expanded"
      ? "collapsed"
      : "expanded";
  document.documentElement.setAttribute("data-rail", next);
  try { localStorage.setItem("reelforge-rail", next); } catch (e) {}
  paintRail(next);
});

// --- Tema claro / oscuro ---
// El tema ya quedó aplicado por el script del <head> (evita el parpadeo);
// acá solo se refleja en el botón y se persiste al cambiarlo.
const themeBtn = document.getElementById("theme-btn");
const themeIcon = document.getElementById("theme-icon");
const themeLabel = document.getElementById("theme-label");

function paintTheme(theme) {
  const dark = theme === "dark";
  // El botón anuncia a qué tema se cambia, no en cuál se está.
  themeIcon.setAttribute("href", dark ? "#i-sun" : "#i-moon");
  themeLabel.textContent = dark ? "Tema claro" : "Tema oscuro";
  themeBtn.title = dark ? "Cambiar a tema claro" : "Cambiar a tema oscuro";
}

paintTheme(document.documentElement.getAttribute("data-theme"));

themeBtn.addEventListener("click", () => {
  const next =
    document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try { localStorage.setItem("reelforge-theme", next); } catch (e) {}
  paintTheme(next);
});

// Limpiar archivos temporales (uploads, outputs, downloads).
const cleanupBtn = document.getElementById("cleanup-btn");
const cleanupStatus = document.getElementById("cleanup-status");

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

cleanupBtn.addEventListener("click", async () => {
  if (!confirm("¿Borrar todos los archivos temporales (subidos, procesados y descargados)?")) return;

  cleanupBtn.disabled = true;
  cleanupStatus.textContent = "Limpiando…";
  cleanupStatus.classList.remove("err");

  try {
    const res = await fetch("/api/cleanup", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Error al limpiar");
    cleanupStatus.textContent =
      data.removed > 0
        ? `✓ ${data.removed} archivo(s), ${formatBytes(data.freed_bytes)} liberados`
        : "✓ Ya estaba limpio";
  } catch (err) {
    const msg = err.message === "Failed to fetch"
      ? "No se pudo conectar con el servidor. ¿Está la app corriendo?"
      : err.message;
    cleanupStatus.textContent = msg;
    cleanupStatus.classList.add("err");
  } finally {
    cleanupBtn.disabled = false;
  }
});
