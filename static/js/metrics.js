// Métricas: los indicadores de los trabajos de Competitivo y la calibración.
// Los datos vienen de /api/competitivo/metrics (un registro por trabajo, en
// COMPETITIVO_FOLDER/_metricas) y /api/competitivo/calibration.
(function () {
  const KINDS = {
    fase1: "Fase 1",
    resumen: "Resumen",
    edit: "Edit (plan)",
    edit_render: "Edit (render)",
    diario: "Resumen diario",
    transcripcion: "Transcripción",
    calibracion: "Calibración",
  };
  const STATUS = { ok: "bien", paused: "en pausa", error: "falló" };
  const CAL = { ok: "bien", fail: "FALLA", error: "ERROR", missing: "falta el archivo",
    known: "conocida", fixed: "¡arreglada!" };

  let days = 7;

  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)} %`);
  const secs = (v) => {
    if (v === null || v === undefined) return "—";
    return v >= 90 ? `${Math.floor(v / 60)} min ${Math.round(v % 60)} s` : `${Math.round(v)} s`;
  };
  const when = (iso) => new Date(iso).toLocaleString("es", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

  function kpi(num, label, sub, accent) {
    return `<div class="card tl-stat${accent ? " tl-stat-accent" : ""}">
      <span class="tl-stat-num">${esc(num)}</span>
      <span class="tl-stat-label">${esc(label)}</span>
      ${sub ? `<span class="mt-stat-sub">${esc(sub)}</span>` : ""}
    </div>`;
  }

  function detail(r) {
    const bits = [];
    if (r.preset) bits.push(`preset ${r.preset}`);
    if (r.anchors) bits.push(`anclas ${r.anchors.join(", ")}`);
    if (r.kills) bits.push(`${r.kills} kills (${r.kills_sure || 0} seguras, ${r.kills_full || 0} plano entero)`);
    if (r.overrides) bits.push(`${r.overrides} correcciones`);
    if (r.retries) bits.push("reintento");
    if (r.video_s) bits.push(`video ${secs(r.video_s)}`);
    if (r.nvenc_fallbacks) bits.push(`${r.nvenc_fallbacks}× NVENC → CPU`);
    if (r.skipped) bits.push(`${r.skipped} tramo(s) sin video`);
    if (r.error) bits.push(r.error);
    return bits.join(" · ");
  }

  function render(data) {
    const s = data.summary;
    const ok = Object.values(s.kinds).reduce((a, k) => a + k.ok, 0);
    el("mt-kpis").innerHTML = [
      kpi(s.jobs, "trabajos", s.jobs ? `${ok} bien · ${s.failed} fallaron` : "", false),
      kpi(pct(s.anchors_high_pct), "anclas seguras", s.anchors ? `de ${s.anchors}` : "", true),
      kpi(pct(s.clock_calibrated_pct), "reloj calibrado", "", false),
      kpi(pct(s.kills_sure_pct), "kills con foco seguro", s.kills ? `de ${s.kills}` : "", false),
      kpi(pct(s.kills_full_pct), "kills en plano entero", "", false),
      kpi(s.overrides_per_edit ?? "—", "correcciones por edit", "", false),
      kpi(s.retries, "reintentos", "", false),
      kpi(s.nvenc_fallbacks, "NVENC → CPU", "", false),
      kpi(s.warnings, "con aviso", `${s.skipped_spans} tramos sin video`, false),
      kpi(data.discarded, "descartados", "versiones rehechas", false),
    ].join("");

    const kinds = Object.entries(s.kinds);
    el("mt-kinds").innerHTML = kinds.map(([k, v]) => `<tr>
      <td>${esc(KINDS[k] || k)}</td><td>${v.jobs}</td><td>${v.ok}</td>
      <td>${v.failed ? `<span class="mt-bad">${v.failed}</span>` : 0}</td>
      <td>${secs(v.median_s)}</td><td>${secs(v.median_video_s)}</td></tr>`).join("");
    el("mt-kinds-empty").classList.toggle("hidden", kinds.length > 0);

    el("mt-recent").innerHTML = data.recent.map((r) => `<tr>
      <td class="mt-nowrap">${esc(when(r.at))}</td><td>${esc(KINDS[r.kind] || r.kind)}</td>
      <td><span class="mt-${r.status}">${esc(STATUS[r.status] || r.status)}</span></td>
      <td>${secs(r.seconds)}</td><td class="mt-detail">${esc(detail(r))}</td></tr>`).join("")
      || `<tr><td colspan="5" class="hint">Sin trabajos en este período.</td></tr>`;
    el("mt-file").textContent = `Registro: ${data.file}`;
  }

  function renderCalibration(report) {
    const status = el("mt-cal-status");
    if (!report) {
      status.textContent = "Todavía no se corrió.";
      el("mt-cal-table").classList.add("hidden");
      return;
    }
    status.className = `mt-cal-status ${report.passed ? "mt-ok" : "mt-bad"}`;
    status.textContent = `${when(report.at)} · ${report.ok}/${report.cases} bien`
      + (report.fail ? ` · ${report.fail} fallas` : "")
      + (report.error ? ` · ${report.error} errores` : "")
      + (report.known ? ` · ${report.known} conocidas` : "")
      + (report.missing ? ` · ${report.missing} sin archivo` : "")
      + ` · ${secs(report.seconds)}`;
    el("mt-cal-rows").innerHTML = (report.results || []).map((r) => {
      let info = r.error || r.known_issue || r.note || "";
      if (r.status === "fail") info = `esperado ${JSON.stringify(r.expected)} · medido ${JSON.stringify(r.measured)}`;
      if (r.status === "fixed") info = "Ya da bien: sacale known_issue al caso.";
      return `<tr><td class="mt-nowrap">${esc(r.id)}</td><td>${esc(r.check)}</td>
        <td><span class="mt-cal-${r.status}">${esc(CAL[r.status] || r.status)}</span></td>
        <td class="mt-detail">${esc(info)}</td></tr>`;
    }).join("");
    el("mt-cal-table").classList.remove("hidden");
  }

  async function load() {
    const loading = el("mt-loading");
    try {
      const [mres, cres] = await Promise.all([
        fetch(`/api/competitivo/metrics?days=${days}`), fetch("/api/competitivo/calibration")]);
      const data = await mres.json();
      const cal = await cres.json();
      if (!mres.ok) throw new Error(data.error || `HTTP ${mres.status}`);
      render(data);
      renderCalibration(cal.report);
      loading.classList.add("hidden");
      el("mt-body").classList.remove("hidden");
    } catch (err) {
      loading.classList.remove("hidden");
      loading.textContent = err.message === "Failed to fetch"
        ? "No se pudo conectar con el servidor. ¿Está la app corriendo?"
        : `Error al cargar las métricas: ${err.message}`;
    }
  }

  async function runCalibration() {
    const btn = el("mt-cal-run");
    const status = el("mt-cal-status");
    btn.disabled = true;
    status.className = "mt-cal-status";
    try {
      const res = await fetch("/api/competitivo/calibration", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        const job = await (await fetch(`/api/competitivo/status/${data.job_id}`)).json();
        if (job.status === "error") throw new Error(job.error);
        if (job.status === "done") { renderCalibration(job.resultado); break; }
        status.textContent = job.stage || "Corriendo…";
      }
      load();
    } catch (err) {
      status.className = "mt-cal-status mt-bad";
      status.textContent = `No se pudo correr: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  }

  el("mt-days").addEventListener("click", (ev) => {
    const chip = ev.target.closest(".tl-chip");
    if (!chip) return;
    el("mt-days").querySelectorAll(".tl-chip").forEach((c) => c.classList.toggle("active", c === chip));
    days = Number(chip.dataset.days);
    load();
  });
  el("mt-reload").addEventListener("click", load);
  el("mt-cal-run").addEventListener("click", runCalibration);

  // Se carga al abrir la vista (y de nuevo cada vez: los trabajos cambian).
  document.querySelector('[data-target="view-metrics"]')
    .addEventListener("click", () => load());
})();
