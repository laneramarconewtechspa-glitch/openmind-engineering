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
  // Il backend (fetch_news.py, RETAIN_WINDOW_HOURS) tiene le notizie per 48h
  // anche se il sito ne mostra solo le ultime 24: la fascia 24-48h è
  // esattamente l'archivio "di ieri" mostrato dal toggle Archive qui sotto.
  const ARCHIVE_WINDOW_HOURS = 48;
  // Quante notizie mostra ogni vista (oggi / ieri): tenere allineato a
  // PUBLISH_TOP_N in fetch_news.py. Il backend ne conserva di più (riserva di
  // "fresche") e qui si mostrano le migliori per punteggio ancora nella finestra.
  const MAX_PAPERS = 10;
  const REFRESH_MINUTES = 15; // ricontrolla news.json periodicamente a pagina aperta
  const GOLDEN_ANGLE = 2.399963;
  const REDUCED_MOTION = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const FLASH_FILE = "flash.json";
  const FLASH_SPEED_PX_PER_SEC = 55; // velocità di scorrimento del ticker, costante a prescindere da quante flash ci sono

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
  const emptyStateText = emptyState ? emptyState.querySelector("p") : null;
  const filtersEl = document.getElementById("filters");
  const lastUpdatedEl = document.getElementById("last-updated");
  const detailPanel = document.getElementById("detail-panel");
  const detailContent = document.getElementById("detail-content");
  const detailOverlay = document.getElementById("detail-overlay");
  const detailClose = document.getElementById("detail-close");

  const flashBar = document.getElementById("flash-bar");
  const flashTrack = document.getElementById("flash-bar-track");
  const flashTrackWrap = flashBar ? flashBar.querySelector(".flash-bar-track-wrap") : null;
  const flashContentA = document.getElementById("flash-bar-content-a");
  const flashContentB = document.getElementById("flash-bar-content-b");

  const archiveToggle = document.getElementById("archive-toggle");
  const archiveStatus = document.getElementById("archive-status");

  /* ---------------------------------------------------------------- */
  /* State                                                              */
  /* ---------------------------------------------------------------- */

  let allPapers = [];      // notizie di oggi (ultime WINDOW_HOURS)
  let archivePapers = [];  // notizie di ieri (tra WINDOW_HOURS e ARCHIVE_WINDOW_HOURS fa)
  let archiveMode = false; // false = si vede "oggi", true = si vede "ieri"
  let visiblePapers = [];
  let poolPapers = [];     // tutto news.json normalizzato (oggi + ieri + riserva): base delle statistiche
  let flashItems = [];     // Flash News correnti: idem
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

  function createScoreBadge(score, size) {
    if (score === undefined || score === null || Number.isNaN(Number(score))) return "";
    const s = Math.max(0, Math.min(100, Math.round(Number(score))));
    const r = size / 2 - 3;
    const c = 2 * Math.PI * r;
    const offset = c * (1 - s / 100);
    const fontSize = size <= 30 ? 9 : 13;
    return `
      <svg class="score-badge" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="BITL Score ${s} out of 100">
        <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="#ffffff" stroke="#eae9e5" stroke-width="2.5"/>
        <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#d71920" stroke-width="2.5"
          stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"
          transform="rotate(-90 ${size / 2} ${size / 2})"/>
        <text x="50%" y="52%" text-anchor="middle" dominant-baseline="middle"
          font-family="IBM Plex Mono, monospace" font-weight="600" font-size="${fontSize}" fill="#d71920">${s}</text>
      </svg>`;
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

  function currentPool() {
    return archiveMode ? archivePapers : allPapers;
  }

  async function loadNews() {
    try {
      const res = await fetch(NEWS_FILE, { cache: "no-store" });
      if (!res.ok) throw new Error("Impossibile caricare news.json");
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error("news.json deve contenere un array");

      const now = Date.now();
      const todayCutoff = now - WINDOW_HOURS * 3600 * 1000;
      const archiveCutoff = now - ARCHIVE_WINDOW_HOURS * 3600 * 1000;

      const valid = data
        .filter((p) => p.title && p.source_name && p.source_url && p.published_at)
        .map(normalizePaper);
      const byScoreThenDate = (a, b) => (b.score ?? -1) - (a.score ?? -1) || new Date(b.published_at) - new Date(a.published_at);

      poolPapers = valid;

      allPapers = valid
        .filter((p) => { const t = new Date(p.published_at).getTime(); return !Number.isNaN(t) && t >= todayCutoff; })
        .sort(byScoreThenDate)
        .slice(0, MAX_PAPERS);

      archivePapers = valid
        .filter((p) => { const t = new Date(p.published_at).getTime(); return !Number.isNaN(t) && t < todayCutoff && t >= archiveCutoff; })
        .sort(byScoreThenDate)
        .slice(0, MAX_PAPERS);

      visiblePapers = [...currentPool()];
      renderFilters();
      renderLastUpdated(data);
      scheduleStatsRefresh();
      return true;
    } catch (err) {
      console.error("loadNews:", err);
      allPapers = [];
      archivePapers = [];
      poolPapers = [];
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
    if (document.visibilityState === "visible") { refreshIfNeeded(); loadFlash(); }
  });

  /* ---------------------------------------------------------------- */
  /* Flash bar — "Breaking Loop" ticker                                */
  /* ---------------------------------------------------------------- */

  function normalizeFlash(item) {
    return {
      title: safeText(item.title),
      summary: safeText(item.summary),
      category: safeText(item.category) === "N/A" ? "Other Engineering" : safeText(item.category),
      source_name: safeText(item.source_name),
      source_url: safeText(item.source_url),
      published_at: item.published_at,
    };
  }

  function createFlashItemHTML(item) {
    return `
      <a class="flash-item" href="${escapeHTML(item.source_url)}" target="_blank" rel="noopener noreferrer">
        <span class="flash-item-cat">${escapeHTML(item.category.toUpperCase())}</span>
        <span class="flash-item-title">${escapeHTML(item.title)}</span>
        <span class="flash-item-summary">— ${escapeHTML(item.summary)}</span>
      </a>`;
  }

  async function loadFlash() {
    if (!flashBar) return; // markup non presente: nessuna funzionalità da rompere
    try {
      const res = await fetch(FLASH_FILE, { cache: "no-store" });
      if (!res.ok) throw new Error("flash.json non disponibile");
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error("flash.json deve contenere un array");

      const items = data
        .filter((f) => f.title && f.summary && f.category && f.source_url)
        .map(normalizeFlash)
        .sort((a, b) => new Date(b.published_at) - new Date(a.published_at));

      flashItems = items;
      renderFlashBar(items);
      scheduleStatsRefresh();
    } catch (err) {
      // A differenza di loadNews, il file flash.json è per design opzionale
      // (può mancare, essere vuoto, o non ancora esistere su un repo appena
      // creato): nessun console.error qui, la barra si nasconde e basta.
      flashItems = [];
      renderFlashBar([]);
      scheduleStatsRefresh();
    }
  }

  function renderFlashBar(items) {
    if (!items.length) {
      flashBar.hidden = true;
      app.classList.remove("has-flash-bar");
      return;
    }

    const html = items.map(createFlashItemHTML).join("");
    flashContentA.innerHTML = html;

    // Sveliamo la barra PRIMA di misurare scrollWidth più sotto: un elemento
    // hidden (display:none) misura sempre larghezza 0, il che falserebbe il
    // calcolo della durata dell'animazione.
    flashBar.hidden = false;
    app.classList.add("has-flash-bar");

    if (REDUCED_MOTION) {
      // Niente scorrimento automatico: contenuto singolo, scorribile a mano.
      flashContentB.innerHTML = "";
      flashContentB.hidden = true;
      flashTrack.classList.add("no-scroll");
      if (flashTrackWrap) flashTrackWrap.classList.add("manual-scroll");
    } else {
      flashContentB.innerHTML = html;
      flashContentB.hidden = false;
      flashTrack.classList.remove("no-scroll");
      if (flashTrackWrap) flashTrackWrap.classList.remove("manual-scroll");
      // Durata proporzionale alla larghezza reale del contenuto, per una
      // velocità di scorrimento costante indipendentemente da quante flash
      // news ci sono in un dato momento. Leggere scrollWidth forza da solo
      // il reflow necessario: niente requestAnimationFrame (può restare in
      // sospeso a lungo su un tab in background o non ancora composito).
      const width = flashContentA.scrollWidth;
      const duration = Math.max(20, width / FLASH_SPEED_PX_PER_SEC);
      flashTrack.style.setProperty("--flash-duration", `${duration}s`);
    }
  }

  const flashReady = loadFlash();
  setInterval(loadFlash, REFRESH_MINUTES * 60 * 1000);

  /* ---------------------------------------------------------------- */
  /* Statistiche (footer espandibile, GSAP) + pannello "BITL Score"     */
  /* ---------------------------------------------------------------- */

  // Al posto del vecchio footer decorativo: una barra compatta con 3
  // indicatori (articoli analizzati / fonti / categorie) e il criterio BITL
  // Score. Arrivando in fondo alla pagina la barra si espande — timeline GSAP
  // ScrollTrigger legata allo scroll — nel dettaglio completo delle statistiche.
  // Senza GSAP (script non caricato) o con prefers-reduced-motion mostra
  // direttamente lo stato finale, senza animazioni.

  const STATS_FILE = "stats.json";
  const EMPTY_STATS = { articles_analyzed: 0, sources: [], categories: [], funnel: null };
  let statsData = { ...EMPTY_STATS };

  const statsSection = document.getElementById("stats-section");
  const statsPanel = document.getElementById("stats-panel");
  const statsBg = document.getElementById("stats-bg");
  const statsBar = document.getElementById("stats-bar");
  const statsDetail = document.getElementById("stats-detail");
  const statsCards = statsDetail ? [...statsDetail.querySelectorAll(".stats-card")] : [];
  const bitlInfoBtn = document.getElementById("bitl-info-btn");
  const statEls = {
    articles: document.getElementById("stat-articles"),
    sources: document.getElementById("stat-sources"),
    categories: document.getElementById("stat-categories"),
  };

  let statsInitialized = false;
  const statsBarsPlayed = new Set(); // indici delle card le cui barre hanno già fatto l'animazione di riempimento
  let statsOdometersPlayed = false;
  let statsRefreshTimer = null;
  let scrollTriggerRefreshTimer = null;

  const hasGsap = () => typeof window.gsap !== "undefined" && typeof window.ScrollTrigger !== "undefined";
  const formatInt = (n) => Math.max(0, Math.round(Number(n) || 0)).toLocaleString("en-US");
  const pct = (part, whole) => (whole > 0 ? Math.round((part / whole) * 100) : 0);

  function normalizeFunnel(f) {
    if (!f || typeof f !== "object") return null;
    const n = (v) => Math.max(0, Math.round(Number(v) || 0));
    return { analyzed: n(f.analyzed), relevant: n(f.relevant), substantive: n(f.substantive) };
  }

  async function loadStats() {
    try {
      const res = await fetch(STATS_FILE, { cache: "no-store" });
      if (!res.ok) throw new Error("stats.json non disponibile");
      const data = await res.json();
      statsData = {
        articles_analyzed: Number(data.articles_analyzed) || 0,
        sources: Array.isArray(data.sources) ? data.sources : [],
        categories: Array.isArray(data.categories) ? data.categories : [],
        funnel: normalizeFunnel(data.funnel),
      };
    } catch (err) {
      // Opzionale (es. repo appena creato, prima ancora del primo sync):
      // gli indicatori restano a 0, nessun errore visibile.
      statsData = { ...EMPTY_STATS };
    }
    scheduleStatsRefresh();
  }

  const statsReady = loadStats();
  setInterval(loadStats, REFRESH_MINUTES * 60 * 1000);

  /* ---- Odometro: ogni cifra è un rullo 0-9 che scorre come in una slot ---- */

  const odometers = new Map(); // elemento .stat-value -> [{ track, finalPct, idx }]

  function buildOdometer(el, value, animated) {
    if (!el) return;
    const text = formatInt(value);
    el.setAttribute("aria-label", text);
    el.textContent = "";
    odometers.delete(el);
    if (!animated) { el.textContent = text; return; }

    const wrap = document.createElement("span");
    wrap.className = "odo";
    wrap.setAttribute("aria-hidden", "true");
    const tracks = [];
    [...text].forEach((ch) => {
      if (!/\d/.test(ch)) {
        const sep = document.createElement("span");
        sep.className = "odo-static";
        sep.textContent = ch;
        wrap.appendChild(sep);
        return;
      }
      const idx = tracks.length;
      const turns = 1 + idx; // il rullo più a sinistra gira meno e si ferma per primo, come in una slot vera
      const col = document.createElement("span");
      col.className = "odo-col";
      const track = document.createElement("span");
      track.className = "odo-track";
      for (let r = 0; r <= turns; r++) {
        for (let d = 0; d < 10; d++) {
          const digit = document.createElement("span");
          digit.textContent = String(d);
          track.appendChild(digit);
        }
      }
      col.appendChild(track);
      wrap.appendChild(col);
      const total = turns * 10 + Number(ch);
      tracks.push({ track, idx, finalPct: -(total / ((turns + 1) * 10)) * 100 });
    });
    el.appendChild(wrap);
    window.gsap.set(tracks.map((t) => t.track), { yPercent: 0 });
    odometers.set(el, tracks);
  }

  function statValues() {
    return [
      [statEls.articles, statsData.articles_analyzed],
      [statEls.sources, statsData.sources.length],
      [statEls.categories, statsData.categories.length],
    ];
  }

  function buildAllOdometers(animated) {
    statValues().forEach(([el, value]) => buildOdometer(el, value, animated));
  }

  function playOdometers() {
    statsOdometersPlayed = true;
    odometers.forEach((tracks) => {
      tracks.forEach(({ track, finalPct, idx }) => {
        window.gsap.to(track, { yPercent: finalPct, duration: 1.5 + idx * 0.28, delay: idx * 0.05, ease: "power4.out" });
      });
    });
  }

  /* ---- Contenuto del dettaglio (calcolato sui dati veri già caricati) ---- */

  const BITL_WEIGHTS = [
    ["Evidence", 25], ["Applicability", 20], ["Impact", 15],
    ["Cross-domain", 15], ["Feasibility", 15], ["Maturity", 5], ["Momentum", 5],
  ];

  // Tutto ciò che è attualmente pubblicato: notizie complete (oggi + ieri +
  // riserva) e Flash News.
  const publishedPool = () => [...poolPapers, ...flashItems];

  function countBy(list, key) {
    const m = new Map();
    list.forEach((x) => m.set(x[key], (m.get(x[key]) || 0) + 1));
    return m;
  }

  function statsRow(label, valueText, widthPct, isZero) {
    return `<li class="stats-row${isZero ? " is-zero" : ""}">
      <span class="stats-row-label">${escapeHTML(label)}</span>
      <span class="stats-row-value">${escapeHTML(valueText)}</span>
      <span class="stats-track"><span class="stats-fill" data-w="${Math.max(0, Math.min(100, widthPct))}"></span></span>
    </li>`;
  }

  function funnelCardHTML() {
    const f = statsData.funnel;
    const has = !!f && f.analyzed > 0;
    const rel = has ? pct(f.relevant, f.analyzed) : 0;
    const sub = has ? pct(f.substantive, f.analyzed) : 0;
    const lead = has
      ? `Of every 100 articles analyzed, <strong>${rel}</strong> are real engineering stories and <strong>${sub}</strong> are rich enough for a full analysis.`
      : "The funnel fills in as soon as the next sync has run.";
    return `
      <h3 class="stats-card-title">From feed to front page</h3>
      <p class="stats-card-lead">${lead}</p>
      <ul class="stats-rows">
        ${statsRow("Analyzed", has ? formatInt(f.analyzed) : "—", has ? 100 : 0, !has)}
        ${statsRow("Engineering-relevant", has ? `${rel}%` : "—", rel, !has)}
        ${statsRow("Passed full analysis", has ? `${sub}%` : "—", sub, !has)}
      </ul>
      <p class="stats-note">Then only the highest BITL Scores make the circle — ${MAX_PAPERS} per day.</p>`;
  }

  function categoriesCardHTML() {
    const list = publishedPool();
    const counts = countBy(list, "category");
    const cats = statsData.categories.length ? [...statsData.categories] : [...counts.keys()];
    counts.forEach((_, c) => { if (!cats.includes(c)) cats.push(c); });
    const ordered = cats.map((c, i) => ({ c, i, n: counts.get(c) || 0 })).sort((a, b) => b.n - a.n || a.i - b.i);
    const max = Math.max(1, ...ordered.map((o) => o.n));
    const covered = ordered.filter((o) => o.n > 0).length;
    return `
      <h3 class="stats-card-title">Coverage by category</h3>
      <p class="stats-card-lead"><strong>${formatInt(list.length)}</strong> stories published right now, across <strong>${covered}</strong> of ${ordered.length} categories.</p>
      <ul class="stats-rows">
        ${ordered.map((o) => statsRow(o.c, String(o.n), pct(o.n, max), o.n === 0)).join("")}
      </ul>`;
  }

  function bitlCardHTML() {
    const scores = allPapers.map((p) => Number(p.score)).filter((n) => Number.isFinite(n));
    const avg = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null;
    const top = scores.length ? Math.round(Math.max(...scores)) : null;
    const maxWeight = Math.max(...BITL_WEIGHTS.map((w) => w[1]));
    return `
      <h3 class="stats-card-title">The BITL Score</h3>
      <div class="stats-score">
        <div class="stats-score-item"><span class="stats-score-num">${avg === null ? "—" : avg}</span><span class="stats-score-cap">Average today</span></div>
        <div class="stats-score-item"><span class="stats-score-num">${top === null ? "—" : top}</span><span class="stats-score-cap">Highest today</span></div>
      </div>
      <ul class="stats-rows">
        ${BITL_WEIGHTS.map(([label, w]) => statsRow(label, `${w}%`, pct(w, maxWeight), false)).join("")}
      </ul>
      <p class="stats-note">Seven checks by an AI reviewer, combined into one weighted 0–100 number.</p>`;
  }

  function sourcesCardHTML() {
    const counts = countBy(publishedPool(), "source_name");
    const names = statsData.sources.length ? [...statsData.sources] : [...counts.keys()];
    counts.forEach((_, s) => { if (!names.includes(s)) names.push(s); });
    const ordered = names.map((s, i) => ({ s, i, n: counts.get(s) || 0 })).sort((a, b) => b.n - a.n || a.i - b.i);
    const active = ordered.filter((o) => o.n > 0).length;
    return `
      <h3 class="stats-card-title">Sources monitored</h3>
      <p class="stats-card-lead"><strong>${active}</strong> of ${ordered.length} sources contributed to the current edition.</p>
      <ul class="stats-chips">
        ${ordered.map((o) => `<li class="stats-chip${o.n > 0 ? " is-active" : ""}">${escapeHTML(o.s)}${o.n > 0 ? ` <b>${o.n}</b>` : ""}</li>`).join("")}
      </ul>`;
  }

  function renderStatsDetail() {
    const html = [funnelCardHTML(), categoriesCardHTML(), bitlCardHTML(), sourcesCardHTML()];
    statsCards.forEach((card, i) => {
      card.innerHTML = html[i] || "";
      // Le barre restano vuote finché la card non entra in vista (animazione
      // di riempimento); dopo, o senza animazioni, mostrano subito il valore.
      if (statsBarsPlayed.has(i) || !hasGsap() || REDUCED_MOTION) applyBars(i, false);
    });
  }

  function applyBars(cardIndex, animate) {
    const card = statsCards[cardIndex];
    const fills = card ? [...card.querySelectorAll(".stats-fill")] : [];
    if (animate && hasGsap()) {
      window.gsap.fromTo(fills, { width: "0%" }, { width: (i, el) => `${el.dataset.w}%`, duration: 1.2, ease: "power3.out", stagger: 0.05 });
    } else {
      fills.forEach((el) => { el.style.width = `${el.dataset.w}%`; });
    }
  }

  /* ---- Espansione allo scroll ---- */

  function initStatsSection() {
    if (statsInitialized || !statsSection) return;
    statsInitialized = true;

    const animate = hasGsap() && !REDUCED_MOTION;
    renderStatsDetail();
    buildAllOdometers(animate);
    if (!animate) { statsCards.forEach((_, i) => statsBarsPlayed.add(i)); statsOdometersPlayed = true; return; }

    // Lo stato "nascosto" iniziale delle card lo dà il CSS (.is-scrubbed), non
    // GSAP: le tween che partono più avanti nella timeline non vengono
    // renderizzate finché lo scrub non le raggiunge, e dopo ogni
    // ScrollTrigger.refresh() lo stile inline viene ripulito.
    statsSection.classList.add("is-scrubbed");

    const { gsap, ScrollTrigger } = window;
    gsap.registerPlugin(ScrollTrigger);
    ScrollTrigger.config({ ignoreMobileResize: true }); // la barra indirizzi mobile non deve far ricalcolare tutto a ogni scroll

    // Capsula (alta quanto la sola barra) -> pannello pieno, scrubbata sullo scroll:
    // parte quando la sezione entra dal basso e finisce esattamente a fondo pagina.
    // Il bordo inferiore dello sfondo segue quello dello schermo, quindi tutto ciò
    // che è in vista è sempre sopra lo sfondo scuro.
    gsap.fromTo(statsBg,
      { height: () => statsBar.offsetHeight, borderRadius: () => statsBar.offsetHeight / 2 },
      {
        height: () => statsPanel.offsetHeight, borderRadius: 28, ease: "none",
        scrollTrigger: { trigger: statsSection, start: "top 92%", end: "bottom bottom", scrub: 0.8, invalidateOnRefresh: true },
      });

    // Ogni card compare quando ENTRA nel viewport (non su una timeline unica):
    // così, sia in 2 colonne sia in colonna singola su mobile, nessuna card è
    // mai visibile a metà opacità prima di essere davvero a schermo.
    statsCards.forEach((card, i) => {
      gsap.fromTo(card,
        { opacity: 0, y: 56 },
        { opacity: 1, y: 0, ease: "power2.out",
          scrollTrigger: { trigger: card, start: "top 94%", end: "top 68%", scrub: 0.6 } });
      ScrollTrigger.create({
        trigger: card, start: "top 84%", once: true,
        onEnter: () => { statsBarsPlayed.add(i); applyBars(i, true); },
      });
    });

    // I numeri "rullano" appena la barra entra in vista. Una volta sola.
    ScrollTrigger.create({ trigger: statsBar, start: "top 96%", once: true, onEnter: playOdometers });
  }

  // Ricalcola il dettaglio quando arrivano dati nuovi (refresh periodico,
  // Flash News, stats.json) senza rifare né animazioni né timeline.
  function scheduleStatsRefresh() {
    if (!statsInitialized) return;
    clearTimeout(statsRefreshTimer);
    statsRefreshTimer = setTimeout(() => {
      renderStatsDetail();
      buildAllOdometers(hasGsap() && !REDUCED_MOTION && !statsOdometersPlayed);
      refreshScrollTriggers();
    }, 60);
  }

  // Quando cambia l'altezza della pagina (nuovo render della rete, dati che
  // arrivano) le posizioni di start/end di ScrollTrigger vanno ricalcolate.
  function refreshScrollTriggers() {
    if (!statsInitialized || !hasGsap()) return;
    clearTimeout(scrollTriggerRefreshTimer);
    scrollTriggerRefreshTimer = setTimeout(() => window.ScrollTrigger.refresh(), 120);
  }

  /* ---- Pannello "BITL Score" (icona i) ---- */

  function createBitlInfoHTML() {
    const sourcesHTML = statsData.sources.length
      ? statsData.sources.map((s) => `<li>${escapeHTML(s)}</li>`).join("")
      : `<li>${escapeHTML("Loading…")}</li>`;
    const categoriesHTML = statsData.categories.map((c) => `<li>${escapeHTML(c)}</li>`).join("");
    const weightsHTML = BITL_WEIGHTS.map(([label, w]) => `<li><span>${escapeHTML(label)}</span><span>${w}%</span></li>`).join("");

    return `
      <p class="bitl-info-lead">The <strong>BITL Score</strong> — "Be In The Loop" — ranks every story from 0 to 100, so the ones on screen are the ones actually worth your time.</p>
      <p class="bitl-info-text">Each article is scored by an AI reviewer on seven checks, then combined into one weighted number — never picked by hand, always recomputed the same way:</p>
      <ul class="bitl-weights">${weightsHTML}</ul>
      <p class="bitl-info-text">Evidence and real-world applicability count the most: a flashy claim with nothing to back it up scores low, however exciting it sounds.</p>
      <h3 class="bitl-info-heading">Sources monitored (${statsData.sources.length})</h3>
      <ul class="bitl-source-list">${sourcesHTML}</ul>
      <h3 class="bitl-info-heading">Categories tracked (${statsData.categories.length})</h3>
      <ul class="bitl-source-list">${categoriesHTML}</ul>
    `;
  }

  if (bitlInfoBtn) {
    bitlInfoBtn.addEventListener("click", () => {
      document.querySelectorAll(".neuron").forEach((el) => el.classList.remove("selected"));
      openPanel("THE BITL SCORE", createBitlInfoHTML());
    });
  }

  /* ---------------------------------------------------------------- */
  /* Apertura rete                                                     */
  /* ---------------------------------------------------------------- */

  openBtn.addEventListener("click", async () => {
    if (networkOpened) return;
    networkOpened = true;
    app.classList.add("opened");
    await Promise.all([dataReady, statsReady, flashReady]);
    initStatsSection(); // il footer è già visibile (classe .opened): si può misurare
    requestAnimationFrame(() => {
      if (visiblePapers.length) renderNetwork();
      else emptyState.hidden = false;
    });
  });

  // Sui browser mobile lo scroll nasconde/mostra la barra degli indirizzi,
  // il che cambia window.innerHeight e quindi scatena un evento "resize" a
  // ogni scroll — senza questo controllo, ogni scroll ridisegnava l'intera
  // rete da zero (animazioni di comparsa comprese), il che si percepiva
  // come scatti e come se la pagina "risalisse". Ridisegniamo solo se
  // cambia la LARGHEZZA (unico caso che richiede davvero un nuovo layout).
  let lastRenderWidth = window.innerWidth;
  window.addEventListener("resize", debounce(() => {
    if (window.innerWidth === lastRenderWidth) return;
    lastRenderWidth = window.innerWidth;
    if (networkOpened && visiblePapers.length) renderNetwork();
  }, 220));

  /* ---------------------------------------------------------------- */
  /* Archive — "Learn from yesterday to act today"                     */
  /* ---------------------------------------------------------------- */

  function setEmptyStateMessage(isArchive) {
    if (!emptyStateText) return;
    emptyStateText.textContent = isArchive
      ? "No archive available yet — check back tomorrow."
      : "No papers available in the current time window.";
  }

  if (archiveToggle) {
    archiveToggle.addEventListener("click", () => {
      archiveMode = !archiveMode;
      archiveToggle.classList.toggle("active", archiveMode);
      archiveToggle.setAttribute("aria-expanded", String(archiveMode));
      if (archiveStatus) archiveStatus.textContent = archiveMode ? "VIEWING · YESTERDAY" : "VIEWING · TODAY";
      setEmptyStateMessage(archiveMode);

      visiblePapers = [...currentPool()];
      activeCategory = null;
      renderFilters();

      if (!networkOpened) return; // la rete non è ancora aperta: nulla da ridisegnare ora
      if (visiblePapers.length) {
        renderNetwork();
      } else {
        neuronsLayer.innerHTML = "";
        synapses.innerHTML = "";
        emptyState.hidden = false;
        refreshScrollTriggers();
      }
    });
  }

  /* ---------------------------------------------------------------- */
  /* Layout a cerchio regolare                                         */
  /* ---------------------------------------------------------------- */

  function computeCirclePositions(count, stageRect, coreX, coreY, columnVisible, compact) {
    // In modalità compatta (telefono) usiamo lo stesso layout circolare del
    // desktop, solo con card molto più piccole — niente più fallback a
    // lista verticale: il cervello al centro è parte dell'identità visiva
    // del sito e non deve sparire su schermi stretti.
    const cardW = compact ? 56 : 150, cardH = compact ? 56 : 190, gap = compact ? 12 : 22;
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

    // Stesso layout circolare a ogni larghezza — su telefono (compact)
    // cambiano solo le dimensioni di card/cervello/raggio, mai la struttura:
    // il cervello al centro resta sempre il fulcro visivo del sito.
    const compact = window.innerWidth <= 640;
    const columnVisible = window.innerWidth > 1080;
    const cardHalfHeight = compact ? 30 : 110;

    // Il cervello è centrato via CSS (top: 50% dell'altezza dello stage), le card
    // sono posizionate in pixel da JS. Se lo stage cambia altezza DOPO aver
    // calcolato le posizioni, il 50% si sposta: il cervello scende e le card no
    // (il cervello "decentrato e più in basso" visto su telefono, dove lo stage
    // parte basso e quasi sempre deve crescere per contenere il cerchio).
    // Perciò si porta PRIMA lo stage all'altezza necessaria e solo dopo si misura
    // il cervello e si piazzano le card. Il raggio dipende solo da larghezza e
    // numero di card, quindi la seconda passata è già stabile (max 3 per sicurezza).
    stage.style.minHeight = "";
    let layout = null, coreX = 0, coreY = 0;
    for (let pass = 0; pass < 3; pass++) {
      neuralCore.style.transform = "";
      connectorRing.style.transform = "";
      const stageRect = stage.getBoundingClientRect();
      const coreRect = neuralCore.getBoundingClientRect();
      coreX = coreRect.left + coreRect.width / 2 - stageRect.left;
      coreY = coreRect.top + coreRect.height / 2 - stageRect.top;
      layout = computeCirclePositions(visiblePapers.length, stageRect, coreX, coreY, columnVisible, compact);

      const maxBottom = layout.positions.reduce((m, p) => Math.max(m, p.y + cardHalfHeight), 0);
      const needed = Math.ceil(maxBottom + 40);
      if (needed <= stageRect.height + 1) break;   // il cerchio ci sta: misure valide
      stage.style.minHeight = `${needed}px`;       // altrimenti allunga lo stage e rimisura
    }
    const { positions, dotRadius, shiftDown } = layout;

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

    positions.forEach((pos, i) => {
      drawSynapse(pos, i);
      createPaperCard(visiblePapers[i], pos, i, coreX, coreY, compact);
    });
    applyFilter(activeCategory);
    refreshScrollTriggers();
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

  function createPaperCard(paper, pos, index, coreX, coreY, compact) {
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
    article.className = compact ? "neuron neuron-compact" : "neuron";
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

    if (compact) {
      // Nodo compatto per telefono: stesso cerchio/cervello del desktop, ma
      // niente testo (illeggibile a queste dimensioni) — solo miniatura,
      // indice e punteggio. Il tap apre lo stesso pannello di dettaglio
      // completo del desktop (vedi openDetail più sotto, invariato).
      const thumbHTML = paper.image_url
        ? `<img class="neuron-thumb-img" src="${escapeHTML(paper.image_url)}" alt="" loading="lazy">`
        : `<div class="neuron-thumb-img neuron-thumb-na">BL</div>`;
      const scoreBadgeCompactHTML = createScoreBadge(paper.score, 26);
      article.innerHTML = `
        <span class="neuron-compact-index">${String(index + 1).padStart(2, "0")}</span>
        ${thumbHTML}
        ${scoreBadgeCompactHTML ? `<span class="neuron-compact-score">${scoreBadgeCompactHTML}</span>` : ""}
      `;
    } else {
      const imageHTML = paper.image_url
        ? `<img class="paper-image" src="${escapeHTML(paper.image_url)}" alt="" loading="lazy">`
        : `<div class="paper-image paper-image-na"><span class="mark-main">Be in<br>the loop</span></div>`;
      const scoreBadgeHTML = createScoreBadge(paper.score, 34);

      article.innerHTML = `
        <div class="paper-header">
          <span class="paper-number">RESEARCH ARTICLE</span>
          <span>${String(index + 1).padStart(2, "0")}${paper.is_preprint ? " · PREPRINT" : ""}</span>
        </div>
        <div class="paper-image-wrap">
          ${imageHTML}
          ${scoreBadgeHTML ? `<div class="score-badge-wrap">${scoreBadgeHTML}</div>` : ""}
        </div>
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
    }

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
    const categories = [...new Set(currentPool().map((p) => p.category))];
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

  const detailPanelKicker = document.getElementById("detail-panel-kicker");

  // Pannello laterale generico: lo stesso identico markup/animazione serve
  // sia per il dettaglio di un articolo sia per il pannello "BITL Score" —
  // un solo posto dove gestire apertura/chiusura/focus/scroll-lock invece
  // di due pannelli quasi identici duplicati.
  function openPanel(kickerText, contentHTML, category) {
    detailPanelKicker.textContent = kickerText;
    if (category) detailContent.setAttribute("data-category", category);
    else detailContent.removeAttribute("data-category");
    detailContent.innerHTML = contentHTML;
    detailPanel.classList.add("visible");
    detailOverlay.classList.add("visible");
    detailPanel.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    detailClose.focus();
  }

  function openDetail(paper, cardEl) {
    document.querySelectorAll(".neuron").forEach((el) => el.classList.remove("selected"));
    if (cardEl) cardEl.classList.add("selected");
    openPanel("RESEARCH PAPER", createDetailHTML(paper), paper.category);
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
    const scoreBadgeHTML = createScoreBadge(p.score, 48);

    return `
      <div class="d-image-wrap">
        ${imageHTML}
        ${scoreBadgeHTML ? `<div class="score-badge-wrap score-badge-wrap-lg">${scoreBadgeHTML}</div>` : ""}
      </div>
      <div class="d-meta">
        <span>${escapeHTML(p.source_name)}</span>
        <span class="d-badge">${escapeHTML(p.category)}</span>
        <span>${escapeHTML(formatDate(p.published_at))}</span>
        ${preprintBadge}
      </div>

      <h2 class="d-title">${escapeHTML(p.title)}</h2>

      <div class="d-background">
        <span class="d-background-label">BACKGROUND · THE BIG PROBLEM</span>
        <p class="d-bluf">${escapeHTML(p.big_problem)}</p>
      </div>

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
