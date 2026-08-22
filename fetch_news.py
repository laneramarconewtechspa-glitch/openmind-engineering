#!/usr/bin/env python3
"""
OpenMind Engineering — sync_engineering_news
=============================================
Raccoglie le notizie di ingegneria delle ultime ore da ScienceDaily, Tech Xplore,
IEEE Spectrum, MIT Technology Review, arXiv ed EurekAlert!, le filtra per
pertinenza ingegneristica e le struttura secondo lo schema BLUF / Intro / R1-R3 /
Conclusion usando l'API Gemini (Google AI Studio), poi salva tutto in docs/news.json
(letto dal frontend statico).

Pensato per girare come job schedulato di GitHub Actions, ma funziona anche in
locale: basta esportare GEMINI_API_KEY e lanciare `python fetch_news.py`.
"""

import datetime as dt
import json
import os
import re
import sys
import time
from html import unescape

import feedparser
import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------- #
# Configurazione
# --------------------------------------------------------------------------- #

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# "groq" (default) o "gemini" — permette di cambiare motore senza toccare il
# resto della pipeline, cambiando solo questa variabile d'ambiente nel workflow.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "groq").lower()

# Da febbraio 2026 OpenAlex richiede una chiave API (gratuita, $1 di credito al
# giorno — ampiamente sufficiente per il nostro utilizzo). Se non è impostata,
# la fonte OpenAlex viene semplicemente saltata invece di bloccare il resto.
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY")
OPENALEX_CONTACT_EMAIL = os.environ.get("OPENALEX_CONTACT_EMAIL", "")

OUTPUT_PATH = os.path.join("docs", "news.json")

# Finestra usata per RACCOGLIERE i candidati (con margine di sicurezza rispetto
# alle 24h "vere", per non perdere nulla tra due sync consecutivi).
COLLECT_WINDOW_HOURS = 30
# Finestra usata per TENERE gli elementi già pubblicati sul sito prima di
# eliminarli dal file (il frontend applica comunque il filtro "ultime 24h" a
# runtime, quindi questo è solo un margine per evitare buchi tra due sync).
RETAIN_WINDOW_HOURS = 48
# Numero massimo di notizie VALUTATE per ogni esecuzione: deve essere
# abbastanza alto da non tagliare nessuna fonte prima ancora di darle una
# possibilità (in pratica quasi mai raggiunto tutto). Non è il numero di
# notizie pubblicate: quello è deciso da PUBLISH_TOP_N qui sotto, in base
# all'OpenMind Score.
MAX_ITEMS_PER_RUN = 120
# Numero di notizie effettivamente PUBBLICATE sul sito: sempre e solo le
# migliori per OpenMind Score tra quelle che superano il controllo qualità,
# indipendentemente da quante ne vengono valutate.
PUBLISH_TOP_N = 10
# Se queste chiamate a Gemini falliscono di fila, il run si interrompe subito
# invece di ritentare inutilmente su tutte le notizie rimaste.
MAX_CONSECUTIVE_FAILURES = 3

REQUEST_HEADERS = {"User-Agent": "OpenMindEngineeringBot/1.0 (+personal news digest)"}

CATEGORIES = [
    "Robotics",
    "Aerospace",
    "Energy",
    "Civil & Infrastructure",
    "Mechanical",
    "Electronics & Semiconductors",
    "Materials",
    "Biomedical",
    "Computing",
    "Other Engineering",
]

