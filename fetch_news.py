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
# Numero massimo di notizie elaborate (e quindi chiamate a Gemini) per ogni
# esecuzione: tiene il digest snello e limita tempo/costi per run.
MAX_ITEMS_PER_RUN = 10
# Se queste chiamate a Gemini falliscono di fila, il run si interrompe subito
# invece di ritentare inutilmente su tutte le notizie rimaste.
MAX_CONSECUTIVE_FAILURES = 3

REQUEST_HEADERS = {"User-Agent": "OpenMindEngineeringBot/1.0 (+personal news digest)"}

CATEGORIES = [
    "Robotica",
    "Aerospaziale",
    "Energia",
    "Civile e Infrastrutture",
    "Meccanica",
    "Elettronica e Semiconduttori",
    "Materiali",
    "Biomedica",
    "Informatica e Computing",
    "Altro Ingegneria",
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
    {"name": "EurekAlert!", "url": "https://www.eurekalert.org/specialtopic/tech/home"},
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

SYSTEM_RULES = """Sei un analista tecnico che prepara un digest quotidiano di notizie di \
ingegneria per lettori esperti ma di fretta. Ricevi il titolo e il contenuto di UN \
solo articolo/paper e devi restituire SOLO il JSON richiesto dallo schema, seguendo \
queste regole ferree:

1. Valuta per primo "is_engineering_relevant": false se la notizia riguarda \
   salute pura, policy pura, scienza di base o business senza una chiara \
   applicazione o innovazione ingegneristica; in quel caso compila gli altri \
   campi con stringa vuota "".
2. Se è pertinente riporta il tutto con linguaggio specifico, professionale e scientifico: periodi brevi e \
   diretti, ma che abbiano come obiettivo quello di far capire il contenuto dell'articolo \
   in pochi minuti di lettura e che sia qualcosa che il lettore può raccontare (conclusion e future_directions \
   possono essere leggermente più articolate).
3. Nel BLUF deve essere presente da "big_problem" a "small_problem", da "small_problem" ad "idea" da "idea" a summary.\
   "big_problem" = il macro-problema di settore che questa ricerca affronta spiegato in maniera esaustiva, ma comunque breve\
4. "small_problem" = il problema tecnico specifico affrontato da QUESTO \
   studio/articolo. "idea" = l'intuizione/approccio proposto. "plan" = come \
   è stato testato o implementato.
5. I tre risultati (result_1/2/3) devono avere un numero o una metrica REALE \
   presa dal testo fornito (percentuale, fattore di miglioramento, costo, \
   tempo, efficienza...). Se un numero non è nel testo fornito, scrivi ESATTAMENTE \
   "dato non specificato nella fonte" nel campo _number corrispondente: non \
   inventarlo e non stimarlo mai. Fai si però che i dati siano effettivamente numerici o comunque quantificabili in maniera che il lettore \
   abbia un'idea sul perchè questi risultati sono importanti.
6. "conclusion" = perché è un'innovazione reale, con confronto allo stato \
   dell'arte se è menzionato nel testo. "future_directions" = i prossimi passi, \
   se indicati.
7. Basati ESCLUSIVAMENTE sul testo fornito. Non aggiungere fatti, numeri o nomi \
   che non compaiono nel testo, anche se pensi di conoscerli.
8. Se la fonte è arXiv (preprint), aggiungi alla fine di "conclusion" la frase \
   "Risultato preliminare, non ancora sottoposto a peer review."
"""

SYSTEM_RULES += (
    "\n\nRispondi SOLO con un oggetto JSON valido, senza testo prima o dopo, senza "
    "blocchi ```. Deve avere ESATTAMENTE queste chiavi:\n"
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


def fetch_og_image(article_url: str) -> str | None:
    """Fallback: recupera la pagina dell'articolo ed estrae il meta og:image."""
    try:
        resp = requests.get(article_url, headers=REQUEST_HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
        if tag and tag.get("content"):
            return tag["content"]
    except requests.RequestException:
        pass
    return None


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

# Modifica effettuata sul limite del summary, aumento a 8000 piuttosto che 2500

# def build_user_prompt(item: dict) -> str:
 #   return (
 #       f"Fonte: {item['source_name']}\n"
 #       f"Titolo originale: {item['title']}\n"
  #      f"Riassunto/abstract originale: {item['summary'][:2500]}\n\n"
 #       "Restituisci il JSON richiesto seguendo esattamente lo schema e le regole "
 #       "del system prompt."
 #   )


def build_user_prompt(item: dict) -> str:
    return (
        f"Fonte: {item['source_name']}\n"
        f"Titolo originale: {item['title']}\n"
        f"Riassunto/abstract originale: str({item.get('content', item.get('summary', '')))[:8000]}\n\n"
        "Restituisci il JSON richiesto seguendo esattamente lo schema e le regole "
        "del system prompt."
    )


def call_gemini(item: dict) -> dict | None:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY non impostata nell'ambiente.")

    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_RULES}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(item)}]}],
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


def call_groq(item: dict) -> dict | None:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY non impostata nell'ambiente.")

    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": build_user_prompt(item)},
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


def call_llm(item: dict) -> dict | None:
    """Dispatcher: usa il motore scelto in LLM_PROVIDER senza cambiare il resto
    della pipeline."""
    if LLM_PROVIDER == "gemini":
        return call_gemini(item)
    return call_groq(item)


NUMBER_RE = re.compile(r"\d[\d.,]*")


def guard_against_invented_numbers(structured: dict, source_text: str) -> dict:
    """Controllo di sicurezza extra: se un numero indicato in result_N_number
    non compare nel testo originale, lo sostituisce con la dicitura neutra
    invece di lasciare passare un possibile numero inventato dal modello."""
    haystack = source_text.replace(",", ".")
    for i in (1, 2, 3):
        key = f"result_{i}_number"
        value = str(structured.get(key, "")) #eventualmente rimuovere str
        digits = NUMBER_RE.findall(value.replace(",", "."))
        if digits and not any(d in haystack for d in digits):
            structured[key] = "dato non specificato nella fonte"
    return structured


def structure_item(item: dict) -> tuple[dict | None, bool]:
    """Ritorna (brief, chiamata_fallita).
    brief è None sia se la notizia non è pertinente sia se la chiamata a
    Gemini è fallita; chiamata_fallita è True SOLO nel secondo caso, ed è
    il segnale che main() usa per il circuit breaker."""
    structured = call_llm(item)
    if structured is None:
        return None, True
    if not structured.get("is_engineering_relevant"):
        return None, False
    structured = guard_against_invented_numbers(structured, item["title"] + " " + item["summary"])

    image_url = item["image_url"] or fetch_og_image(item["url"])
    brief = {
        "title": structured["title"] or item["title"],
        "source_name": item["source_name"],
        "source_url": item["url"],
        "image_url": image_url,
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

    print(f"[INFO] Salvate {len(new_briefs)} nuove notizie pertinenti su {len(fresh_items)} valutate.")
    print(f"[INFO] Totale notizie in {OUTPUT_PATH}: {len(merged)}.")


if __name__ == "__main__":
    main()
