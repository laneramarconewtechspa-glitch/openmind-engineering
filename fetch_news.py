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

OUTPUT_PATH = os.path.join("docs", "news.json")

# Finestra usata per RACCOGLIERE i candidati (con margine di sicurezza rispetto
# alle 24h "vere", per non perdere nulla tra due sync consecutivi).
COLLECT_WINDOW_HOURS = 30
# Finestra usata per TENERE gli elementi già pubblicati sul sito prima di
# eliminarli dal file (il frontend applica comunque il filtro "ultime 24h" a
# runtime, quindi questo è solo un margine per evitare buchi tra due sync).
RETAIN_WINDOW_HOURS = 48
# Numero massimo di notizie VALUTATE per ogni esecuzione (non tutte verranno
# pubblicate: molte saranno scartate dal controllo qualità se il contenuto
# risulta troppo povero — per questo il numero è più alto di quante notizie
# ci si aspetta effettivamente in output).
MAX_ITEMS_PER_RUN = 16
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
    },
    "required": [
        "is_engineering_relevant", "category", "title", "big_problem", "small_problem",
        "idea", "plan",
        "result_1_headline", "result_1_number", "result_1_detail",
        "result_2_headline", "result_2_number", "result_2_detail",
        "result_3_headline", "result_3_number", "result_3_detail",
        "conclusion", "future_directions",
    ],
}

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
9. If the source is arXiv (preprint), append to the end of "conclusion" the \
   sentence "Preliminary result, not yet peer-reviewed."
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


def collect_all(cutoff: dt.datetime) -> list[dict]:
    all_items = []
    for source in SOURCES:
        items = collect_from_source(source, cutoff)
        print(f"[INFO] {source['name']}: {len(items)} candidati da {source['url']}")
        all_items.append(items)
        # arXiv chiede gentilmente almeno ~3s tra le richieste alla loro API
        if source["name"] == "arXiv":
            time.sleep(3)
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
    return (
        f"Source: {item['source_name']}\n"
        f"Original title: {item['title']}\n"
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
        "title": structured["title"] or item["title"],
        "source_name": item["source_name"],
        "source_url": item["url"],
        "image_url": image_url or item["image_url"],
        "category": structured["category"],
        "published_at": item["published_at"].isoformat(),
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "is_preprint": item["is_preprint"],
        "big_problem": structured["big_problem"],
        "small_problem": structured["small_problem"],
        "idea": structured["idea"],
        "plan": structured["plan"],
        "result_1_headline": structured["result_1_headline"],
        "result_1_number": structured["result_1_number"],
        "result_1_detail": structured["result_1_detail"],
        "result_2_headline": structured["result_2_headline"],
        "result_2_number": structured["result_2_number"],
        "result_2_detail": structured["result_2_detail"],
        "result_3_headline": structured["result_3_headline"],
        "result_3_number": structured["result_3_number"],
        "result_3_detail": structured["result_3_detail"],
        "conclusion": structured["conclusion"],
        "future_directions": structured["future_directions"],
    }
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
    merged.sort(key=lambda b: b["published_at"], reverse=True)
    return merged


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
        brief, call_failed = structure_item(item)

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

    print(f"[INFO] Salvate {len(new_briefs)} notizie pertinenti E sostanziali su {len(fresh_items)} valutate "
          f"(le altre sono state scartate per pertinenza o qualità insufficiente — vedi i log [INFO]/[WARN] sopra).")
    print(f"[INFO] Totale notizie in {OUTPUT_PATH}: {len(merged)}.")


if __name__ == "__main__":
    main()