SOURCES = [
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/matter_energy/engineering.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/matter_energy/civil_engineering.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/matter_energy/robotics.xml"},
    {"name": "Tech Xplore", "url": "https://techxplore.com/rss-feed/"},
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/type/news.rss"},
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss"},
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/topic/aerospace.rss"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "NSF", "url": "https://www.nsf.gov/rss/rss_www_news.xml"},
    # EurekAlert! disattivata: al momento non ho trovato un URL RSS pubblico
    # funzionante per la sezione Tech & Engineering (i pattern noti tornano
    # 404 — il sito sembra aver riorganizzato la distribuzione RSS). Se trovi
    # l'URL corretto, riattivala aggiungendo una riga come le altre qui sopra.
    {
        "name": "arXiv",
        "url": (
            "http://export.arxiv.org/api/query?search_query="
            "cat:eess.SY+OR+cat:eess.SP+OR+cat:eess.IV+OR+cat:cs.RO"
            "&sortBy=submittedDate&sortOrder=descending&max_results=25"
        ),
    },
]

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_engineering_relevant": {"type": "BOOLEAN"},
        "category": {"type": "STRING", "enum": CATEGORIES},
        "title": {"type": "STRING"},
        "big_problem": {"type": "STRING"},
        "small_problem": {"type": "STRING"},
        "idea": {"type": "STRING"},
        "plan": {"type": "STRING"},
        "result_1_headline": {"type": "STRING"},
        "result_1_number": {"type": "STRING"},
        "result_1_detail": {"type": "STRING"},
        "result_2_headline": {"type": "STRING"},
        "result_2_number": {"type": "STRING"},
        "result_2_detail": {"type": "STRING"},
        "result_3_headline": {"type": "STRING"},
        "result_3_number": {"type": "STRING"},
        "result_3_detail": {"type": "STRING"},
        "conclusion": {"type": "STRING"},
        "future_directions": {"type": "STRING"},
        "evidence_score": {"type": "INTEGER"},
        "applicability_score": {"type": "INTEGER"},
        "impact_score": {"type": "INTEGER"},
        "cross_domain_score": {"type": "INTEGER"},
        "maturity_score": {"type": "INTEGER"},
        "momentum_score": {"type": "INTEGER"},
        "feasibility_score": {"type": "INTEGER"},
    },
    "required": [
        "is_engineering_relevant", "category", "title", "big_problem", "small_problem",
        "idea", "plan",
        "result_1_headline", "result_1_number", "result_1_detail",
        "result_2_headline", "result_2_number", "result_2_detail",
        "result_3_headline", "result_3_number", "result_3_detail",
        "conclusion", "future_directions",
        "evidence_score", "applicability_score", "impact_score", "cross_domain_score",
        "maturity_score", "momentum_score", "feasibility_score",
    ],
}

# Pesi dell'"OpenMind Score": la somma pesata la calcoliamo NOI in Python (non
# il modello) subito dopo aver ricevuto i 7 punteggi grezzi, così il numero
# finale è sempre matematicamente corretto e verificabile.
SCORE_WEIGHTS = {
    "evidence_score": 0.25,
    "applicability_score": 0.20,
    "impact_score": 0.15,
    "cross_domain_score": 0.15,
    "feasibility_score": 0.15,
    "maturity_score": 0.05,
    "momentum_score": 0.05,
}


def compute_openmind_score(structured: dict) -> tuple[int, dict[str, int]]:
    """Calcola la somma pesata a partire dai 7 punteggi grezzi restituiti dal
    modello. Ritorna (score_finale, punteggi_singoli_puliti)."""
    clean_scores = {}
    total = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        try:
            val = float(structured.get(key))
        except (TypeError, ValueError):
            val = 50.0  # valore neutro se il modello non fornisce un numero valido
        val = round(max(0.0, min(100.0, val)))
        clean_scores[key] = val
        total += val * weight
    return round(total), clean_scores

