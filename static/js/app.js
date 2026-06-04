// ReelForge — navegación de la sidebar. Muestra una vista de módulo a la vez.
document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".module-view").forEach((v) => v.classList.add("hidden"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.target).classList.remove("hidden");
  });
});
