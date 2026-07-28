(() => {
  "use strict";

  const WINDOW_HOURS = 24;
  const GOLDEN_ANGLE = 2.399963; // radianti (~137.508°)
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const app = document.getElementById("app");
  const stage = document.getElementById("stage");
  const openBtn = document.getElementById("open-btn");
  const synapsesSvg = document.getElementById("synapses");
  const neuronsLayer = document.getElementById("neurons-layer");
  const emptyState = document.getElementById("empty-state");
  const filtersEl = document.getElementById("filters");
  const lastUpdatedEl = document.getElementById("last-updated");
  const detailPanel = document.getElementById("detail-panel");
  const detailContent = document.getElementById("detail-content");
  const detailOverlay = document.getElementById("detail-overlay");
  const detailClose = document.getElementById("detail-close");

  let allBriefs = [];
  let visibleBriefs = [];
  let activeCategory = null;
  let hasOpened = false;

  // ------------------------------------------------------------------ //
  // Caricamento dati
  // ------------------------------------------------------------------ //

  async function loadBriefs() {
    try {
      const res = await fetch("news.json", { cache: "no-store" });
      if (!res.ok) throw new Error("news.json non raggiungibile");
      const data = await res.json();
      const cutoff = Date.now() - WINDOW_HOURS * 3600 * 1000;
      allBriefs = data.filter((b) => new Date(b.published_at).getTime() >= cutoff);
      allBriefs.sort((a, b) => new Date(b.published_at) - new Date(a.published_at));
      visibleBriefs = allBriefs;
      renderFilters();
      renderLastUpdated(data);
    } catch (err) {
      console.error("Errore nel caricamento di news.json:", err);
      allBriefs = [];
      visibleBriefs = [];
    }
  }

  function renderLastUpdated(rawData) {
    if (!rawData.length) { lastUpdatedEl.textContent = ""; return; }
    const latest = rawData.reduce((max, b) =>
      new Date(b.fetched_at) > new Date(max) ? b.fetched_at : max, rawData[0].fetched_at);
    const d = new Date(latest);
    const time = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
    const sameDay = new Date().toDateString() === d.toDateString();
    lastUpdatedEl.textContent = sameDay
      ? `Aggiornato oggi alle ${time}`
      : `Aggiornato il ${d.toLocaleDateString("it-IT")} alle ${time}`;
  }

  // ------------------------------------------------------------------ //
  // Apertura della mente
  // ------------------------------------------------------------------ //
  
/* //////////////BLOCCO DA ELIMINARE //////////////////
  openBtn.addEventListener("click", () => {
    if (hasOpened) return;
    hasOpened = true;
    app.classList.add("opened");

    if (!visibleBriefs.length) {
      emptyState.hidden = false;
      requestAnimationFrame(() => emptyState.classList.add("visible"));
      return;
    }
    layoutNeurons();
  });

  *//////////// BLOCCO DA ELIMINARE ///////////////////////

  ////////////////////////SOSTITUTO TEMPORANEO///////////////////
  openBtn.onclick = function () {
  alert("CLICK FUNZIONA");

  hasOpened = true;
  app.classList.add("opened");

  if (!visibleBriefs.length) {
    emptyState.hidden = false;
    return;
  }

  layoutNeurons();
};
  ///////////////////////TERMINE SOSTITUTO TEMPORANEO///////////////////



  
  window.addEventListener("resize", debounce(() => {
    if (hasOpened && visibleBriefs.length) layoutNeurons();
  }, 200));

  function debounce(fn, wait) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
  }

  // ------------------------------------------------------------------ //
  // Layout a spirale (angolo aureo) + rendering neuroni/sinapsi
  // ------------------------------------------------------------------ //

  function layoutNeurons() {
    neuronsLayer.innerHTML = "";
    synapsesSvg.innerHTML = "";

    const rect = stage.getBoundingClientRect();
    const originX = rect.width / 2;
    const originY = rect.height / 2;
    const minRadius = Math.min(rect.width, rect.height) * 0.16 + 60;
    const maxRadius = Math.min(rect.width, rect.height) * 0.44;
    const n = visibleBriefs.length;

    visibleBriefs.forEach((brief, i) => {
      const angle = i * GOLDEN_ANGLE;
      const t = n > 1 ? Math.sqrt((i + 1) / n) : 1;
      const radius = minRadius + (maxRadius - minRadius) * t;
      const x = originX + radius * Math.cos(angle);
      const y = originY + radius * Math.sin(angle);

      drawSynapse(originX, originY, x, y, i);
      drawNeuron(brief, x, y, i);
    });
  }

  function drawSynapse(ox, oy, x, y, index) {
    const mx = (ox + x) / 2 + (y - oy) * 0.08 * (index % 2 === 0 ? 1 : -1);
    const my = (oy + y) / 2 + (ox - x) * 0.08 * (index % 2 === 0 ? 1 : -1);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M ${ox} ${oy} Q ${mx} ${my} ${x} ${y}`);
    path.setAttribute("class", "synapse");
    path.dataset.index = String(index);
    if (!REDUCED_MOTION) path.style.transitionDelay = `${Math.min(index * 35, 900)}ms`;
    synapsesSvg.appendChild(path);
  }

  function drawNeuron(brief, x, y, index) {
    const el = document.createElement("div");
    el.className = "neuron" + (brief.image_url ? "" : " no-image");
    el.dataset.category = brief.category;
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
    if (brief.image_url) el.style.backgroundImage = `url("${brief.image_url}")`;
    if (!REDUCED_MOTION) {
      el.style.setProperty("--delay", `${Math.min(index * 0.035, 0.9)}s`);
      el.style.setProperty("--float-duration", `${5 + Math.random() * 3}s`);
      el.style.setProperty("--float-delay", `${Math.random() * 3}s`);
    }
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", brief.title);

    const tooltip = document.createElement("span");
    tooltip.className = "neuron-tooltip";
    tooltip.textContent = brief.title;
    el.appendChild(tooltip);

    const open = () => openDetail(brief, el);
    el.addEventListener("click", open);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });

    neuronsLayer.appendChild(el);
  }

  // ------------------------------------------------------------------ //
  // Filtri per categoria
  // ------------------------------------------------------------------ //

  function renderFilters() {
    filtersEl.innerHTML = "";
    const categories = [...new Set(allBriefs.map((b) => b.category))];
    if (categories.length < 2) return;

    const allChip = makeChip("Tutte", null);
    allChip.classList.add("active");
    filtersEl.appendChild(allChip);
    categories.forEach((cat) => filtersEl.appendChild(makeChip(cat, cat)));
  }

  function makeChip(label, category) {
    const chip = document.createElement("button");
    chip.className = "chip";
    chip.textContent = label;
    if (category) chip.dataset.category = category;
    chip.addEventListener("click", () => applyFilter(category, chip));
    return chip;
  }

  function applyFilter(category, chipEl) {
    activeCategory = category;
    [...filtersEl.children].forEach((c) => c.classList.remove("active"));
    chipEl.classList.add("active");

    [...neuronsLayer.children].forEach((el) => {
      const match = !category || el.dataset.category === category;
      el.classList.toggle("dimmed", !match);
    });
    [...synapsesSvg.children].forEach((path) => {
      const idx = Number(path.dataset.index);
      const brief = visibleBriefs[idx];
      const match = !category || brief.category === category;
      path.style.opacity = match ? "" : "0.06";
    });
  }

  // ------------------------------------------------------------------ //
  // Pannello di dettaglio
  // ------------------------------------------------------------------ //

  function openDetail(brief, neuronEl) {
    [...neuronsLayer.children].forEach((el) => el.classList.remove("selected"));
    neuronEl.classList.add("selected");

    detailContent.setAttribute("data-category", brief.category);
    detailContent.innerHTML = buildDetailHTML(brief);
    detailPanel.classList.add("visible");
    detailOverlay.classList.add("visible");
    detailPanel.setAttribute("aria-hidden", "false");
    detailClose.focus();
  }

  function closeDetail() {
    detailPanel.classList.remove("visible");
    detailOverlay.classList.remove("visible");
    detailPanel.setAttribute("aria-hidden", "true");
    [...neuronsLayer.children].forEach((el) => el.classList.remove("selected"));
  }

  detailClose.addEventListener("click", closeDetail);
  detailOverlay.addEventListener("click", closeDetail);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && detailPanel.classList.contains("visible")) closeDetail();
  });

  function formatDate(iso) {
    return new Date(iso).toLocaleDateString("it-IT", {
      day: "numeric", month: "long", hour: "2-digit", minute: "2-digit",
    });
  }

  function esc(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  }

  function buildDetailHTML(b) {
    const imageBlock = b.image_url
      ? `<img class="d-image" src="${esc(b.image_url)}" alt="" loading="lazy">`
      : `<div class="d-image neuron no-image" style="position:static;transform:none;border-radius:14px;"></div>`;

    const preprintBadge = b.is_preprint
      ? `<span class="d-preprint">Preprint — non peer-reviewed</span>` : "";

    return `
      ${imageBlock}
      <div class="d-meta">
        <span>${esc(b.source_name)}</span>
        <span class="d-badge">${esc(b.category)}</span>
        <span>${formatDate(b.published_at)}</span>
        ${preprintBadge}
      </div>

      <p class="d-bluf-label">BLUF</p>
      <p class="d-bluf">${esc(b.big_problem)}</p>

      <div class="d-intro">
        <p class="d-section-label">Intro</p>
        <div class="d-intro-row"><strong>Problema</strong><span>${esc(b.small_problem)}</span></div>
        <div class="d-intro-row"><strong>Idea</strong><span>${esc(b.idea)}</span></div>
        <div class="d-intro-row"><strong>Piano</strong><span>${esc(b.plan)}</span></div>
      </div>

      <p class="d-section-label">Risultati</p>
      <div class="d-results">
        ${[1, 2, 3].map((n) => `
          <div class="d-result-card">
            <div class="d-result-headline">${esc(b[`result_${n}_headline`])}</div>
            <span class="d-result-number">${esc(b[`result_${n}_number`])}</span>
            <div class="d-result-detail">${esc(b[`result_${n}_detail`])}</div>
          </div>`).join("")}
      </div>

      <p class="d-section-label">Conclusione</p>
      <p class="d-conclusion">${esc(b.conclusion)}</p>

      <p class="d-section-label">Direzioni future</p>
      <p class="d-future">${esc(b.future_directions)}</p>

      <div class="d-actions">
        <a class="d-source-link" href="${esc(b.source_url)}" target="_blank" rel="noopener noreferrer">
          Leggi la fonte originale ↗
        </a>
      </div>
      <p class="d-attribution">
        Contenuto elaborato automaticamente a partire dalla fonte indicata.
        Testo e diritti originali di ${esc(b.source_name)}.
      </p>
    `;
  }

  // ------------------------------------------------------------------ //
  // Avvio
  // ------------------------------------------------------------------ //

  loadBriefs();
})();
