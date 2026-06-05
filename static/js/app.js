// ReelForge — navegación de la sidebar. Muestra una vista de módulo a la vez.
document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".module-view").forEach((v) => v.classList.add("hidden"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.target).classList.remove("hidden");
  });
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
    cleanupStatus.textContent = err.message;
    cleanupStatus.classList.add("err");
  } finally {
    cleanupBtn.disabled = false;
  }
});
