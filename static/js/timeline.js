// Historial: línea del tiempo del proyecto (solo lectura).
// Los datos vienen de /api/timeline (git log parseado en el server); acá solo
// se agrupan por mes, se pintan y se filtran por tipo o texto.
(function () {
  const KINDS = {
    feat: "Nuevo",
    fix: "Arreglo",
    perf: "Optimización",
    otro: "Interno",
  };
  const MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
  const MESES_CORTO = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago",
    "sep", "oct", "nov", "dic"];

  let entries = [];
  let loaded = false;
  let filterKind = "all";
  let query = "";

  // "2026-09-05" partido a mano: con new Date(iso) el string se toma como UTC
  // y en nuestro huso la fecha se corre un día para atrás.
  const parseDate = (iso) => {
    const [y, m, d] = iso.split("-").map(Number);
    return { y, m: m - 1, d };
  };
  const shortDate = (iso) => {
    const { m, d } = parseDate(iso);
    return `${d} ${MESES_CORTO[m]}`;
  };
  const longDate = (iso) => {
    const { y, m, d } = parseDate(iso);
    return `${d} de ${MESES[m]} de ${y}`;
  };
  const monthLabel = (iso) => {
    const { y, m } = parseDate(iso);
    const name = MESES[m];
    return `${name[0].toUpperCase()}${name.slice(1)} ${y}`;
  };

  // Los cuerpos de los commits traen tags literales (<video>, <details>): sin
  // escapar, el navegador los interpretaría como HTML.
  const esc = (s) => s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function detailHtml(blocks) {
    return blocks.map((b) => b.type === "ul"
      ? `<ul>${b.items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`
      : `<p>${esc(b.text)}</p>`).join("");
  }

  function itemHtml(e) {
    const scopes = e.scopes
      .map((s) => `<span class="tl-scope">${esc(s)}</span>`).join("");
    const detail = e.detail.length
      ? `<details class="tl-detail">
           <summary>Detalle</summary>
           <div class="tl-detail-body">${detailHtml(e.detail)}</div>
         </details>`
      : "";
    return `
      <article class="tl-item" data-kind="${e.kind}">
        <div class="tl-rail"><span class="tl-dot"></span></div>
        <div class="card tl-card">
          <div class="tl-meta">
            <span class="tl-kind tl-kind-${e.kind}">${KINDS[e.kind]}</span>
            ${scopes}
            <time class="tl-date" title="${longDate(e.date)}">${shortDate(e.date)}</time>
            <code class="tl-hash">${esc(e.hash)}</code>
          </div>
          <h3 class="tl-title">${esc(e.title)}</h3>
          ${detail}
        </div>
      </article>`;
  }

  function matches(e) {
    if (filterKind !== "all" && e.kind !== filterKind) return false;
    if (!query) return true;
    const hay = [e.title, e.scopes.join(" "), e.hash,
      e.detail.map((b) => b.text || b.items.join(" ")).join(" ")]
      .join(" ").toLowerCase();
    return hay.includes(query);
  }

  function render() {
    const list = el("tl-list");
    const visibles = entries.filter(matches);
    el("tl-empty").classList.toggle("hidden", visibles.length > 0);

    let html = "";
    let mes = null;
    visibles.forEach((e) => {
      const m = e.date.slice(0, 7);
      if (m !== mes) {
        mes = m;
        const n = visibles.filter((x) => x.date.startsWith(m)).length;
        html += `<div class="tl-month">
            <span class="tl-month-label">${monthLabel(e.date)}</span>
            <span class="tl-month-count">${n} ${n === 1 ? "hito" : "hitos"}</span>
          </div>`;
      }
      html += itemHtml(e);
    });
    list.innerHTML = html;
  }

  async function load() {
    const loading = el("tl-loading");
    try {
      const res = await fetch("/api/timeline");
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      if (!data.available) {
        loading.textContent = `No se pudo leer el historial: ${data.error}`;
        return;
      }
      entries = data.entries;
      const s = data.stats;
      el("tl-n-total").textContent = s.total;
      el("tl-n-feat").textContent = s.feat;
      el("tl-n-fix").textContent = s.fix;
      el("tl-n-perf").textContent = s.perf;
      el("tl-range").textContent = s.first
        ? `desde el ${longDate(s.first)} · ${s.months} ${s.months === 1 ? "mes" : "meses"}`
        : "";
      render();
      loading.classList.add("hidden");
      el("tl-body").classList.remove("hidden");
      loaded = true;
    } catch (err) {
      loading.textContent = err.message === "Failed to fetch"
        ? "No se pudo conectar con el servidor. ¿Está la app corriendo?"
        : `Error al cargar el historial: ${err.message}`;
    }
  }

  el("tl-filters").addEventListener("click", (ev) => {
    const chip = ev.target.closest(".tl-chip");
    if (!chip) return;
    el("tl-filters").querySelectorAll(".tl-chip")
      .forEach((c) => c.classList.toggle("active", c === chip));
    filterKind = chip.dataset.kind;
    render();
  });

  el("tl-search").addEventListener("input", (ev) => {
    query = ev.target.value.trim().toLowerCase();
    render();
  });

  // Se carga la primera vez que se abre la vista (no en cada arranque).
  document.querySelector('[data-target="view-timeline"]')
    .addEventListener("click", () => { if (!loaded) load(); });
})();