SYSTEM_RULES = """You are a technical analyst preparing a daily engineering news digest for \
expert but time-pressed readers. You receive the title and the FULL TEXT of the \
article's web page for ONE single article/paper (already stripped of navigation/ads) \
and must return ONLY the JSON required by the schema, following these strict rules:

1. Evaluate "is_engineering_relevant" first. Set it to false in TWO cases:
   (a) the story is about pure health/medicine, pure policy, basic science, or \
       business with no clear engineering application or innovation;
   (b) the story COULD be relevant but the text provided is too thin, paywalled, \
       or generic to support a genuinely informative analysis (e.g. you cannot \
       identify a specific problem, a specific idea/method, and at least two \
       concrete findings). When in doubt because the material is too weak, \
       prefer false — a shorter digest of solid stories beats a longer digest \
       full of empty ones.
   If false, fill every other field with an empty string "".
2. If relevant, write EVERYTHING in English. Every field must be a complete, \
   naturally-connected sentence (or two) that could be read aloud and make \
   sense on its own — never a telegraphic fragment, never a bare noun phrase, \
   never generic filler. Target lengths: big_problem 20-28 words; \
   small_problem, idea and plan 30-42 words each; conclusion and \
   future_directions 35-50 words each.
3. "big_problem" = the big-picture industry problem this research addresses \
   (the BLUF), as one sharp, specific sentence — not a vague truism.
4. "small_problem" = the specific technical problem addressed by THIS study/ \
   article, with enough context to stand alone. "idea" = the proposed insight/ \
   approach, explained concretely (what did they actually build, test, or \
   propose?). "plan" = how it was tested or implemented (method, setup, scale).
5. The three results must each be a genuine, specific FINDING or OUTCOME of \
   this research — something the team measured, built, or demonstrated — never \
   a generic industry fact (e.g. overall market size or total sector output is \
   NOT a result). Each needs a real number or metric taken from the text \
   (percentage, improvement factor, cost, time, efficiency, scale...). If a \
   genuine result has no number attached in the text, leave result_N_number as \
   an EMPTY STRING "" — never invent or estimate one, and never write "N/A" or \
   similar there either. If you cannot identify at least TWO genuine, specific \
   results from the text, set is_engineering_relevant to false instead of \
   forcing weak ones.
6. NEVER write "N/A", "unknown", "not specified", "none", or similar placeholder \
   text in any field. Every field is either a real, substantive sentence, or — \
   only for result_N_number — an empty string.
7. "conclusion" = why this is a genuine innovation, compared against the state \
   of the art if mentioned in the text. "future_directions" = concrete next \
   steps, if indicated in the text.
8. Base yourself EXCLUSIVELY on the text provided. Do not add facts, numbers, \
   or names that do not appear in the text, even if you think you know them.
9. If the "Is this a preprint?" line below says yes, append to the end of \
   "conclusion" the sentence "Preliminary result, not yet peer-reviewed."
10. Score the finding on 7 dimensions, each an INTEGER from 0 to 100. Judge \
    strictly from the text provided — do not inflate a score just because the \
    source uses hype words like "breakthrough" or "revolutionary" without \
    proof to back them up.
    - evidence_score: how solid/verifiable the results are. 0-20 pure claim \
      with no data; 21-40 theory/simulation only; 41-60 proof of concept; \
      61-75 convincing experimental results; 76-90 solid results with \
      benchmarks/validation; 91-100 very strong, replicated or validated in \
      realistic conditions.
    - applicability_score: could an engineer realistically use this in a \
      project? 0-20 purely theoretical; 21-40 possible future application; \
      41-60 plausible but needs development; 61-80 concretely testable now; \
      81-100 realistically applicable today or very soon.
    - impact_score: how much this could change available technical \
      capabilities (performance, cost, efficiency, new capabilities). 0-20 \
      marginal improvement; 21-40 local optimization; 41-60 significant \
      improvement; 61-80 strong technological impact; 81-100 transformative/ \
      breakthrough potential.
    - cross_domain_score: how plausibly this principle/technique transfers to \
      OTHER engineering domains beyond its original one (materials, energy, \
      aerospace, robotics, electronics, manufacturing, etc. — real technical \
      connections, not keyword matching). 0-20 very narrow use; 21-40 few \
      reuses; 41-60 applicable in several contexts; 61-80 strong \
      transferability; 81-100 highly generalizable principle.
    - maturity_score: technology readiness, TRL-inspired. 0-20 TRL 1-2 \
      (concept only); 21-40 TRL 3-4 (proof of concept/lab validation); 41-60 \
      TRL 5-6 (relevant environment/prototype); 61-80 TRL 7-8 (operational \
      demonstration); 81-100 TRL 9 (proven, operational). A low score here is \
      NORMAL and fine for early-stage research — do not treat it as a flaw.
    - momentum_score: signs that activity around this is accelerating (rising \
      publications, follow-up work, adoption, related patents/repos/startups \
      MENTIONED IN THE TEXT — never invented). 0 if the text gives no signal \
      either way; otherwise judge from what's actually stated.
    - feasibility_score: economic/operational realism of implementing this \
      (cost, hardware/material/software availability, scalability, required \
      expertise). 0-20 practically not implementable; 21-40 very complex/ \
      costly; 41-60 possible with significant development; 61-80 \
      realistically implementable; 81-100 relatively easy to adopt/prototype.
"""

