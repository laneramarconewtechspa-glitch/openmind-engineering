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
  const brainScene = document.getElementById("brain-scene");
  const brain3d = document.getElementById("brain-3d");
  const specular = document.getElementById("brain-specular");
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
    return d.toLocaleDateString("it-IT", { day: "2-digit", month: "long", year: "numeric" });
  }

  function formatDateTime(value) {
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "N/A";
    return d.toLocaleString("it-IT", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function normalizePaper(paper) {
    return {
      ...paper,
      title: safeText(paper.title),
      source_name: safeText(paper.source_name),
      source_url: safeText(paper.source_url),
      published_at: paper.published_at,
      category: safeText(paper.category) === "N/A" ? "Altro" : safeText(paper.category),
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
    lastUpdatedEl.textContent = "ULTIMO AGGIORNAMENTO · " + formatDateTime(latest).toUpperCase();
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
  /* Tilt 3D del cervello (idle drift + parallasse al mouse)           */
  /* ---------------------------------------------------------------- */

  function initBrainTilt() {
    if (REDUCED_MOTION || !brain3d) return;

    const BASE_RY = -34; // presenta il cervello di 3/4 lateralmente invece che frontalmente
    let targetRX = 0, targetRY = 0, curRX = 0, curRY = 0;

    window.addEventListener("mousemove", (e) => {
      const dx = (e.clientX - window.innerWidth / 2) / (window.innerWidth / 2);
      const dy = (e.clientY - window.innerHeight / 2) / (window.innerHeight / 2);
      targetRY = BASE_RY + clamp(dx, -1, 1) * 10;
      targetRX = clamp(-dy, -1, 1) * 8;
    });
    targetRY = BASE_RY;

    function tick(t) {
      curRX += (targetRX - curRX) * 0.055;
      curRY += (targetRY - curRY) * 0.055;
      const idle = Math.sin(t / 2600) * 3;
      brain3d.style.transform = `rotateX(${curRX.toFixed(2)}deg) rotateY(${(curRY + idle).toFixed(2)}deg)`;
      if (specular) {
        specular.style.setProperty("--sx", (-curRY * 1.6).toFixed(1));
        specular.style.setProperty("--sy", (curRX * 1.6).toFixed(1));
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }
  initBrainTilt();

  /* ---------------------------------------------------------------- */
  /* Layout a spirale anti-sovrapposizione                             */
  /* ---------------------------------------------------------------- */

  function computePositions(count, stageRect, coreX, coreY, brainHalfW, brainHalfH) {
    const cardW = 192, cardH = 258, gap = 20;
    const columnVisible = stageRect.width > 1080;
    const marginLeft = columnVisible ? 236 : 20;
    const marginRight = 18, marginTop = 18, marginBottom = 24;
    const coreHalfW = (brainHalfW || 130) + 24; // area (ellittica) da lasciare libera attorno al cervello, proporzionata alla sua dimensione reale a schermo
    const coreHalfH = (brainHalfH || 130) + 40;

    const usableLeft = marginLeft, usableRight = stageRect.width - marginRight;
    const usableTop = marginTop, usableBottom = stageRect.height - marginBottom;

    const overlapsRect = (r1, r2, g) =>
      !(r1.right + g < r2.left || r1.left - g > r2.right || r1.bottom + g < r2.top || r1.top - g > r2.bottom);

    const insideCoreEllipse = (x, y) => {
      const ex = coreHalfW + cardW / 2 + gap * 0.6;
      const ey = coreHalfH + cardH / 2 + gap * 0.6;
      const dx = (x - coreX) / ex, dy = (y - coreY) / ey;
      return dx * dx + dy * dy < 1;
    };

    // Due griglie di celle intrecciate (la seconda sfalsata di mezzo passo) per avere
    // più candidati liberi tra cui scegliere: a differenza di una spirale a raggio
    // crescente, questo copre SEMPRE l'intera area utilizzabile, senza lasciare buchi.
    const stepX = cardW + gap, stepY = cardH + gap;
    const candidates = [];
    for (let pass = 0; pass < 2; pass++) {
      const offX = pass === 1 ? stepX / 2 : 0;
      const offY = pass === 1 ? stepY / 2 : 0;
      for (let y = usableTop + cardH / 2 + offY; y <= usableBottom - cardH / 2; y += stepY) {
        for (let x = usableLeft + cardW / 2 + offX; x <= usableRight - cardW / 2; x += stepX) {
          if (!insideCoreEllipse(x, y)) candidates.push({ x, y });
        }
      }
    }

    // Ordina i candidati per distanza dal core con un pizzico di casualità, per un
    // effetto organico invece che a griglia rigida.
    candidates.forEach((c) => {
      c.d = Math.hypot(c.x - coreX, (c.y - coreY) / 0.9) + (Math.random() - 0.5) * stepX * 0.5;
    });
    candidates.sort((a, b) => a.d - b.d);

    const placed = [];
    for (const c of candidates) {
      if (placed.length >= count) break;
      const x = c.x + (Math.random() - 0.5) * gap * 0.7;
      const y = c.y + (Math.random() - 0.5) * gap * 0.7;
      const rect = { left: x - cardW / 2, right: x + cardW / 2, top: y - cardH / 2, bottom: y + cardH / 2 };
      if (placed.some((p) => overlapsRect(rect, p.rect, gap * 0.5))) continue;
      placed.push({ x, y, rect });
    }

    // Fallback per casi estremi (schermi molto piccoli o molti articoli insieme):
    // se le due griglie non bastano, cerca posizioni aggiuntive scendendo con un
    // passo fine, controllando comunque le sovrapposizioni (mai a costo zero).
    let fx = usableLeft + cardW / 2, fy = usableBottom - cardH / 2;
    let guard = 0;
    while (placed.length < count && guard < 4000) {
      guard++;
      const rect = { left: fx - cardW / 2, right: fx + cardW / 2, top: fy - cardH / 2, bottom: fy + cardH / 2 };
      if (!placed.some((p) => overlapsRect(rect, p.rect, 6))) {
        placed.push({ x: fx, y: fy, rect });
      }
      fx += 34;
      if (fx > usableRight - cardW / 2) { fx = usableLeft + cardW / 2; fy += 34; }
    }

    return placed.map((p) => ({ x: p.x, y: p.y }));
  }

  /* ---------------------------------------------------------------- */
  /* Render rete                                                        */
  /* ---------------------------------------------------------------- */

  function renderNetwork() {
    emptyState.hidden = true;
    neuronsLayer.innerHTML = "";
    synapses.innerHTML = "";

    const isMobile = window.innerWidth <= 640;
    const stageRect = stage.getBoundingClientRect();
    const coreRect = neuralCore.getBoundingClientRect();
    const brainRect = brainScene.getBoundingClientRect();
    const coreX = coreRect.left + coreRect.width / 2 - stageRect.left;
    const coreY = coreRect.top + coreRect.height / 2 - stageRect.top;

    if (isMobile) {
      visiblePapers.forEach((paper, i) => createPaperCard(paper, null, i, coreX, coreY));
      applyFilter(activeCategory);
      return;
    }

    const positions = computePositions(visiblePapers.length, stageRect, coreX, coreY, brainRect.width / 2, brainRect.height / 2);

    const maxBottom = positions.reduce((m, p) => Math.max(m, p.y + 258 / 2), 0);
    stage.style.minHeight = maxBottom + 40 > stageRect.height
      ? `${maxBottom + 40}px`
      : "";

    positions.forEach((pos, i) => {
      drawSynapse(coreX, coreY, pos.x, pos.y, i);
      createPaperCard(visiblePapers[i], pos, i, coreX, coreY);
    });
    applyFilter(activeCategory);
  }

  function drawSynapse(ox, oy, x, y, index) {
    const bend = index % 2 === 0 ? 1 : -1;
    const mx = (ox + x) / 2 + (y - oy) * 0.1 * bend;
    const my = (oy + y) / 2 + (ox - x) * 0.1 * bend;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M ${ox} ${oy} Q ${mx} ${my} ${x} ${y}`);
    path.setAttribute("class", "synapse-line live");
    path.dataset.index = String(index);
    if (!REDUCED_MOTION) path.style.transitionDelay = `${Math.min(index * 70, 700)}ms`;
    synapses.appendChild(path);
  }

  /* ---------------------------------------------------------------- */
  /* Card (con spark che la precede sulla vista desktop)               */
  /* ---------------------------------------------------------------- */

  function createPaperCard(paper, pos, index, coreX, coreY) {
    const delay = Math.min(index * 0.09, 1.1);
    const travel = 0.55 + Math.random() * 0.25;

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
    article.style.setProperty("--delay", `${pos ? delay : index * 0.06}s`);
    article.style.setProperty("--travel", `${pos ? travel : 0}s`);
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
      : `<div class="paper-image paper-image-na">N/A</div>`;

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

    neuronsLayer.appendChild(article);
  }

  /* ---------------------------------------------------------------- */
  /* Filtri                                                             */
  /* ---------------------------------------------------------------- */

  function renderFilters() {
    filtersEl.innerHTML = "";
    const categories = [...new Set(allPapers.map((p) => p.category))];
    if (categories.length < 2) { activeCategory = null; return; }

    filtersEl.appendChild(makeChip("TUTTE", null));
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
    [...synapses.querySelectorAll(".synapse-line")].forEach((line) => {
      const p = visiblePapers[Number(line.dataset.index)];
      const match = !p || !category || p.category === category;
      line.style.opacity = match ? "" : "0.05";
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
    return `
      <div class="d-result-card">
        <div class="d-result-headline">${escapeHTML(headline)}</div>
        <span class="d-result-number">${escapeHTML(number)}</span>
        <div class="d-result-detail">${escapeHTML(detail)}</div>
      </div>`;
  }

  function createDetailHTML(p) {
    const imageHTML = p.image_url
      ? `<img class="d-image" src="${escapeHTML(p.image_url)}" alt="">`
      : `<div class="d-image paper-image-na">N/A</div>`;
    const preprintBadge = p.is_preprint ? `<span class="d-badge" style="background:var(--ink)">PREPRINT</span>` : "";

    return `
      ${imageHTML}
      <div class="d-meta">
        <span>${escapeHTML(p.source_name)}</span>
        <span class="d-badge">${escapeHTML(p.category)}</span>
        <span>${escapeHTML(formatDate(p.published_at))}</span>
        ${preprintBadge}
      </div>

      <p class="d-bluf-label">BLUF</p>
      <p class="d-bluf">${escapeHTML(p.big_problem)}</p>

      <div class="d-intro">
        <p class="d-section-label">INTRO</p>
        <div class="d-intro-row"><strong>PROBLEMA</strong><span>${escapeHTML(p.small_problem)}</span></div>
        <div class="d-intro-row"><strong>IDEA</strong><span>${escapeHTML(p.idea)}</span></div>
        <div class="d-intro-row"><strong>PIANO</strong><span>${escapeHTML(p.plan)}</span></div>
      </div>

      <p class="d-section-label">RISULTATI</p>
      <div class="d-results">
        ${createResultCard(p.result_1_headline, p.result_1_number, p.result_1_detail)}
        ${createResultCard(p.result_2_headline, p.result_2_number, p.result_2_detail)}
        ${createResultCard(p.result_3_headline, p.result_3_number, p.result_3_detail)}
      </div>

      <p class="d-section-label">CONCLUSIONE</p>
      <p class="d-conclusion">${escapeHTML(p.conclusion)}</p>

      <p class="d-section-label">DIREZIONI FUTURE</p>
      <p class="d-future">${escapeHTML(p.future_directions)}</p>

      <div class="d-actions">
        <a class="d-source-link" href="${escapeHTML(p.source_url)}" target="_blank" rel="noopener noreferrer">
          LEGGI LA FONTE ORIGINALE ↗
        </a>
      </div>
      <p class="d-attribution">
        Contenuto elaborato automaticamente a partire dalla fonte indicata.
        Testo e diritti originali appartengono alla fonte: ${escapeHTML(p.source_name)}.
      </p>
    `;
  }

})();
