/* ========================================================================
   BE IN THE LOOP — Neural Editorial Engine
   ======================================================================== */

(() => {
  "use strict";

  /* ---------------------------------------------------------------- */
  /* Config                                                            */
  /* ---------------------------------------------------------------- */

  const NEWS_FILE = "news.json";
  const WINDOW_HOURS = 24;
  const MAX_PAPERS = 10;
  const REFRESH_MINUTES = 15; // ricontrolla news.json periodicamente a pagina aperta
  const GOLDEN_ANGLE = 2.399963;
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------------- */
  /* DOM                                                                */
  /* ---------------------------------------------------------------- */

  const app = document.getElementById("app");
  const openBtn = document.getElementById("open-btn");
  const stage = document.getElementById("stage");
  const neuralCore = document.getElementById("neural-core");
  const brainWrap = document.getElementById("brain-wrap");
  const connectorRing = document.getElementById("connector-ring");
  const synapses = document.getElementById("synapses");
  const neuronsLayer = document.getElementById("neurons-layer");
  const emptyState = document.getElementById("empty-state");
  const filtersEl = document.getElementById("filters");
  const lastUpdatedEl = document.getElementById("last-updated");
  const detailPanel = document.getElementById("detail-panel");
  const detailContent = document.getElementById("detail-content");
  const detailOverlay = document.getElementById("detail-overlay");
  const detailClose = document.getElementById("detail-close");

  /* ---------------------------------------------------------------- */
  /* State                                                              */
  /* ---------------------------------------------------------------- */

  let allPapers = [];
  let visiblePapers = [];
  let activeCategory = null;
  let networkOpened = false;
  let resizeTimer = null;

  /* ---------------------------------------------------------------- */
  /* Helpers                                                            */
  /* ---------------------------------------------------------------- */

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function safeText(value) {
    if (value === null || value === undefined || String(value).trim() === "") return "N/A";
    return String(value).trim();
  }

  function escapeHTML(value) {
    const el = document.createElement("div");
    el.textContent = safeText(value);
    return el.innerHTML;
  }

  function normalizeImage(value) {
    if (!value) return "";
    const image = String(value).trim();
    if (image.startsWith("/static/browse/")) return "https://arxiv.org" + image;
    return image;
  }

  function formatDate(value) {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "N/A";
    return d.toLocaleDateString("en-GB", { day: "2-digit", month: "long", year: "numeric" });
  }

  function formatDateTime(value) {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "N/A";
    return d.toLocaleString("en-GB", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  const NOT_SPECIFIED_VALUES = new Set(["", "n/a", "dato non specificato nella fonte", "not specified in source"]);

  function hasNumber(value) {
    return !NOT_SPECIFIED_VALUES.has(String(value || "").trim().toLowerCase());
  }

  function hasContent(value) {
    return !NOT_SPECIFIED_VALUES.has(String(value || "").trim().toLowerCase());
  }

  function normalizePaper(paper) {
    return {
      ...paper,
      title: safeText(paper.title),
      source_name: safeText(paper.source_name),
      source_url: safeText(paper.source_url),
      published_at: paper.published_at,
      category: safeText(paper.category) === "N/A" ? "Other" : safeText(paper.category),
      image_url: normalizeImage(paper.image_url),
      is_preprint: !!paper.is_preprint,
      big_problem: safeText(paper.big_problem),
      small_problem: safeText(paper.small_problem),
      idea: safeText(paper.idea),
      plan: safeText(paper.plan),
      conclusion: safeText(paper.conclusion),
      future_directions: safeText(paper.future_directions),
      result_1_headline: safeText(paper.result_1_headline),
      result_1_number: safeText(paper.result_1_number),
      result_1_detail: safeText(paper.result_1_detail),
      result_2_headline: safeText(paper.result_2_headline),
      result_2_number: safeText(paper.result_2_number),
      result_2_detail: safeText(paper.result_2_detail),
      result_3_headline: safeText(paper.result_3_headline),
      result_3_number: safeText(paper.result_3_number),
      result_3_detail: safeText(paper.result_3_detail),
    };
  }

  function createAbstract(paper) {
    const parts = [paper.small_problem, paper.idea].filter((p) => p && p !== "N/A");
    return parts.join(" — ") || (paper.big_problem !== "N/A" ? paper.big_problem : "");
  }

  function papersSignature(list) {
    return list.map((p) => p.source_url).sort().join("|");
  }

  function debounce(fn, wait) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
  }

  /* ---------------------------------------------------------------- */
  /* Caricamento dati + auto-refresh                                   */
  /* ---------------------------------------------------------------- */

  async function loadNews() {
    try {
      const res = await fetch(NEWS_FILE, { cache: "no-store" });
      if (!res.ok) throw new Error("Impossibile caricare news.json");
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error("news.json deve contenere un array");

      const cutoff = Date.now() - WINDOW_HOURS * 3600 * 1000;

      allPapers = data
        .filter((p) => p.title && p.source_name && p.source_url && p.published_at)
        .filter((p) => {
          const t = new Date(p.published_at).getTime();
          return !Number.isNaN(t) && t >= cutoff;
        })
        .map(normalizePaper)
        .sort((a, b) => new Date(b.published_at) - new Date(a.published_at))
        .slice(0, MAX_PAPERS);

      visiblePapers = [...allPapers];
      renderFilters();
      renderLastUpdated(data);
      return true;
    } catch (err) {
      console.error("loadNews:", err);
      allPapers = [];
      visiblePapers = [];
      return false;
    }
  }

  function renderLastUpdated(data) {
    if (!data.length) { lastUpdatedEl.textContent = ""; return; }
    const latest = data.reduce((max, p) => (new Date(p.fetched_at || p.published_at) > new Date(max) ? (p.fetched_at || p.published_at) : max), data[0].fetched_at || data[0].published_at);
    lastUpdatedEl.textContent = "LAST UPDATED · " + formatDateTime(latest).toUpperCase();
  }

  const dataReady = loadNews();

  async function refreshIfNeeded() {
    const prevSig = papersSignature(visiblePapers);
    await loadNews();
    if (!networkOpened) return;
    if (papersSignature(visiblePapers) === prevSig) return; // nulla di nuovo, non ri-animare tutto
    if (visiblePapers.length) {
      renderNetwork();
    } else {
      neuronsLayer.innerHTML = "";
      synapses.innerHTML = "";
      emptyState.hidden = false;
    }
  }

  setInterval(refreshIfNeeded, REFRESH_MINUTES * 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshIfNeeded();
  });

  /* ---------------------------------------------------------------- */
  /* Apertura rete                                                     */
  /* ---------------------------------------------------------------- */

  openBtn.addEventListener("click", async () => {
    if (networkOpened) return;
    networkOpened = true;
    app.classList.add("opened");
    await dataReady;
    requestAnimationFrame(() => {
      if (visiblePapers.length) renderNetwork();
      else emptyState.hidden = false;
    });
  });

  window.addEventListener("resize", debounce(() => {
    if (networkOpened && visiblePapers.length) renderNetwork();
  }, 220));

  /* ---------------------------------------------------------------- */
  /* Layout a cerchio regolare                                         */
  /* ---------------------------------------------------------------- */

  function computeCirclePositions(count, stageRect, coreX, coreY, columnVisible) {
    const cardW = 150, cardH = 190, gap = 22;
    const diag = Math.hypot(cardW, cardH) + gap; // unico valore sicuro a qualsiasi angolo attorno al cerchio
    const marginLeft = columnVisible ? 220 : 20;
    const marginRight = 20, marginTop = 26, marginBottom = 36;
    const usableLeft = marginLeft, usableRight = stageRect.width - marginRight;

    const spaceRadiusHoriz = Math.max(140, Math.min(coreX - usableLeft, usableRight - coreX));
    const minR = count > 1 ? (diag / 2) / Math.sin(Math.PI / count) : 0;
    // Il raggio deve garantire zero sovrapposizioni (minR) — se lo spazio
    // orizzontale disponibile è più stretto lo rispettiamo comunque quando
    // possibile, ma MAI a costo di far accavallare le card.
    const cardRadius = Math.max(minR, Math.min(spaceRadiusHoriz, 340));

    const positions = [];
    for (let i = 0; i < count; i++) {
      const angle = -Math.PI / 2 + i * ((2 * Math.PI) / count);
      positions.push({
        x: coreX + cardRadius * Math.cos(angle),
        y: coreY + cardRadius * Math.sin(angle),
        dotX: coreX + Math.min(cardRadius * 0.5, 118) * Math.cos(angle),
        dotY: coreY + Math.min(cardRadius * 0.5, 118) * Math.sin(angle),
      });
    }

    // Se il cerchio calcolato non entra in alto (raggio maggiore dello spazio
    // sopra il centro), sposta l'INTERO layout (non solo le card) più in
    // basso di quel tanto, invece di tagliare o far accavallare nulla.
    const minTop = Math.min(...positions.map((p) => p.y - cardH / 2));
    const shiftDown = minTop < marginTop ? marginTop - minTop : 0;
    if (shiftDown > 0) {
      positions.forEach((p) => { p.y += shiftDown; p.dotY += shiftDown; });
    }

    return { positions, dotRadius: Math.min(cardRadius * 0.5, 118), cardRadius, shiftDown };
  }

  /* ---------------------------------------------------------------- */
  /* Render rete                                                        */
  /* ---------------------------------------------------------------- */

  function renderNetwork() {
    emptyState.hidden = true;
    neuronsLayer.innerHTML = "";
    synapses.innerHTML = "";
    neuronsLayer.classList.remove("hovering");

    const isMobile = window.innerWidth <= 640;
    neuralCore.style.transform = "";
    connectorRing.style.transform = "";
    const stageRect = stage.getBoundingClientRect();
    const coreRect = neuralCore.getBoundingClientRect();
    const coreX = coreRect.left + coreRect.width / 2 - stageRect.left;
    const coreY = coreRect.top + coreRect.height / 2 - stageRect.top;

    if (isMobile) {
      connectorRing.style.display = "none";
      visiblePapers.forEach((paper, i) => createPaperCard(paper, null, i));
      applyFilter(activeCategory);
      return;
    }

    const columnVisible = window.innerWidth > 1080;
    const { positions, dotRadius, shiftDown } = computeCirclePositions(
      visiblePapers.length, stageRect, coreX, coreY, columnVisible
    );

    // Se il layout è stato spostato in basso per non tagliare le card in
    // alto, sposta insieme anche il cervello e l'anello, per restare centrati.
    neuralCore.style.transform = shiftDown
      ? `translate(-50%, calc(-50% + ${shiftDown}px))`
      : "";
    connectorRing.style.transform = shiftDown
      ? `translate(-50%, calc(-50% + ${shiftDown}px))`
      : "";

    connectorRing.style.display = "";
    connectorRing.style.width = `${dotRadius * 2}px`;
    connectorRing.style.height = `${dotRadius * 2}px`;

    const maxBottom = positions.reduce((m, p) => Math.max(m, p.y + 110), 0);
    stage.style.minHeight = maxBottom + 40 > stageRect.height ? `${maxBottom + 40}px` : "";

    positions.forEach((pos, i) => {
      drawSynapse(pos, i);
      createPaperCard(visiblePapers[i], pos, i, coreX, coreY);
    });
    applyFilter(activeCategory);
  }

  function drawSynapse(pos, index) {
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", pos.dotX); dot.setAttribute("cy", pos.dotY); dot.setAttribute("r", 3.5);
    dot.setAttribute("class", "ring-dot live");
    dot.dataset.index = String(index);
    if (!REDUCED_MOTION) dot.style.transitionDelay = `${Math.min(index * 70, 800)}ms`;
    synapses.appendChild(dot);

    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", pos.dotX); line.setAttribute("y1", pos.dotY);
    line.setAttribute("x2", pos.x); line.setAttribute("y2", pos.y);
    line.setAttribute("class", "synapse-line live");
    line.dataset.index = String(index);
    if (!REDUCED_MOTION) line.style.transitionDelay = `${Math.min(index * 70, 800)}ms`;
    synapses.appendChild(line);
  }

  /* ---------------------------------------------------------------- */
  /* Card — dimensione ridotta, si ingrandisce al passaggio del mouse   */
  /* ---------------------------------------------------------------- */

  function createPaperCard(paper, pos, index, coreX, coreY) {
    const delay = Math.min(index * 0.07, 0.8);
    const travel = 0.5 + Math.random() * 0.2;

    if (pos && !REDUCED_MOTION) {
      const spark = document.createElement("span");
      spark.className = "spark";
      spark.style.left = `${pos.x}px`;
      spark.style.top = `${pos.y}px`;
      spark.style.setProperty("--dx", `${coreX - pos.x}px`);
      spark.style.setProperty("--dy", `${coreY - pos.y}px`);
      spark.style.setProperty("--delay", `${delay}s`);
      spark.style.setProperty("--travel", `${travel}s`);
      neuronsLayer.appendChild(spark);
    }

    const article = document.createElement("article");
    article.className = "neuron";
    article.dataset.category = paper.category;
    article.dataset.index = String(index);
    if (pos) {
      article.style.left = `${pos.x}px`;
      article.style.top = `${pos.y}px`;
    }
    article.style.setProperty("--delay", `${pos ? delay + travel - 0.08 : index * 0.06}s`);
    if (REDUCED_MOTION) {
      article.style.animation = "none";
      article.style.opacity = "1";
      article.style.transform = pos ? "translate(-50%, -50%) scale(1)" : "none";
    }

    article.tabIndex = 0;
    article.setAttribute("role", "button");
    article.setAttribute("aria-label", paper.title);

    const imageHTML = paper.image_url
      ? `<img class="paper-image" src="${escapeHTML(paper.image_url)}" alt="" loading="lazy">`
      : `<div class="paper-image paper-image-na"><span class="mark-main">Be in<br>the loop</span></div>`;

    article.innerHTML = `
      <div class="paper-header">
        <span class="paper-number">RESEARCH ARTICLE</span>
        <span>${String(index + 1).padStart(2, "0")}${paper.is_preprint ? " · PREPRINT" : ""}</span>
      </div>
      ${imageHTML}
      <div class="paper-body">
        <div class="paper-category">${escapeHTML(paper.category)}</div>
        <h2 class="paper-title">${escapeHTML(paper.title)}</h2>
        <p class="paper-abstract">${escapeHTML(createAbstract(paper))}</p>
        <div class="paper-meta">
          <span>${escapeHTML(formatDate(paper.published_at))}</span>
          <span>${escapeHTML(paper.source_name)}</span>
        </div>
      </div>
    `;

    const open = () => openDetail(paper, article);
    article.addEventListener("click", open);
    article.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
    // ingrandisce la card sotto il mouse e affievolisce le altre, per una lettura chiara
    article.addEventListener("mouseenter", () => neuronsLayer.classList.add("hovering"));
    article.addEventListener("mouseleave", () => neuronsLayer.classList.remove("hovering"));

    neuronsLayer.appendChild(article);
  }

  /* ---------------------------------------------------------------- */
  /* Filtri                                                             */
  /* ---------------------------------------------------------------- */

  function renderFilters() {
    filtersEl.innerHTML = "";
    const categories = [...new Set(allPapers.map((p) => p.category))];
    if (categories.length < 2) { activeCategory = null; return; }

    filtersEl.appendChild(makeChip("ALL", null));
    categories.forEach((cat) => filtersEl.appendChild(makeChip(cat.toUpperCase(), cat)));
    applyFilter(activeCategory);
  }

  function makeChip(label, category) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = label;
    if (category) chip.dataset.category = category;
    chip.addEventListener("click", () => applyFilter(category));
    return chip;
  }

  function applyFilter(category) {
    activeCategory = category;
    [...filtersEl.children].forEach((c) => c.classList.toggle("active", (c.dataset.category || null) === category));
    [...neuronsLayer.querySelectorAll(".neuron")].forEach((el) => {
      const match = !category || el.dataset.category === category;
      el.classList.toggle("dimmed", !match);
    });
    [...synapses.querySelectorAll(".synapse-line, .ring-dot")].forEach((el) => {
      const p = visiblePapers[Number(el.dataset.index)];
      const match = !p || !category || p.category === category;
      el.style.opacity = match ? "" : "0.06";
    });
  }

  /* ---------------------------------------------------------------- */
  /* Dettaglio                                                          */
  /* ---------------------------------------------------------------- */

  function openDetail(paper, cardEl) {
    document.querySelectorAll(".neuron").forEach((el) => el.classList.remove("selected"));
    if (cardEl) cardEl.classList.add("selected");

    detailContent.setAttribute("data-category", paper.category);
    detailContent.innerHTML = createDetailHTML(paper);
    detailPanel.classList.add("visible");
    detailOverlay.classList.add("visible");
    detailPanel.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    detailClose.focus();
  }

  function closeDetail() {
    detailPanel.classList.remove("visible");
    detailOverlay.classList.remove("visible");
    detailPanel.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
    document.querySelectorAll(".neuron").forEach((el) => el.classList.remove("selected"));
  }

  detailClose.addEventListener("click", closeDetail);
  detailOverlay.addEventListener("click", closeDetail);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && detailPanel.classList.contains("visible")) closeDetail();
  });

  function createResultCard(headline, number, detail) {
    if (!hasContent(headline) || !hasContent(detail)) return "";
    const numberHTML = hasNumber(number) ? `<span class="d-result-number">${escapeHTML(number)}</span>` : "";
    return `
      <div class="d-result-card">
        <div class="d-result-headline">${escapeHTML(headline)}</div>
        ${numberHTML}
        <div class="d-result-detail">${escapeHTML(detail)}</div>
      </div>`;
  }

  function joinSentences(parts) {
    return parts
      .filter((p) => p && p !== "N/A")
      .map((p) => (/[.!?]$/.test(p.trim()) ? p.trim() : p.trim() + "."))
      .join(" ");
  }

  function createDetailHTML(p) {
    const imageHTML = p.image_url
      ? `<img class="d-image" src="${escapeHTML(p.image_url)}" alt="">`
      : `<div class="d-image paper-image-na"><span class="mark-main">Be in the loop</span></div>`;
    const preprintBadge = p.is_preprint ? `<span class="d-badge" style="background:var(--ink)">PREPRINT</span>` : "";
    const introText = joinSentences([p.small_problem, p.idea, p.plan]);
    const closingText = joinSentences([p.conclusion, p.future_directions]);

    return `
      ${imageHTML}
      <div class="d-meta">
        <span>${escapeHTML(p.source_name)}</span>
        <span class="d-badge">${escapeHTML(p.category)}</span>
        <span>${escapeHTML(formatDate(p.published_at))}</span>
        ${preprintBadge}
      </div>

      <p class="d-bluf">${escapeHTML(p.big_problem)}</p>

      <p class="d-intro-text">${escapeHTML(introText)}</p>

      <div class="d-results">
        ${createResultCard(p.result_1_headline, p.result_1_number, p.result_1_detail)}
        ${createResultCard(p.result_2_headline, p.result_2_number, p.result_2_detail)}
        ${createResultCard(p.result_3_headline, p.result_3_number, p.result_3_detail)}
      </div>

      <p class="d-conclusion">${escapeHTML(closingText)}</p>

      <div class="d-actions">
        <a class="d-source-link" href="${escapeHTML(p.source_url)}" target="_blank" rel="noopener noreferrer">
          READ THE ORIGINAL SOURCE ↗
        </a>
      </div>
      <p class="d-attribution">
        Content automatically processed from the source indicated.
        Original text and rights belong to ${escapeHTML(p.source_name)}.
      </p>
    `;
  }

})();
