/* ====================================================================== */
/* BE IN THE LOOP                                                        */
/* NEURAL EDITORIAL ENGINE                                               */
/* ====================================================================== */

(() => {

    "use strict";


    /* ================================================================== */
    /* CONFIG                                                             */
    /* ================================================================== */

    const NEWS_FILE =
        "news.json";


    const WINDOW_HOURS =
        24;


    /*
     * Se vuoi mostrare tutti gli articoli:
     *
     * const MAX_PAPERS = null;
     *
     * Se vuoi limitare il numero:
     *
     * const MAX_PAPERS = 8;
     */

    const MAX_PAPERS =
        8;


    const REDUCED_MOTION =
        window.matchMedia(
            "(prefers-reduced-motion: reduce)"
        ).matches;


    /* ================================================================== */
    /* DOM                                                                */
    /* ================================================================== */

    const app =
        document.getElementById(
            "app"
        );


    const openButton =
        document.getElementById(
            "open-btn"
        );


    const stage =
        document.getElementById(
            "stage"
        );


    const neuralCore =
        document.getElementById(
            "neural-core"
        );


    const synapses =
        document.getElementById(
            "synapses"
        );


    const neuronsLayer =
        document.getElementById(
            "neurons-layer"
        );


    const emptyState =
        document.getElementById(
            "empty-state"
        );


    const filters =
        document.getElementById(
            "filters"
        );


    const lastUpdated =
        document.getElementById(
            "last-updated"
        );


    const detailPanel =
        document.getElementById(
            "detail-panel"
        );


    const detailContent =
        document.getElementById(
            "detail-content"
        );


    const detailOverlay =
        document.getElementById(
            "detail-overlay"
        );


    const detailClose =
        document.getElementById(
            "detail-close"
        );


    /* ================================================================== */
    /* STATE                                                              */
    /* ================================================================== */

    let allPapers =
        [];


    let visiblePapers =
        [];


    let activeCategory =
        null;


    let networkOpened =
        false;


    let resizeTimer =
        null;


    let animationFrame =
        null;


    /* ================================================================== */
    /* HELPERS                                                            */
    /* ================================================================== */

    function safeText(
        value
    ) {

        if (
            value === null ||
            value === undefined ||
            String(value).trim() === ""
        ) {

            return "N/A";

        }


        return String(
            value
        ).trim();

    }


    function escapeHTML(
        value
    ) {

        const element =
            document.createElement(
                "div"
            );


        element.textContent =
            safeText(
                value
            );


        return element.innerHTML;

    }


    function normalizeImage(
        value
    ) {

        if (
            !value
        ) {

            return "";

        }


        const image =
            String(
                value
            ).trim();


        if (
            image.startsWith(
                "/static/browse/"
            )
        ) {

            return (
                "https://arxiv.org" +
                image
            );

        }


        return image;

    }


    function formatDate(
        value
    ) {

        const date =
            new Date(
                value
            );


        if (
            Number.isNaN(
                date.getTime()
            )
        ) {

            return "N/A";

        }


        return date.toLocaleDateString(
            "it-IT",
            {
                day:
                    "2-digit",

                month:
                    "long",

                year:
                    "numeric"
            }
        );

    }


    function formatDateTime(
        value
    ) {

        const date =
            new Date(
                value
            );


        if (
            Number.isNaN(
                date.getTime()
            )
        ) {

            return "N/A";

        }


        return date.toLocaleString(
            "it-IT",
            {
                day:
                    "2-digit",

                month:
                    "2-digit",

                year:
                    "numeric",

                hour:
                    "2-digit",

                minute:
                    "2-digit"
            }
        );

    }


    function normalizePaper(
        paper
    ) {

        return {

            ...paper,

            title:
                safeText(
                    paper.title
                ),

            source_name:
                safeText(
                    paper.source_name
                ),

            source_url:
                safeText(
                    paper.source_url
                ),

            published_at:
                paper.published_at,

            category:
                safeText(
                    paper.category
                ) === "N/A"
                    ? "Altro"
                    : safeText(
                        paper.category
                    ),

            image_url:
                normalizeImage(
                    paper.image_url
                ),

            big_problem:
                safeText(
                    paper.big_problem
                ),

            small_problem:
                safeText(
                    paper.small_problem
                ),

            idea:
                safeText(
                    paper.idea
                ),

            plan:
                safeText(
                    paper.plan
                ),

            conclusion:
                safeText(
                    paper.conclusion
                ),

            future_directions:
                safeText(
                    paper.future_directions
                ),

            result_1_headline:
                safeText(
                    paper.result_1_headline
                ),

            result_1_number:
                safeText(
                    paper.result_1_number
                ),

            result_1_detail:
                safeText(
                    paper.result_1_detail
                ),

            result_2_headline:
                safeText(
                    paper.result_2_headline
                ),

            result_2_number:
                safeText(
                    paper.result_2_number
                ),

            result_2_detail:
                safeText(
                    paper.result_2_detail
                ),

            result_3_headline:
                safeText(
                    paper.result_3_headline
                ),

            result_3_number:
                safeText(
                    paper.result_3_number
                ),

            result_3_detail:
                safeText(
                    paper.result_3_detail
                )

        };

    }


    /* ================================================================== */
    /* LOAD DATA                                                          */
    /* ================================================================== */

    async function loadNews() {

        try {

            const response =
                await fetch(
                    NEWS_FILE,
                    {
                        cache:
                            "no-store"
                    }
                );


            if (
                !response.ok
            ) {

                throw new Error(
                    "Impossibile caricare news.json"
                );

            }


            const data =
                await response.json();


            if (
                !Array.isArray(
                    data
                )
            ) {

                throw new Error(
                    "news.json deve contenere un array"
                );

            }


            const cutoff =
                Date.now() -
                (
                    WINDOW_HOURS *
                    60 *
                    60 *
                    1000
                );


            allPapers =

                data

                    .filter(
                        paper => {

                            if (
                                !paper.title ||
                                !paper.source_name ||
                                !paper.source_url ||
                                !paper.published_at
                            ) {

                                return false;

                            }


                            const date =
                                new Date(
                                    paper.published_at
                                );


                            return (
                                !Number.isNaN(
                                    date.getTime()
                                )
                            );

                        }
                    )

                    .filter(
                        paper => {

                            const timestamp =
                                new Date(
                                    paper.published_at
                                ).getTime();


                            return (
                                timestamp >=
                                cutoff
                            );

                        }
                    )

                    .map(
                        normalizePaper
                    )

                    .sort(
                        (
                            a,
                            b
                        ) =>

                            new Date(
                                b.published_at
                            ) -

                            new Date(
                                a.published_at
                            )

                    );


            if (
                MAX_PAPERS !== null
            ) {

                allPapers =
                    allPapers.slice(
                        0,
                        MAX_PAPERS
                    );

            }


            visiblePapers =
                [
                    ...allPapers
                ];


            renderFilters();


            renderLastUpdated(
                data
            );


            console.log(
                "Paper caricati:",
                allPapers.length
            );


        } catch (
            error
        ) {

            console.error(
                error
            );


            allPapers =
                [];


            visiblePapers =
                [];


            renderEmptyState();

        }

    }


    /* ================================================================== */
    /* LAST UPDATED                                                       */
    /* ================================================================== */

    function renderLastUpdated(
        data
    ) {

        const dates =

            data

                .map(
                    item =>
                        new Date(
                            item.fetched_at
                        )
                )

                .filter(
                    date =>
                        !Number.isNaN(
                            date.getTime()
                        )
                );


        if (
            !dates.length
        ) {

            lastUpdated.textContent =
                "";

            return;

        }


        const latest =
            dates.reduce(
                (
                    max,
                    current
                ) =>
                    current >
                    max
                        ? current
                        : max
            );


        lastUpdated.textContent =
            "LAST UPDATE · " +
            formatDateTime(
                latest
            );

    }


    /* ================================================================== */
    /* FILTERS                                                            */
    /* ================================================================== */

    function renderFilters() {

        filters.innerHTML =
            "";


        const categories =

            [
                ...new Set(
                    allPapers.map(
                        paper =>
                            paper.category
                    )
                )
            ];


        if (
            !categories.length
        ) {

            return;

        }


        const allButton =
            createFilterButton(
                "TUTTI",
                null
            );


        allButton.classList.add(
            "active"
        );


        filters.appendChild(
            allButton
        );


        categories.forEach(
            category => {

                filters.appendChild(
                    createFilterButton(
                        category,
                        category
                    )
                );

            }
        );

    }


    function createFilterButton(
        label,
        category
    ) {

        const button =
            document.createElement(
                "button"
            );


        button.type =
            "button";


        button.className =
            "chip";


        button.textContent =
            label;


        button.addEventListener(
            "click",
            () => {

                activeCategory =
                    category;


                document
                    .querySelectorAll(
                        ".chip"
                    )
                    .forEach(
                        item =>
                            item.classList.remove(
                                "active"
                            )
                    );


                button.classList.add(
                    "active"
                );


                applyFilter();

            }
        );


        return button;

    }


    function applyFilter() {

        document
            .querySelectorAll(
                ".neuron"
            )
            .forEach(
                paper => {

                    const match =

                        !activeCategory ||

                        paper.dataset.category ===
                        activeCategory;


                    paper.classList.toggle(
                        "dimmed",
                        !match
                    );

                }
            );


        document
            .querySelectorAll(
                ".synapse"
            )
            .forEach(
                line => {

                    if (
                        line.dataset.type !==
                        "core"
                    ) {

                        line.style.opacity =
                            activeCategory
                                ? ".08"
                                : "";

                        return;

                    }


                    const index =
                        Number(
                            line.dataset.paperIndex
                        );


                    const paper =
                        visiblePapers[
                            index
                        ];


                    if (
                        !paper
                    ) {

                        return;

                    }


                    const match =

                        !activeCategory ||

                        paper.category ===
                        activeCategory;


                    line.style.opacity =
                        match
                            ? ""
                            : ".04";

                }
            );

    }


    /* ================================================================== */
    /* OPEN NETWORK                                                       */
    /* ================================================================== */

    openButton.addEventListener(
        "click",
        () => {

            if (
                networkOpened
            ) {

                return;

            }


            networkOpened =
                true;


            app.classList.add(
                "opened"
            );


            setTimeout(
                () => {

                    if (
                        visiblePapers.length
                    ) {

                        renderNetwork();

                    } else {

                        renderEmptyState();

                    }

                },
                500
            );

        }
    );


    /* ================================================================== */
    /* EMPTY STATE                                                        */
    /* ================================================================== */

    function renderEmptyState() {

        emptyState.hidden =
            false;

    }


    /* ================================================================== */
    /* NETWORK RENDER                                                     */
    /* ================================================================== */

    function renderNetwork() {

        neuronsLayer.innerHTML =
            "";


        synapses.innerHTML =
            "";


        if (
            animationFrame
        ) {

            cancelAnimationFrame(
                animationFrame
            );

        }


        const stageRect =
            stage.getBoundingClientRect();


        const isMobile =
            window.innerWidth <= 650;


        const coreRect =
            neuralCore.getBoundingClientRect();


        const stageLeft =
            stageRect.left;


        const stageTop =
            stageRect.top;


        const coreX =
            coreRect.left +
            coreRect.width /
            2 -
            stageLeft;


        const coreY =
            coreRect.top +
            coreRect.height /
            2 -
            stageTop;


        const positions =
            calculatePositions(
                stageRect.width,
                stageRect.height,
                isMobile
            );


        drawCoreConnections(
            coreX,
            coreY,
            positions
        );


        drawPaperConnections(
            positions
        );


        positions.forEach(
            (
                position,
                index
            ) => {

                createPaperCard(
                    visiblePapers[
                        index
                    ],
                    position,
                    index
                );

            }
        );


        applyFilter();


        if (
            !REDUCED_MOTION
        ) {

            startPulseAnimation();

        }

    }


    /* ================================================================== */
    /* PAPER POSITIONS                                                    */
    /* ================================================================== */

    function calculatePositions(
        width,
        height,
        isMobile
    ) {

        const positions =
            [];


        /*
         * Il layout è intenzionalmente asimmetrico.
         *
         * Le card non vengono messe in cerchio.
         * Questo evita l'effetto "orbita matematica"
         * e produce un impianto editoriale più simile
         * a una composizione di magazine.
         */


        if (
            isMobile
        ) {

            const mobilePositions = [

                {
                    x:
                        width *
                        .18,

                    y:
                        height *
                        .73
                },

                {
                    x:
                        width *
                        .82,

                    y:
                        height *
                        .73
                },

                {
                    x:
                        width *
                        .18,

                    y:
                        height *
                        .90
                },

                {
                    x:
                        width *
                        .82,

                    y:
                        height *
                        .90
                },

                {
                    x:
                        width *
                        .50,

                    y:
                        height *
                        .92
                },

                {
                    x:
                        width *
                        .15,

                    y:
                        height *
                        .55
                },

                {
                    x:
                        width *
                        .85,

                    y:
                        height *
                        .55
                },

                {
                    x:
                        width *
                        .50,

                    y:
                        height *
                        .70
                }

            ];


            return visiblePapers.map(
                (
                    paper,
                    index
                ) =>
                    mobilePositions[
                        index %
                        mobilePositions.length
                    ]
            );

        }


        const desktopPositions = [

            {
                x:
                    width *
                    .24,

                y:
                    height *
                    .28
            },

            {
                x:
                    width *
                    .82,

                y:
                    height *
                    .24
            },

            {
                x:
                    width *
                    .15,

                y:
                    height *
                    .65
            },

            {
                x:
                    width *
                    .87,

                y:
                    height *
                    .62
            },

            {
                x:
                    width *
                    .30,

                y:
                    height *
                    .86
            },

            {
                x:
                    width *
                    .72,

                y:
                    height *
                    .87
            },

            {
                x:
                    width *
                    .08,

                y:
                    height *
                    .43
            },

            {
                x:
                    width *
                    .93,

                y:
                    height *
                    .43
            }

        ];


        return visiblePapers.map(
            (
                paper,
                index
            ) =>
                desktopPositions[
                    index %
                    desktopPositions.length
                ]
        );

    }


    /* ================================================================== */
    /* CORE CONNECTIONS                                                   */
    /* ================================================================== */

    function drawCoreConnections(
        coreX,
        coreY,
        positions
    ) {

        positions.forEach(
            (
                position,
                index
            ) => {

                const path =
                    createCurvedPath(
                        coreX,
                        coreY,
                        position.x,
                        position.y,
                        index
                    );


                path.dataset.type =
                    "core";


                path.dataset.paperIndex =
                    String(
                        index
                    );


                synapses.appendChild(
                    path
                );

            }
        );

    }


    /* ================================================================== */
    /* PAPER CONNECTIONS                                                  */
    /* ================================================================== */

    function drawPaperConnections(
        positions
    ) {

        /*
         * Collega ogni card con la card successiva.
         * La rete resta leggibile e non diventa una ragnatela.
         */

        for (
            let index = 0;
            index <
            positions.length - 1;
            index++
        ) {

            const a =
                positions[
                    index
                ];


            const b =
                positions[
                    index + 1
                ];


            const path =
                createCurvedPath(
                    a.x,
                    a.y,
                    b.x,
                    b.y,
                    index +
                    100
                );


            path.classList.add(
                "synapse-secondary"
            );


            path.dataset.type =
                "secondary";


            synapses.appendChild(
                path
            );

        }

    }


    /* ================================================================== */
    /* CURVED PATH                                                        */
    /* ================================================================== */

    function createCurvedPath(
        x1,
        y1,
        x2,
        y2,
        index
    ) {

        const dx =
            x2 -
            x1;


        const dy =
            y2 -
            y1;


        const distance =
            Math.sqrt(
                dx * dx +
                dy * dy
            );


        const nx =
            -dy /
            (
                distance ||
                1
            );


        const ny =
            dx /
            (
                distance ||
                1
            );


        const direction =
            index %
            2 ===
            0
                ? 1
                : -1;


        const curve =
            Math.min(
                90,
                distance *
                .12
            );


        const cx =
            (
                x1 +
                x2
            ) /
            2 +
            nx *
            curve *
            direction;


        const cy =
            (
                y1 +
                y2
            ) /
            2 +
            ny *
            curve *
            direction;


        const path =
            document.createElementNS(
                "http://www.w3.org/2000/svg",
                "path"
            );


        path.setAttribute(
            "d",
            `
            M ${x1} ${y1}
            Q ${cx} ${cy}
            ${x2} ${y2}
            `
        );


        path.classList.add(
            "synapse"
        );


        return path;

    }


    /* ================================================================== */
    /* PAPER CARD                                                         */
    /* ================================================================== */

    function createPaperCard(
        paper,
        position,
        index
    ) {

        const article =
            document.createElement(
                "article"
            );


        article.className =
            "neuron";


        article.dataset.category =
            paper.category;


        article.dataset.index =
            String(
                index
            );


        article.style.left =
            `${position.x}px`;


        article.style.top =
            `${position.y}px`;


        article.tabIndex =
            0;


        article.setAttribute(
            "role",
            "button"
        );


        article.setAttribute(
            "aria-label",
            paper.title
        );


        const imageHTML =

            paper.image_url

                ?

                `
                <img
                    class="paper-image"
                    src="${escapeHTML(
                        paper.image_url
                    )}"
                    alt=""
                    loading="lazy"
                >
                `

                :

                `
                <div
                    class="paper-image paper-image-na"
                >
                    N/A
                </div>
                `;


        article.innerHTML = `

            <div class="paper-header">

                <span class="paper-number">
                    RESEARCH ARTICLE
                </span>

                <span>
                    ${String(
                        index +
                        1
                    ).padStart(
                        2,
                        "0"
                    )}
                </span>

            </div>


            ${imageHTML}


            <div class="paper-body">

                <div class="paper-category">

                    ${escapeHTML(
                        paper.category
                    )}

                </div>


                <h2 class="paper-title">

                    ${escapeHTML(
                        paper.title
                    )}

                </h2>


                <p class="paper-abstract">

                    ${escapeHTML(
                        createAbstract(
                            paper
                        )
                    )}

                </p>


                <div class="paper-meta">

                    <span>

                        ${escapeHTML(
                            formatDate(
                                paper.published_at
                            )
                        )}

                    </span>


                    <span>

                        ${escapeHTML(
                            paper.source_name
                        )}

                    </span>

                </div>

            </div>

        `;


        article.addEventListener(
            "click",
            () =>
                openDetail(
                    paper,
                    article
                )
        );


        article.addEventListener(
            "keydown",
            event => {

                if (
                    event.key ===
                    "Enter" ||

                    event.key ===
                    " "
                ) {

                    event.preventDefault();


                    openDetail(
                        paper,
                        article
                    );

                }

            }
        );


        neuronsLayer.appendChild(
            article
        );

    }


    /* ================================================================== */
    /* ABSTRACT                                                           */
    /* ================================================================== */

    function createAbstract(
        paper
    ) {

        const candidates = [

            paper.idea,

            paper.small_problem,

            paper.big_problem,

            paper.conclusion

        ];


        for (
            const value of
            candidates
        ) {

            if (
                value &&
                value !== "N/A"
            ) {

                return value;

            }

        }


        return "N/A";

    }


    /* ================================================================== */
    /* SYNAPTIC PULSE                                                     */
    /* ================================================================== */

    function startPulseAnimation() {

        const paths =
            [
                ...synapses.querySelectorAll(
                    ".synapse"
                )
            ];


        if (
            !paths.length
        ) {

            return;

        }


        const pulses =
            [];


        paths.forEach(
            (
                path,
                index
            ) => {

                const length =
                    path.getTotalLength();


                const pulse =
                    document.createElementNS(
                        "http://www.w3.org/2000/svg",
                        "circle"
                    );


                pulse.classList.add(
                    "synapse-pulse"
                );


                pulse.setAttribute(
                    "r",
                    path.classList.contains(
                        "synapse-secondary"
                    )
                        ? "2"
                        : "2.8"
                );


                synapses.appendChild(
                    pulse
                );


                pulses.push(
                    {
                        path,
                        pulse,
                        length,
                        delay:
                            index *
                            550
                    }
                );

            }
        );


        const start =
            performance.now();


        function animate(
            now
        ) {

            const elapsed =
                now -
                start;


            pulses.forEach(
                item => {

                    const duration =
                        item.path.classList.contains(
                            "synapse-secondary"
                        )
                            ? 9000
                            : 6500;


                    const progress =
                        (
                            elapsed +
                            item.delay
                        ) %
                        duration /
                        duration;


                    const point =
                        item.path.getPointAtLength(
                            progress *
                            item.length
                        );


                    item.pulse.setAttribute(
                        "cx",
                        point.x
                    );


                    item.pulse.setAttribute(
                        "cy",
                        point.y
                    );

                }
            );


            animationFrame =
                requestAnimationFrame(
                    animate
                );

        }


        animationFrame =
            requestAnimationFrame(
                animate
            );

    }


    /* ================================================================== */
    /* DETAIL PANEL                                                       */
    /* ================================================================== */

    function openDetail(
        paper,
        article
    ) {

        document
            .querySelectorAll(
                ".neuron"
            )
            .forEach(
                element =>
                    element.classList.remove(
                        "selected"
                    )
            );


        article.classList.add(
            "selected"
        );


        detailContent.innerHTML =
            createDetailHTML(
                paper
            );


        detailPanel.classList.add(
            "visible"
        );


        detailOverlay.classList.add(
            "visible"
        );


        detailPanel.setAttribute(
            "aria-hidden",
            "false"
        );


        document.body.style.overflow =
            "hidden";


        detailClose.focus();

    }


    function closeDetail() {

        detailPanel.classList.remove(
            "visible"
        );


        detailOverlay.classList.remove(
            "visible"
        );


        detailPanel.setAttribute(
            "aria-hidden",
            "true"
        );


        document.body.style.overflow =
            "";


        document
            .querySelectorAll(
                ".neuron"
            )
            .forEach(
                element =>
                    element.classList.remove(
                        "selected"
                    )
            );

    }


    detailClose.addEventListener(
        "click",
        closeDetail
    );


    detailOverlay.addEventListener(
        "click",
        closeDetail
    );


    document.addEventListener(
        "keydown",
        event => {

            if (
                event.key ===
                "Escape"
            ) {

                if (
                    detailPanel.classList.contains(
                        "visible"
                    )
                ) {

                    closeDetail();

                }

            }

        }
    );


    /* ================================================================== */
    /* DETAIL HTML                                                        */
    /* ================================================================== */

    function createDetailHTML(
        paper
    ) {

        const imageHTML =

            paper.image_url

                ?

                `
                <img
                    class="d-image"
                    src="${escapeHTML(
                        paper.image_url
                    )}"
                    alt=""
                >
                `

                :

                `
                <div
                    class="d-image paper-image-na"
                >
                    N/A
                </div>
                `;


        return `

            ${imageHTML}


            <div class="d-meta">

                <span>
                    ${escapeHTML(
                        paper.source_name
                    )}
                </span>


                <span class="d-badge">

                    ${escapeHTML(
                        paper.category
                    )}

                </span>


                <span>

                    ${escapeHTML(
                        formatDate(
                            paper.published_at
                        )
                    )}

                </span>

            </div>


            <p class="d-bluf-label">
                BLUF
            </p>


            <p class="d-bluf">

                ${escapeHTML(
                    paper.big_problem
                )}

            </p>


            <div class="d-intro">

                <p class="d-section-label">
                    INTRO
                </p>


                <div class="d-intro-row">

                    <strong>
                        PROBLEMA
                    </strong>

                    <span>
                        ${escapeHTML(
                            paper.small_problem
                        )}
                    </span>

                </div>


                <div class="d-intro-row">

                    <strong>
                        IDEA
                    </strong>

                    <span>
                        ${escapeHTML(
                            paper.idea
                        )}
                    </span>

                </div>


                <div class="d-intro-row">

                    <strong>
                        PIANO
                    </strong>

                    <span>
                        ${escapeHTML(
                            paper.plan
                        )}
                    </span>

                </div>

            </div>


            <p class="d-section-label">
                RISULTATI
            </p>


            <div class="d-results">


                ${createResultCard(
                    paper.result_1_headline,
                    paper.result_1_number,
                    paper.result_1_detail
                )}


                ${createResultCard(
                    paper.result_2_headline,
                    paper.result_2_number,
                    paper.result_2_detail
                )}


                ${createResultCard(
                    paper.result_3_headline,
                    paper.result_3_number,
                    paper.result_3_detail
                )}


            </div>


            <p class="d-section-label">
                CONCLUSIONE
            </p>


            <p class="d-conclusion">

                ${escapeHTML(
                    paper.conclusion
                )}

            </p>


            <p class="d-section-label">
                DIREZIONI FUTURE
            </p>


            <p class="d-future">

                ${escapeHTML(
                    paper.future_directions
                )}

            </p>


            <div class="d-actions">

                <a
                    class="d-source-link"
                    href="${escapeHTML(
                        paper.source_url
                    )}"
                    target="_blank"
                    rel="noopener noreferrer"
                >

                    LEGGI LA FONTE ORIGINALE ↗

                </a>

            </div>


            <p class="d-attribution">

                Contenuto elaborato automaticamente
                a partire dalla fonte indicata.

                Testo e diritti originali appartengono
                alla fonte:

                ${escapeHTML(
                    paper.source_name
                )}.

            </p>

        `;

    }


    /* ================================================================== */
    /* RESULT CARD                                                        */
    /* ================================================================== */

    function createResultCard(
        headline,
        number,
        detail
    ) {

        return `

            <div class="d-result-card">

                <div class="d-result-headline">

                    ${escapeHTML(
                        headline
                    )}

                </div>


                <span class="d-result-number">

                    ${escapeHTML(
                        number
                    )}

                </span>


                <div class="d-result-detail">

                    ${escapeHTML(
                        detail
                    )}

                </div>

            </div>

        `;

    }


    /* ================================================================== */
    /* RESPONSIVE NETWORK                                                */
    /* ================================================================== */

    window.addEventListener(
        "resize",
        () => {

            clearTimeout(
                resizeTimer
            );


            resizeTimer =
                setTimeout(
                    () => {

                        if (
                            networkOpened
                        ) {

                            renderNetwork();

                        }

                    },
                    250
                );

        }
    );


    /* ================================================================== */
    /* START                                                              */
    /* ================================================================== */

    loadNews();


})();