SYSTEM_RULES += (
    "\n\nReply with ONLY a valid JSON object, no text before or after, no ``` "
    "code blocks. It must have EXACTLY these keys:\n"
    + "\n".join(f"- {k}" for k in RESPONSE_SCHEMA["properties"])
)


# --------------------------------------------------------------------------- #
# Raccolta feed
# --------------------------------------------------------------------------- #

def clean_html(raw_html: str) -> str:
    """Toglie tag HTML e spazi ridondanti da un riassunto RSS."""
    if not raw_html:
        return ""
    text = BeautifulSoup(unescape(raw_html), "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def entry_published_dt(entry) -> dt.datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return dt.datetime(*parsed[:6], tzinfo=dt.timezone.utc)


def entry_image(entry) -> str | None:
    """Cerca un'immagine di anteprima nei campi tipici del feed RSS/Atom."""
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if media:
            url = media[0].get("url")
            if url:
                return url
    for link in entry.get("links", []):
        if str(link.get("type", "")).startswith("image/"):
            return link.get("href")
    if entry.get("links"):
        for link in entry["links"]:
            if link.get("rel") == "enclosure" and "image" in str(link.get("type", "")):
                return link.get("href")
    return None


def fetch_article_page(article_url: str) -> tuple[str | None, str]:
    """Recupera la pagina dell'articolo UNA sola volta ed estrae sia l'immagine
    og:image (di solito più grande e nitida della miniatura RSS) sia il TESTO
    INTEGRALE dell'articolo — non solo il breve riassunto del feed, che spesso
    è troppo povero per costruire un'analisi seria con BLUF/idea/piano/risultati."""
    try:
        resp = requests.get(article_url, headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        image_url = None
        tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if tag and tag.get("content"):
            image_url = tag["content"]

        for junk in soup(["script", "style", "nav", "footer", "header", "aside", "form", "iframe"]):
            junk.decompose()
        article_tag = soup.find("article")
        if article_tag:
            text = article_tag.get_text(" ", strip=True)
        else:
            paragraphs = soup.find_all("p")
            text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
        text = re.sub(r"\s+", " ", text).strip()

        return image_url, text[:9000]
    except requests.RequestException:
        return None, ""


def collect_from_source(source: dict, cutoff: dt.datetime) -> list[dict]:
    """Scarica un feed RSS/Atom (con timeout esplicito!) e restituisce gli item
    pubblicati dopo `cutoff`. IMPORTANTE: passiamo da `requests` con un timeout
    reale invece di lasciare che feedparser apra la connessione da solo, perché
    feedparser.parse(url) non ha un timeout di default e può restare bloccato
    a tempo indeterminato su una fonte lenta o che non risponde correttamente."""
    items = []
    try:
        resp = requests.get(source["url"], headers=REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            raise ValueError(str(parsed.bozo_exception))
    except Exception as exc:  # noqa: BLE001 - vogliamo continuare con le altre fonti
        print(f"[WARN] fonte non raggiungibile: {source['name']} ({source['url']}): {exc}", file=sys.stderr)
        return items

    is_arxiv = source["name"] == "arXiv"
    for entry in parsed.entries:
        published = entry_published_dt(entry)
        if not published or published < cutoff:
            continue
        url = entry.get("link")
        if not url:
            continue
        items.append(
            {
                "source_name": source["name"],
                "url": url,
                "title": clean_html(entry.get("title", "")),
                "summary": clean_html(entry.get("summary", entry.get("description", ""))),
                "published_at": published,
                "image_url": entry_image(entry),
                "is_preprint": is_arxiv,
            }
        )
    return items


def collect_from_semantic_scholar(cutoff: dt.datetime) -> list[dict]:
    """Semantic Scholar: paper scientifici veri (con abstract), non notizie di
    sintesi — API pubblica gratuita, nessuna chiave richiesta per il nostro
    volume di utilizzo."""
    items = []
    date_from = cutoff.strftime("%Y-%m-%d")
    date_to = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    params = {
        "query": "engineering",
        "fields": "title,abstract,url,externalIds,publicationDate,venue",
        "fieldsOfStudy": "Engineering,Materials Science",
        "publicationDateOrYear": f"{date_from}:{date_to}",
        "sort": "publicationDate:desc",
        "limit": 30,
    }
    try:
        resp = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
            params=params, headers=REQUEST_HEADERS, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
    except (requests.RequestException, ValueError) as exc:
        print(f"[WARN] fonte non raggiungibile: Semantic Scholar: {exc}", file=sys.stderr)
        return items

    for paper in data:
        pub_date = paper.get("publicationDate")
        if not pub_date:
            continue
        try:
            published = dt.datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if published < cutoff:
            continue
        url = paper.get("url")
        doi = (paper.get("externalIds") or {}).get("DOI")
        if not url and doi:
            url = f"https://doi.org/{doi}"
        if not url or not paper.get("title"):
            continue
        items.append({
            "source_name": "Semantic Scholar",
            "url": url,
            "title": clean_html(paper.get("title") or ""),
            "summary": clean_html(paper.get("abstract") or ""),
            "published_at": published,
            "image_url": None,
            "is_preprint": not bool(paper.get("venue")),  # nessuna rivista/venue = probabile preprint
        })
    return items


def reconstruct_openalex_abstract(inverted_index: dict | None) -> str:
    """OpenAlex non fornisce l'abstract come testo semplice ma come 'inverted
    index' (parola -> lista di posizioni): lo ricostruiamo in testo leggibile."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions[i] = word
    return " ".join(positions[i] for i in sorted(positions))


def collect_from_openalex(cutoff: dt.datetime) -> list[dict]:
    """OpenAlex: catalogo aperto di oltre 480 milioni di lavori scientifici.
    Dal 2026 richiede una chiave API gratuita (OPENALEX_API_KEY): se non è
    impostata, la fonte viene saltata senza bloccare il resto della raccolta."""
    items = []
    if not OPENALEX_API_KEY:
        print("[WARN] OPENALEX_API_KEY non impostata: fonte OpenAlex saltata.", file=sys.stderr)
        return items

    params = {
        "search": "engineering",
        "filter": f"from_publication_date:{cutoff.strftime('%Y-%m-%d')}",
        "sort": "publication_date:desc",
        "per-page": 30,
        "api_key": OPENALEX_API_KEY,
    }
    if OPENALEX_CONTACT_EMAIL:
        params["mailto"] = OPENALEX_CONTACT_EMAIL
    try:
        resp = requests.get("https://api.openalex.org/works", params=params, headers=REQUEST_HEADERS, timeout=20)
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except (requests.RequestException, ValueError) as exc:
        print(f"[WARN] fonte non raggiungibile: OpenAlex: {exc}", file=sys.stderr)
        return items

    for work in results:
        pub_date = work.get("publication_date")
        if not pub_date:
            continue
        try:
            published = dt.datetime.strptime(pub_date, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if published < cutoff:
            continue
        url = (work.get("primary_location") or {}).get("landing_page_url") or work.get("id")
        title = work.get("title") or work.get("display_name") or ""
        if not url or not title:
            continue
        items.append({
            "source_name": "OpenAlex",
            "url": url,
            "title": clean_html(title),
            "summary": clean_html(reconstruct_openalex_abstract(work.get("abstract_inverted_index"))),
            "published_at": published,
            "image_url": None,
            "is_preprint": work.get("type") == "preprint",
        })
    return items


def collect_all(cutoff: dt.datetime) -> list[dict]:
    all_items = []
    for source in SOURCES:
        items = collect_from_source(source, cutoff)
        print(f"[INFO] {source['name']}: {len(items)} candidati da {source['url']}")
        all_items.append(items)
        # arXiv chiede gentilmente almeno ~3s tra le richieste alla loro API
        if source["name"] == "arXiv":
            time.sleep(3)

    ss_items = collect_from_semantic_scholar(cutoff)
    print(f"[INFO] Semantic Scholar: {len(ss_items)} candidati")
    all_items.append(ss_items)

    oa_items = collect_from_openalex(cutoff)
    print(f"[INFO] OpenAlex: {len(oa_items)} candidati")
    all_items.append(oa_items)

    return [item for group in all_items for item in group]


def dedupe(items: list[dict], already_seen_urls: set[str]) -> list[dict]:
    fresh, seen_titles = [], set()
    for item in items:
        title_key = item["title"].strip().lower()
        if item["url"] in already_seen_urls or title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        fresh.append(item)
    return fresh


# --------------------------------------------------------------------------- #
# Gemini: filtro di pertinenza + strutturazione
# --------------------------------------------------------------------------- #

def build_user_prompt(item: dict, article_text: str) -> str:
    # Usa il testo integrale della pagina se lo abbiamo recuperato ed è
    # sostanzioso; altrimenti ripiega sul riassunto del feed RSS (meglio
    # di niente, ma il modello viene comunque istruito a segnare "non
    # pertinente" se il materiale resta troppo povero per un'analisi vera).
    body = article_text if len(article_text) > len(item["summary"]) + 200 else item["summary"]
    preprint_line = "Yes" if item.get("is_preprint") else "No"
    return (
        f"Source: {item['source_name']}\n"
        f"Original title: {item['title']}\n"
        f"Is this a preprint (not yet peer-reviewed)? {preprint_line}\n"
        f"Article text:\n{body[:7000]}\n\n"
        "Return the required JSON following exactly the schema and rules "
        "from the system prompt."
    )


def call_gemini(item: dict, article_text: str) -> dict | None:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY non impostata nell'ambiente.")

    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_RULES}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(item, article_text)}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    for attempt in range(2):
        try:
            resp = requests.post(GEMINI_URL, headers=headers, json=body, timeout=20)
            if resp.status_code == 429:
                wait = 6 * (attempt + 1)
                print(f"[WARN] rate limit Gemini, aspetto {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                # Log esplicito di status + corpo risposta: è la parte più utile
                # per capire SUBITO se è un problema di chiave, di modello o di
                # quota, invece di scoprirlo solo dopo minuti di silenzio.
                print(
                    f"[WARN] Gemini {resp.status_code} per {item['url']}: {resp.text[:300]}",
                    file=sys.stderr,
                )
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (requests.RequestException, KeyError, json.JSONDecodeError) as exc:
            print(f"[WARN] chiamata Gemini fallita ({item['url']}): {exc}", file=sys.stderr)
            time.sleep(2)
    return None


def call_groq(item: dict, article_text: str) -> dict | None:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY non impostata nell'ambiente.")

    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": build_user_prompt(item, article_text)},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_API_KEY}"}

    for attempt in range(2):
        try:
            resp = requests.post(GROQ_URL, headers=headers, json=body, timeout=20)
            if resp.status_code == 429:
                wait = 6 * (attempt + 1)
                print(f"[WARN] rate limit Groq, aspetto {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                print(
                    f"[WARN] Groq {resp.status_code} per {item['url']}: {resp.text[:300]}",
                    file=sys.stderr,
                )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return json.loads(text)
        except (requests.RequestException, KeyError, IndexError, json.JSONDecodeError) as exc:
            print(f"[WARN] chiamata Groq fallita ({item['url']}): {exc}", file=sys.stderr)
            time.sleep(2)
    return None


def call_llm(item: dict, article_text: str) -> dict | None:
    """Dispatcher: usa il motore scelto in LLM_PROVIDER senza cambiare il resto
    della pipeline."""
    if LLM_PROVIDER == "gemini":
        return call_gemini(item, article_text)
    return call_groq(item, article_text)


NUMBER_RE = re.compile(r"\d[\d.,]*")


PLACEHOLDER_VALUES = {"", "n/a", "na", "none", "unknown", "not specified", "unavailable", "tbd"}


def guard_against_invented_numbers(structured: dict, source_text: str) -> dict:
    """Controllo di sicurezza extra: se un numero indicato in result_N_number
    non compare nel testo originale, lo sostituisce con la dicitura neutra
    invece di lasciare passare un possibile numero inventato dal modello."""
    haystack = source_text.replace(",", ".")
    for i in (1, 2, 3):
        key = f"result_{i}_number"
        value = structured.get(key, "")
        digits = NUMBER_RE.findall(value.replace(",", "."))
        if digits and not any(d in haystack for d in digits):
            structured[key] = ""
    return structured


def is_substantive(structured: dict) -> tuple[bool, str]:
    """Controllo di qualità: rifiuta strutture con campi vuoti o segnaposto
    tipo 'N/A' — meglio scartare una notizia che pubblicarla senza contenuto
    reale. Ritorna (ok, motivo_se_scartata)."""

    def has_content(key: str, min_words: int = 4) -> bool:
        val = str(structured.get(key, "")).strip()
        if val.lower() in PLACEHOLDER_VALUES:
            return False
        return len(val.split()) >= min_words

    required = ["title", "big_problem", "small_problem", "idea", "plan", "conclusion"]
    for key in required:
        if not has_content(key, min_words=3 if key == "title" else 5):
            return False, f"campo '{key}' vuoto o troppo generico"

    solid_results = 0
    for i in (1, 2, 3):
        headline = str(structured.get(f"result_{i}_headline", "")).strip()
        detail = str(structured.get(f"result_{i}_detail", "")).strip()
        if (
            headline.lower() not in PLACEHOLDER_VALUES
            and detail.lower() not in PLACEHOLDER_VALUES
            and len(detail.split()) >= 4
        ):
            solid_results += 1
    if solid_results < 2:
        return False, f"solo {solid_results}/3 risultati con contenuto reale (minimo 2)"

    return True, ""


def structure_item(item: dict) -> tuple[dict | None, bool]:
    """Ritorna (brief, chiamata_fallita).
    brief è None se la notizia non è pertinente, se il contenuto risulta
    troppo povero per un'analisi seria, o se la chiamata all'LLM è fallita;
    chiamata_fallita è True SOLO in quest'ultimo caso, ed è il segnale che
    main() usa per il circuit breaker."""
    image_url, article_text = fetch_article_page(item["url"])

    structured = call_llm(item, article_text)
    if structured is None:
        return None, True
    if not structured.get("is_engineering_relevant"):
        return None, False

    ok, reason = is_substantive(structured)
    if not ok:
        print(f"[INFO] Scartata (contenuto insufficiente: {reason}): {item['title'][:70]}", file=sys.stderr)
        return None, False

    haystack = item["title"] + " " + item["summary"] + " " + article_text
    structured = guard_against_invented_numbers(structured, haystack)

    brief = {
        "title": structured.get("title") or item["title"],
        "source_name": item["source_name"],
        "source_url": item["url"],
        "image_url": image_url or item["image_url"],
        "category": structured.get("category") or "Other Engineering",
        "published_at": item["published_at"].isoformat(),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "is_preprint": item["is_preprint"],
        "big_problem": structured.get("big_problem", ""),
        "small_problem": structured.get("small_problem", ""),
        "idea": structured.get("idea", ""),
        "plan": structured.get("plan", ""),
        "result_1_headline": structured.get("result_1_headline", ""),
        "result_1_number": structured.get("result_1_number", ""),
        "result_1_detail": structured.get("result_1_detail", ""),
        "result_2_headline": structured.get("result_2_headline", ""),
        "result_2_number": structured.get("result_2_number", ""),
        "result_2_detail": structured.get("result_2_detail", ""),
        "result_3_headline": structured.get("result_3_headline", ""),
        "result_3_number": structured.get("result_3_number", ""),
        "result_3_detail": structured.get("result_3_detail", ""),
        "conclusion": structured.get("conclusion", ""),
        "future_directions": structured.get("future_directions", ""),
    }

    score, sub_scores = compute_openmind_score(structured)
    brief["score"] = score
    brief.update(sub_scores)

    return brief, False


# --------------------------------------------------------------------------- #
# Persistenza
# --------------------------------------------------------------------------- #

def load_existing() -> list[dict]:
    if not os.path.exists(OUTPUT_PATH):
        return []
    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def merge_and_prune(existing: list[dict], new_briefs: list[dict]) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    retain_cutoff = now - dt.timedelta(hours=RETAIN_WINDOW_HOURS)

    by_url = {b["source_url"]: b for b in existing}
    for b in new_briefs:
        by_url[b["source_url"]] = b

    merged = [
        b for b in by_url.values()
        if dt.datetime.fromisoformat(b["published_at"]) >= retain_cutoff
    ]
    merged.sort(key=lambda b: (b.get("score", 0), b["published_at"]), reverse=True)
    # Pubblichiamo sempre e solo le migliori PUBLISH_TOP_N per OpenMind Score:
    # una notizia con punteggio più alto arrivata in un run successivo può
    # quindi "scalzare" una già pubblicata con punteggio più basso.
    return merged[:PUBLISH_TOP_N]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    collect_cutoff = now - dt.timedelta(hours=COLLECT_WINDOW_HOURS)

    existing = load_existing()
    already_seen_urls = {b["source_url"] for b in existing}

    raw_items = collect_all(collect_cutoff)
    fresh_items = dedupe(raw_items, already_seen_urls)
    fresh_items.sort(key=lambda it: it["published_at"], reverse=True)
    fresh_items = fresh_items[:MAX_ITEMS_PER_RUN]
    print(f"[INFO] {len(fresh_items)} articoli da valutare con Gemini (limite {MAX_ITEMS_PER_RUN}/run).")

    new_briefs = []
    consecutive_failures = 0
    for i, item in enumerate(fresh_items, start=1):
        print(f"[INFO] ({i}/{len(fresh_items)}) elaboro: {item['title'][:70]}...")
        try:
            brief, call_failed = structure_item(item)
        except Exception as exc:  # noqa: BLE001 - un errore imprevisto su UN articolo non deve fermare l'intero run
            print(f"[WARN] Errore imprevisto su questo articolo, lo salto: {exc}", file=sys.stderr)
            brief, call_failed = None, True

        if call_failed:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"[ERROR] {MAX_CONSECUTIVE_FAILURES} chiamate a Gemini fallite di fila: "
                    "interrompo il run invece di continuare a vuoto. Guarda la riga "
                    "'[WARN] Gemini ...' qui sopra per il motivo esatto (chiave non "
                    "valida, quota esaurita, modello non disponibile, ecc.).",
                    file=sys.stderr,
                )
                break
        else:
            consecutive_failures = 0
            if brief:
                new_briefs.append(brief)

        time.sleep(1.5)  # margine di cortesia sui rate limit del free tier

    merged = merge_and_prune(existing, new_briefs)
    save_json(OUTPUT_PATH, merged)

    print(f"[INFO] {len(new_briefs)} notizie pertinenti E sostanziali su {len(fresh_items)} valutate "
          f"(le altre sono state scartate per pertinenza o qualità insufficiente — vedi i log [INFO]/[WARN] sopra).")
    print(f"[INFO] Pubblicate le migliori {len(merged)} per OpenMind Score (tetto: {PUBLISH_TOP_N}) in {OUTPUT_PATH}.")


if __name__ == "__main__":
    main()
