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

# Su Windows la console usa di default una codifica legacy (es. cp1252) che
# manda in crash il processo su qualunque titolo con caratteri tipografici
# (trattini "‑", virgolette curve, ecc. — comunissimi nei feed RSS). Forziamo
# stdout/stderr in UTF-8 così i log non fanno mai fallire lo script.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --------------------------------------------------------------------------- #
# Configurazione
# --------------------------------------------------------------------------- #

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Modelli di riserva, in ordine: la quota gratuita di Gemini è PER MODELLO e
# i modelli vengono ritirati spesso (404). Se il principale esaurisce la quota
# giornaliera o sparisce, il run passa al successivo invece di fermarsi.
GEMINI_FALLBACK_MODELS = [
    m.strip()
    for m in os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-3.5-flash").split(",")
    if m.strip() and m.strip() != GEMINI_MODEL
]
GEMINI_MODELS = [GEMINI_MODEL] + GEMINI_FALLBACK_MODELS

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# "gemini" (default) o "groq" — permette di cambiare motore senza toccare il
# resto della pipeline, cambiando solo questa variabile d'ambiente nel workflow.
# Gemini è il default perché impone lo schema JSON a livello di API (tipi/bool/
# enum obbligatori tramite responseSchema), mentre Groq in json_object mode
# forza solo "JSON valido" senza garantire i campi: con modelli come
# openai/gpt-oss-120b questo produceva giudizi di pertinenza inaffidabili
# (is_engineering_relevant sempre false anche su articoli validi).
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()

# Da febbraio 2026 OpenAlex richiede una chiave API (gratuita, $1 di credito al
# giorno — ampiamente sufficiente per il nostro utilizzo). Se non è impostata,
# la fonte OpenAlex viene semplicemente saltata invece di bloccare il resto.
OPENALEX_API_KEY = os.environ.get("OPENALEX_API_KEY")
OPENALEX_CONTACT_EMAIL = os.environ.get("OPENALEX_CONTACT_EMAIL", "")

OUTPUT_PATH = os.path.join("docs", "news.json")

# Finestra usata per RACCOGLIERE i candidati (con margine di sicurezza rispetto
# alle 24h "vere", per non perdere nulla se uno o più sync vengono ritardati o
# saltati: grazie alla cache degli URL già visti, allargarla non costa
# chiamate LLM in più — si valutano comunque solo gli articoli nuovi).
COLLECT_WINDOW_HOURS = 36
# Finestra usata per TENERE gli elementi già pubblicati sul sito prima di
# eliminarli dal file: 24h "oggi" + 24h "ieri" (sezione Archive del sito).
RETAIN_WINDOW_HOURS = 48
# Il frontend (WINDOW_HOURS in docs/script.js) mostra come "oggi" solo gli
# articoli pubblicati nelle ultime 24h: qui serve per selezionare le due
# fasce (oggi / ieri) SEPARATAMENTE. Tenere allineato a docs/script.js.
FRESH_WINDOW_HOURS = 24
# Numero massimo di notizie VALUTATE per ogni esecuzione: deve essere
# abbastanza alto da non tagliare nessuna fonte prima ancora di darle una
# possibilità (in pratica quasi mai raggiunto tutto). Non è il numero di
# notizie pubblicate: quello è deciso da PUBLISH_TOP_N qui sotto, in base
# all'OpenMind Score.
MAX_ITEMS_PER_RUN = 180
# Quante notizie MOSTRA il sito per ciascuna vista (oggi / ieri): sempre e
# solo le migliori per OpenMind Score. Tenere allineato a MAX_PAPERS in
# docs/script.js quando lo si cambia (es. da 10 a 15).
PUBLISH_TOP_N = 10
# In news.json si conserva però una RISERVA più ampia di articoli "freschi"
# (<24h): tra un sync e l'altro gli articoli invecchiano e escono dalla vista
# "oggi" del sito, quindi per averne sempre almeno PUBLISH_TOP_N a schermo a
# qualunque ora ne servono di più di quelli mostrati in un dato istante.
FRESH_POOL_SIZE = PUBLISH_TOP_N * 3
ARCHIVE_POOL_SIZE = PUBLISH_TOP_N
# Fallimenti "veri" di fila (dopo tutti i retry interni) oltre i quali il run
# si ferma. Gli errori temporanei (503, timeout, rate limit al minuto) NON
# fanno interrompere un run alla prima serie sfortunata: vengono ritentati
# in un secondo passaggio a fine run. Gli errori permanenti (chiave non
# valida, quota giornaliera di tutti i modelli finita) fermano invece subito.
MAX_CONSECUTIVE_FAILURES = 8
RETRY_PASS_COOLDOWN_SECONDS = 60

# Cache degli URL già valutati (anche scartati): senza, ogni sync rivaluta
# tutti gli articoli ancora nella finestra di raccolta, sprecando circa metà
# della quota LLM giornaliera. Si può ignorare con la variabile d'ambiente
# IGNORE_SEEN=1 (utile per test/riprove forzate).
SEEN_PATH = os.path.join("data", "seen_urls.json")
SEEN_RETAIN_HOURS = 96

# Contatori CUMULATIVI (mai potati, a differenza di SEEN_PATH): quanti articoli
# sono stati davvero mandati all'LLM da quando il progetto esiste, e quanti di
# questi sono risultati pertinenti / hanno superato il controllo qualità per
# l'analisi completa. Alimentano l'indicatore "ARTICLES ANALYZED" e il
# "funnel" nel pannello espanso del footer: numeri che hanno senso solo se
# crescono sempre, non se si azzerano o oscillano run dopo run.
TOTAL_ANALYZED_PATH = os.path.join("data", "total_analyzed.json")

# Manifest letto dal frontend per i 3 indicatori in home e per il pannello
# "BITL Score": fonti realmente configurate e tassonomia delle categorie.
# Generato a ogni run così resta sempre sincronizzato con SOURCES/CATEGORIES
# qui sotto, senza dover duplicare a mano l'elenco in docs/script.js.
STATS_OUTPUT_PATH = os.path.join("docs", "stats.json")

# --------------------------------------------------------------------------- #
# Flash News: striscia leggera (titolo + sintesi, niente analisi BLUF/score)
# per candidati pertinenti ma esclusi dalla top PUBLISH_TOP_N — scartati per
# contenuto troppo leggero per l'analisi completa, o sostanziosi ma fuori
# classifica per punteggio. Non toccano mai la pipeline di news.json sopra.
# --------------------------------------------------------------------------- #
FLASH_OUTPUT_PATH = os.path.join("docs", "flash.json")
# Più larga/capiente della top 10: le flash costano una frazione di una
# chiamata LLM ciascuna (batch da FLASH_BATCH_SIZE) e non richiedono lo stesso
# turnover stretto delle notizie con analisi completa.
FLASH_RETAIN_WINDOW_HOURS = 72
FLASH_MAX_ITEMS = 30
FLASH_BATCH_SIZE = 10

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
    # Tech Xplore disattivata: protetta da anti-bot Cloudflare, ogni fetch
    # dell'articolo tornava 403 e quindi al modello arrivava solo il breve
    # summary RSS — sprecava valutazioni senza mai produrre contenuto
    # pubblicabile (vedi anche phys.org, stessa rete Science X, stesso blocco).
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/type/news.rss"},
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/topic/robotics.rss"},
    {"name": "IEEE Spectrum", "url": "https://spectrum.ieee.org/feeds/topic/aerospace.rss"},
    {"name": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/"},
    {"name": "NSF", "url": "https://www.nsf.gov/rss/rss_www_news.xml"},
    # Fonti aggiunte per ampliare il ventaglio dopo la disattivazione di Tech
    # Xplore: tutte verificate a mano (feed valido + articolo scaricabile con
    # testo reale, non teaser/paywall/blocco anti-bot) prima di inserirle qui.
    {"name": "New Atlas", "url": "https://newatlas.com/index.rss"},
    {"name": "MIT News", "url": "https://news.mit.edu/rss/feed"},
    {"name": "The Robot Report", "url": "https://www.therobotreport.com/feed/"},
    {"name": "SpaceNews", "url": "https://spacenews.com/feed/"},
    {"name": "EE Times", "url": "https://www.eetimes.com/feed/"},
    {"name": "Engineering.com", "url": "https://www.engineering.com/feed/"},
    {"name": "Renewable Energy World", "url": "https://www.renewableenergyworld.com/feed/"},
    {"name": "Power Engineering", "url": "https://www.power-eng.com/feed/"},
    # Seconda ondata di fonti, per coprire categorie ancora scoperte (civile,
    # biomedicale, computing) — stesso criterio: verificate a mano una per una.
    {"name": "New Civil Engineer", "url": "https://www.newcivilengineer.com/feed/"},
    {"name": "Global Construction Review", "url": "https://www.globalconstructionreview.com/feed/"},
    {"name": "Medical Design & Outsourcing", "url": "https://www.medicaldesignandoutsourcing.com/feed/"},
    # The Register e Data Center Dynamics tolte dopo un run di prova: 38
    # valutazioni combinate, 0 notizie pubblicate — sono editorialmente
    # business/cybersecurity/immobiliare data-center, non ricerca
    # ingegneristica, quindi sprecavano solo quota LLM senza mai passare
    # il filtro di pertinenza (non un caso di "oggi non c'era nulla").
    {"name": "Semiconductor Engineering", "url": "https://semiengineering.com/feed/"},
    {"name": "Electronics Weekly", "url": "https://www.electronicsweekly.com/feed/"},
    {"name": "NASASpaceflight", "url": "https://www.nasaspaceflight.com/feed/"},
    {"name": "Space.com", "url": "https://www.space.com/feeds/all"},
    # Terza ondata: più volume in ingresso mantenendo i criteri stringenti
    # (obiettivo dell'utente: riempire la top 10 tutti i giorni con più
    # candidati, non abbassando ulteriormente la qualità). Molti sono altri
    # feed per categoria dello stesso dominio ScienceDaily, già verificato
    # affidabile (nessun blocco anti-bot) dalle 3 fonti SD già in uso sopra —
    # qui coprono soprattutto le categorie più scoperte (Biomedical,
    # Computing, Materials). Bioengineer.org e SD Space Exploration esclusi:
    # il primo era fuori tema/troppo corto nel test, il secondo avrebbe solo
    # ingrossato ulteriormente l'Aerospace, già la categoria più coperta.
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/matter_energy/electronics.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/matter_energy/nanotechnology.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/matter_energy/energy_and_resources.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/matter_energy/materials_science.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/computers_math/computer_science.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/health_medicine/medical_devices.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/plants_animals/biotechnology.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/earth_climate/environmental_science.xml"},
    {"name": "MD+DI", "url": "https://www.mddionline.com/rss.xml"},
    {"name": "Drug Delivery Business News", "url": "https://www.drugdeliverybusiness.com/feed/"},
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

1. Evaluate "is_engineering_relevant" first. This flags TOPIC fit only — a \
   separate, later step in our own pipeline (not something you do) decides \
   whether the material is rich enough for a full write-up or only a short \
   blurb, so do NOT reject a story just because the text is short or limited: \
   set it to false ONLY in these cases:
   (a) the story has no genuine engineering/technology angle at all — pure \
       entertainment/celebrity/pop-culture, pure astronomy or space imagery \
       with no engineering-project angle, generic corporate M&A/financial \
       news with no described technology, pure health/medicine with no \
       device/engineering component, or pure policy/politics with nothing \
       technical in it;
   (b) there is truly close to NO usable text at all (e.g. the page failed to \
       load and only a one-line title is available) — not just "short", but \
       genuinely too little to honestly say anything factual about it.
   When a story IS a real engineering/technology story — a new device, \
   material, method, infrastructure project, funding for a technical effort, \
   an industry/technical forecast, etc. — set it to TRUE even if the \
   available text is brief or you can only identify one concrete detail: \
   write whatever honest, specific sentences the material actually supports, \
   and leave weaker fields shorter rather than inventing padding. Prefer TRUE \
   whenever there's a genuine technical subject, even if thin.
   If false, fill every other field with an empty string "".
2. If relevant, write EVERYTHING in English. Every field must be a complete, \
   naturally-connected sentence (or two) that could be read aloud and make \
   sense on its own — never a telegraphic fragment, never a bare noun phrase, \
   never generic filler. Target lengths: big_problem 20-28 words; \
   small_problem, idea and plan 30-42 words each; conclusion and \
   future_directions 35-50 words each.
3. "title" = the article's OWN title exactly as given in "Original title" \
   (you may only trim trailing noise such as "(video)" or "| Space photo of \
   the day"). It is a headline, NOT a problem statement and NOT a summary — \
   never put the big problem or any rewritten sentence in "title". \
   "big_problem" = the BACKGROUND problem: the big-picture industry problem \
   this story sits in (the BLUF), as one sharp, specific sentence — not a \
   vague truism, and clearly different from the title.
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
   similar there either. If you cannot identify a genuine result for one or \
   more of the three slots, leave result_N_headline/result_N_detail as empty \
   strings "" too rather than inventing one — do NOT set is_engineering_relevant \
   to false just because fewer than three (or even zero) results are available; \
   that trade-off is handled elsewhere in our pipeline, not by you.
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

# Schema/prompt leggero per le Flash News: elabora un intero batch di
# candidati in una sola chiamata (titolo + riassunto RSS soltanto, MAI il
# testo integrale dell'articolo) e restituisce solo titolo breve + sintesi +
# categoria — nessuna analisi BLUF/idea/piano/risultati/punteggio.
FLASH_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "flashes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "summary": {"type": "STRING"},
                    "category": {"type": "STRING", "enum": CATEGORIES},
                },
                "required": ["id", "title", "summary", "category"],
            },
        },
    },
    "required": ["flashes"],
}

FLASH_SYSTEM_RULES = (
    "You are writing short breaking-news-style blurbs for an engineering/"
    "technology news ticker. You receive a numbered batch of candidates, each "
    "with only a source name, an original title, and a short RSS summary/"
    "abstract (no full article text). For each candidate, write a compact "
    "flash entry using ONLY the information given in that candidate's title "
    "and summary — never invent facts, numbers, or details that aren't there, "
    "and never pull in information from a different candidate in the batch.\n\n"
    "Rules:\n"
    "1. \"title\": a short, punchy news-ticker headline in English, max ~12 "
    "words — tighten/rephrase the original if needed, don't just copy it "
    "verbatim unless it's already tight.\n"
    "2. \"summary\": ONE short sentence in English, max ~30 words, plainly "
    "stating what happened or was announced — no analysis, no score, no "
    "filler, no invented specifics.\n"
    "3. \"category\": pick the single best fit from this exact list: "
    + ", ".join(CATEGORIES) + ".\n"
    "4. If a candidate's title+summary are too thin, generic, or off-topic "
    "(not real engineering/technology news) to honestly write both a title "
    "and a summary without inventing anything, OMIT that id entirely from "
    "your response instead of forcing a weak entry.\n"
    "5. Reply with ONLY a JSON object of the exact shape "
    "{\"flashes\": [{\"id\": <int>, \"title\": \"...\", \"summary\": \"...\", "
    "\"category\": \"...\"}, ...]}, no text before/after, no ``` code blocks. "
    "Include only the ids you can honestly complete — it's fine to return "
    "fewer items than you were given."
)


def build_flash_batch_prompt(batch: list[dict]) -> str:
    blocks = []
    for idx, item in enumerate(batch):
        blocks.append(
            f"id: {idx}\n"
            f"source: {item['source_name']}\n"
            f"title: {item['title']}\n"
            f"summary: {item['summary'] or '(no summary provided)'}"
        )
    return "\n\n".join(blocks) + "\n\nReturn the JSON object described in the system prompt."


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
    # sostanzioso; altrimenti ripiega sul riassunto del feed RSS. Anche se
    # il materiale resta limitato il modello è ora istruito a marcare
    # comunque is_engineering_relevant=true per un vero argomento tecnico:
    # is_substantive() più sotto decide poi se basta per l'analisi completa
    # (top 10) o solo per una Flash News — non è più il modello a scartare.
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


class LLMPermanentError(RuntimeError):
    """Errore che riprovare non risolve (chiave non valida, tutti i modelli
    esauriti o ritirati...). main() interrompe subito il run invece di
    bruciare minuti su articoli che fallirebbero tutti allo stesso modo."""


LLM_TIMEOUT_SECONDS = 60
LLM_MAX_TRANSIENT_RETRIES = 5

_gemini_idx = 0  # indice del modello attualmente in uso in GEMINI_MODELS


def _gemini_url() -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODELS[_gemini_idx]}:generateContent"


def _advance_gemini_model(reason: str) -> None:
    """Passa al prossimo modello Gemini di riserva, o solleva
    LLMPermanentError se non ne restano."""
    global _gemini_idx
    if _gemini_idx + 1 >= len(GEMINI_MODELS):
        raise LLMPermanentError(
            f"tutti i modelli Gemini configurati sono inutilizzabili ({reason}); "
            f"provati: {', '.join(GEMINI_MODELS)}"
        )
    print(
        f"[WARN] modello Gemini '{GEMINI_MODELS[_gemini_idx]}' non utilizzabile ({reason}): "
        f"passo a '{GEMINI_MODELS[_gemini_idx + 1]}'.",
        file=sys.stderr,
    )
    _gemini_idx += 1


def _is_daily_quota(text: str) -> bool:
    """True se un 429 riguarda la quota GIORNALIERA (inutile aspettare pochi
    secondi) e non un semplice limite al minuto."""
    low = (text or "").lower()
    return "perday" in low or "per day" in low or "(tpd)" in low


def _llm_post(provider: str, body: dict, label: str) -> dict | None:
    """POST verso il provider LLM con la gestione degli errori in un unico
    posto. Ritorna il JSON della risposta, oppure None se l'articolo non è
    elaborabile ORA (errori temporanei che persistono dopo i retry, o richiesta
    rifiutata per quello specifico contenuto). Solleva LLMPermanentError per
    gli errori che non ha senso ritentare."""
    if provider == "gemini":
        headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}
    else:
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_API_KEY}"}

    transient = 0
    while True:
        url = _gemini_url() if provider == "gemini" else GROQ_URL
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=LLM_TIMEOUT_SECONDS)
        except requests.RequestException as exc:  # timeout, DNS, connessione interrotta...
            status, text = None, f"errore di rete: {exc}"
        else:
            status, text = resp.status_code, resp.text
            if status == 200:
                try:
                    return resp.json()
                except ValueError:
                    status, text = None, "risposta non JSON"

        # --- da qui in poi: qualcosa è andato storto ---
        if status in (401, 403):
            raise LLMPermanentError(
                f"{provider}: chiave API non valida o senza permessi (HTTP {status}): {text[:200]}"
            )
        if status == 404:
            if provider == "gemini":
                _advance_gemini_model("HTTP 404, modello non disponibile/ritirato")
                continue
            raise LLMPermanentError(f"groq: modello '{GROQ_MODEL}' non disponibile (HTTP 404): {text[:200]}")
        if status == 429 and _is_daily_quota(text):
            if provider == "gemini":
                _advance_gemini_model("quota giornaliera esaurita")
                continue
            raise LLMPermanentError(f"groq: quota giornaliera esaurita: {text[:200]}")
        if status is not None and status < 500 and status != 429:
            # 400/413/422...: la richiesta per QUESTO contenuto viene rifiutata,
            # riprovarla identica non cambia nulla.
            print(f"[WARN] {provider} HTTP {status} ({label}): {text[:300]}", file=sys.stderr)
            return None

        # Errore temporaneo: 429 al minuto, 5xx (es. "high demand"), rete.
        transient += 1
        if transient > LLM_MAX_TRANSIENT_RETRIES:
            print(
                f"[WARN] {provider}: errori temporanei persistenti ({label}), "
                f"ultimo: HTTP {status if status is not None else 'rete'} {text[:200]}",
                file=sys.stderr,
            )
            return None
        wait = min(5 * 2 ** (transient - 1), 40)
        print(
            f"[WARN] {provider} HTTP {status if status is not None else 'rete'} ({label}): "
            f"riprovo tra {wait}s (tentativo {transient}/{LLM_MAX_TRANSIENT_RETRIES})...",
            file=sys.stderr,
        )
        time.sleep(wait)


def extract_json_object(text: str) -> dict:
    """Ripulisce la risposta del modello prima di interpretarla come JSON.
    Nonostante l'istruzione esplicita di non farlo, capita che il modello
    racchiuda comunque la risposta in un blocco ```json ... ``` o aggiunga
    testo prima/dopo l'oggetto JSON vero e proprio: senza questa pulizia,
    OGNI chiamata fallirebbe silenziosamente con un errore di parsing."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Ultima spiaggia: estrae la sottostringa tra la prima "{" e l'ultima "}"
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def _call_and_parse(provider: str, body: dict, label: str, extract_text) -> dict | None:
    """Chiama il provider e interpreta il JSON prodotto dal modello. Se la
    risposta è malformata (o senza candidati, es. bloccata dai filtri) ritenta
    UNA volta: una seconda generazione può uscire valida."""
    for _ in range(2):
        data = _llm_post(provider, body, label)
        if data is None:
            return None
        try:
            parsed = extract_json_object(extract_text(data))
            if isinstance(parsed, dict):
                return parsed
            raise ValueError("il JSON restituito non è un oggetto")
        except (KeyError, IndexError, TypeError, ValueError) as exc:  # JSONDecodeError è un ValueError
            print(f"[WARN] risposta {provider} non interpretabile ({label}): {exc}", file=sys.stderr)
    return None


def _gemini_text(data: dict) -> str:
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _groq_text(data: dict) -> str:
    return data["choices"][0]["message"]["content"]


def call_gemini(item: dict, article_text: str) -> dict | None:
    if not GEMINI_API_KEY:
        raise LLMPermanentError("GEMINI_API_KEY non impostata nell'ambiente.")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_RULES}]},
        "contents": [{"role": "user", "parts": [{"text": build_user_prompt(item, article_text)}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }
    return _call_and_parse("gemini", body, item["url"], _gemini_text)


def call_groq(item: dict, article_text: str) -> dict | None:
    if not GROQ_API_KEY:
        raise LLMPermanentError("GROQ_API_KEY non impostata nell'ambiente.")
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_RULES},
            {"role": "user", "content": build_user_prompt(item, article_text)},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    return _call_and_parse("groq", body, item["url"], _groq_text)


def call_llm(item: dict, article_text: str) -> dict | None:
    """Dispatcher: usa il motore scelto in LLM_PROVIDER senza cambiare il resto
    della pipeline."""
    if LLM_PROVIDER == "gemini":
        return call_gemini(item, article_text)
    return call_groq(item, article_text)


def call_gemini_flash_batch(batch: list[dict]) -> list[dict] | None:
    if not GEMINI_API_KEY:
        raise LLMPermanentError("GEMINI_API_KEY non impostata nell'ambiente.")
    body = {
        "systemInstruction": {"parts": [{"text": FLASH_SYSTEM_RULES}]},
        "contents": [{"role": "user", "parts": [{"text": build_flash_batch_prompt(batch)}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
            "responseSchema": FLASH_RESPONSE_SCHEMA,
        },
    }
    parsed = _call_and_parse("gemini", body, "flash batch", _gemini_text)
    return None if parsed is None else parsed.get("flashes", [])


def call_groq_flash_batch(batch: list[dict]) -> list[dict] | None:
    if not GROQ_API_KEY:
        raise LLMPermanentError("GROQ_API_KEY non impostata nell'ambiente.")
    body = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": FLASH_SYSTEM_RULES},
            {"role": "user", "content": build_flash_batch_prompt(batch)},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    parsed = _call_and_parse("groq", body, "flash batch", _groq_text)
    return None if parsed is None else parsed.get("flashes", [])


def call_llm_flash_batch(batch: list[dict]) -> list[dict] | None:
    """Dispatcher gemello di call_llm, ma per il prompt leggero delle Flash
    News (batch di più candidati in un'unica chiamata)."""
    if LLM_PROVIDER == "gemini":
        return call_gemini_flash_batch(batch)
    return call_groq_flash_batch(batch)


NUMBER_RE = re.compile(r"\d[\d.,]*")

TITLE_NOISE_RE = re.compile(r"\s*[\(\[]\s*(?:video|videos|photos?|gallery|podcast)\s*[\)\]]\s*$", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Toglie da un titolo RSS i marcatori finali tipo '(video)' / '[photos]'."""
    return TITLE_NOISE_RE.sub("", (title or "").strip()).strip()


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

    # "title" non è più tra i campi controllati: ora è sempre il titolo
    # originale dell'articolo (anche di sole 1-2 parole), non testo generato.
    required = ["big_problem", "small_problem", "idea", "plan", "conclusion"]
    for key in required:
        if not has_content(key, min_words=5):
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
    # Soglia abbassata da 2 a 1: da quando il prompt (SYSTEM_RULES) lascia
    # onestamente vuoti gli slot senza un risultato genuino invece di forzarli,
    # richiedere ancora 2/3 risultati qui scartava dalla top 10 (verso le
    # Flash News) articoli comunque validi con un solo riscontro quantificabile
    # ma un problema/idea/piano ben scritti — troppo severo per il volume atteso.
    if solid_results < 1:
        return False, f"solo {solid_results}/3 risultati con contenuto reale (minimo 1)"

    return True, ""


def structure_item(item: dict) -> tuple[dict | None, bool, bool]:
    """Ritorna (brief, chiamata_fallita, pertinente).
    brief è None se la notizia non è pertinente, se il contenuto risulta
    troppo povero per un'analisi seria, o se la chiamata all'LLM è fallita;
    chiamata_fallita è True SOLO in quest'ultimo caso, ed è il segnale che
    main() usa per il circuit breaker. pertinente è True quando il modello ha
    giudicato is_engineering_relevant, ANCHE se poi risultata troppo leggera
    per l'analisi completa — main() usa questo flag per selezionare i
    candidati delle Flash News, senza dover richiamare l'LLM una seconda volta
    solo per saperlo."""
    image_url, article_text = fetch_article_page(item["url"])

    structured = call_llm(item, article_text)
    if structured is None:
        return None, True, False
    if not structured.get("is_engineering_relevant"):
        body_len = max(len(article_text), len(item["summary"]))
        print(
            f"[INFO] Scartata (non pertinente secondo il modello, testo disponibile: "
            f"{body_len} caratteri{' — probabile blocco/fetch fallito' if len(article_text) == 0 else ''}): "
            f"{item['title'][:70]}",
            file=sys.stderr,
        )
        return None, False, False

    ok, reason = is_substantive(structured)
    if not ok:
        print(f"[INFO] Scartata (contenuto insufficiente: {reason}): {item['title'][:70]}", file=sys.stderr)
        return None, False, True

    haystack = item["title"] + " " + item["summary"] + " " + article_text
    structured = guard_against_invented_numbers(structured, haystack)

    brief = {
        # Sempre il titolo ORIGINALE dell'articolo (ripulito da marcatori tipo
        # "(video)"), mai una frase generata dal modello: il "big problem" è
        # il problema di background e vive nel suo campo, non nel titolo.
        "title": clean_title(item["title"]) or structured.get("title") or item["title"],
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

    return brief, False, True


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
    """Unisce le notizie già pubblicate con quelle nuove e sceglie cosa tenere
    in news.json, separatamente per le due fasce che il sito mostra:
      - FRESCHE (<FRESH_WINDOW_HOURS): le migliori FRESH_POOL_SIZE per score.
        Il sito ne mostra le PUBLISH_TOP_N migliori ancora "di oggi" al
        momento della visita; la riserva più ampia serve perché tra un sync e
        l'altro alcune invecchiano e escono dalla vista.
      - ARCHIVIO (tra FRESH_WINDOW_HOURS e RETAIN_WINDOW_HOURS): le migliori
        ARCHIVE_POOL_SIZE, per la sezione "Learn from yesterday".
    Prima si prendevano le migliori 10 in blocco su 48h: articoli di ieri con
    punteggio alto occupavano i posti e oggi ne restavano visibili solo 2-3."""
    now = dt.datetime.now(dt.timezone.utc)
    retain_cutoff = now - dt.timedelta(hours=RETAIN_WINDOW_HOURS)
    fresh_cutoff = now - dt.timedelta(hours=FRESH_WINDOW_HOURS)

    by_url = {b["source_url"]: b for b in existing}
    for b in new_briefs:
        by_url[b["source_url"]] = b

    def published(b: dict) -> dt.datetime:
        return dt.datetime.fromisoformat(b["published_at"])

    merged = [b for b in by_url.values() if published(b) >= retain_cutoff]
    fresh = [b for b in merged if published(b) >= fresh_cutoff]
    older = [b for b in merged if published(b) < fresh_cutoff]

    def rank(b: dict):
        return (b.get("score", 0), b["published_at"])

    fresh.sort(key=rank, reverse=True)
    older.sort(key=rank, reverse=True)
    # Una notizia con punteggio più alto arrivata in un run successivo può
    # "scalzare" una già pubblicata con punteggio più basso, ma solo nella
    # propria fascia: le notizie di oggi non competono con quelle di ieri.
    return fresh[:FRESH_POOL_SIZE] + older[:ARCHIVE_POOL_SIZE]


def load_seen() -> dict[str, str]:
    """URL già valutati in run precedenti (url -> ISO timestamp)."""
    if os.environ.get("IGNORE_SEEN") or not os.path.exists(SEEN_PATH):
        return {}
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_seen(seen: dict[str, str]) -> None:
    """Salva la cache degli URL visti, eliminando quelli più vecchi di
    SEEN_RETAIN_HOURS (un articolo fuori dalla finestra di raccolta non
    tornerà comunque nel feed, quindi non serve ricordarlo per sempre)."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=SEEN_RETAIN_HOURS)
    kept = {}
    for url, ts in seen.items():
        try:
            if dt.datetime.fromisoformat(ts) >= cutoff:
                kept[url] = ts
        except (TypeError, ValueError):
            continue
    save_json(SEEN_PATH, kept)


def load_totals() -> dict:
    """Contatori cumulativi: {"count": N, "funnel": {"analyzed", "relevant",
    "substantive"}}. Tollera il vecchio formato (solo "count") e file mancanti
    o corrotti: in quel caso i contatori del funnel ripartono da zero."""
    totals = {"count": 0, "funnel": {"analyzed": 0, "relevant": 0, "substantive": 0}}
    if not os.path.exists(TOTAL_ANALYZED_PATH):
        return totals
    try:
        with open(TOTAL_ANALYZED_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        totals["count"] = int(data.get("count", 0))
        for key in totals["funnel"]:
            totals["funnel"][key] = int((data.get("funnel") or {}).get(key, 0))
    except (json.JSONDecodeError, OSError, ValueError, TypeError, AttributeError):
        pass
    return totals


def save_totals(totals: dict) -> None:
    save_json(TOTAL_ANALYZED_PATH, totals)


def save_stats_manifest(totals: dict) -> None:
    """Scrive docs/stats.json: alimenta i 3 indicatori in home (articoli
    analizzati/fonti/categorie) e l'elenco fonti nel pannello 'BITL Score'.
    Le fonti sono dedotte da SOURCES (dedup per nome, alcune compaiono più
    volte con feed diversi) più le API dirette non presenti in quella lista."""
    names, seen_names = [], set()
    for s in SOURCES:
        if s["name"] not in seen_names:
            seen_names.add(s["name"])
            names.append(s["name"])
    names.append("Semantic Scholar")
    if OPENALEX_API_KEY:
        names.append("OpenAlex")

    save_json(STATS_OUTPUT_PATH, {
        "articles_analyzed": totals["count"],
        "funnel": totals["funnel"],
        "sources": names,
        "categories": CATEGORIES,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })


# --------------------------------------------------------------------------- #
# Flash News
# --------------------------------------------------------------------------- #

def build_flash_news(candidates: list[dict]) -> list[dict]:
    """Genera le Flash News in batch da FLASH_BATCH_SIZE candidati per
    chiamata LLM (titolo+summary RSS soltanto, mai il testo integrale). Ogni
    batch fallito viene loggato e saltato senza interrompere gli altri —
    questa funzione stessa è comunque richiamata da main() dentro un
    try/except più ampio, per non compromettere mai la top 10 già salvata."""
    flashes = []
    total_batches = (len(candidates) + FLASH_BATCH_SIZE - 1) // FLASH_BATCH_SIZE
    for batch_num, i in enumerate(range(0, len(candidates), FLASH_BATCH_SIZE), start=1):
        batch = candidates[i:i + FLASH_BATCH_SIZE]
        try:
            results = call_llm_flash_batch(batch)
        except LLMPermanentError as exc:
            print(f"[WARN] Flash News interrotte, errore permanente dell'LLM: {exc}", file=sys.stderr)
            break
        except Exception as exc:  # noqa: BLE001 - un batch fallito non deve bloccare gli altri
            print(f"[WARN] batch flash news {batch_num}/{total_batches} fallito, lo salto: {exc}", file=sys.stderr)
            continue
        if not results:
            print(f"[WARN] nessuna flash news dal batch {batch_num}/{total_batches} (chiamata fallita o risposta vuota).", file=sys.stderr)
            continue

        for entry in results:
            try:
                idx = int(entry.get("id"))
                item = batch[idx]
            except (TypeError, ValueError, IndexError):
                continue
            title = str(entry.get("title", "")).strip()
            summary = str(entry.get("summary", "")).strip()
            category = entry.get("category") or "Other Engineering"
            if (
                not title or not summary
                or title.lower() in PLACEHOLDER_VALUES
                or summary.lower() in PLACEHOLDER_VALUES
            ):
                continue
            flashes.append({
                "title": title,
                "summary": summary,
                "category": category if category in CATEGORIES else "Other Engineering",
                "source_name": item["source_name"],
                "source_url": item["url"],
                "published_at": item["published_at"].isoformat(),
                "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })

        if batch_num < total_batches:
            time.sleep(1.5)

    print(f"[INFO] {len(flashes)} flash news generate da {len(candidates)} candidati ({total_batches} batch).")
    return flashes


def load_existing_flash() -> list[dict]:
    if not os.path.exists(FLASH_OUTPUT_PATH):
        return []
    try:
        with open(FLASH_OUTPUT_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []


def merge_and_prune_flash(existing: list[dict], new_flashes: list[dict], exclude_urls: set[str]) -> list[dict]:
    """Stessa logica di merge_and_prune (dedup per source_url + finestra di
    retention + tetto massimo), con l'aggiunta di exclude_urls: una flash news
    il cui source_url è finito nella top 10 (in QUESTO run o in uno precedente)
    non deve mai comparire anche qui — stesso URL sorgente = un posto solo."""
    now = dt.datetime.now(dt.timezone.utc)
    retain_cutoff = now - dt.timedelta(hours=FLASH_RETAIN_WINDOW_HOURS)

    by_url = {f["source_url"]: f for f in existing}
    for f in new_flashes:
        by_url[f["source_url"]] = f

    merged = [
        f for f in by_url.values()
        if f["source_url"] not in exclude_urls
        and dt.datetime.fromisoformat(f["published_at"]) >= retain_cutoff
    ]
    merged.sort(key=lambda f: f["published_at"], reverse=True)
    return merged[:FLASH_MAX_ITEMS]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    collect_cutoff = now - dt.timedelta(hours=COLLECT_WINDOW_HOURS)

    existing = load_existing()
    seen = load_seen()
    already_seen_urls = {b["source_url"] for b in existing} | set(seen)

    raw_items = collect_all(collect_cutoff)
    fresh_items = dedupe(raw_items, already_seen_urls)
    fresh_items.sort(key=lambda it: it["published_at"], reverse=True)
    fresh_items = fresh_items[:MAX_ITEMS_PER_RUN]
    print(
        f"[INFO] {len(fresh_items)} articoli nuovi da valutare (limite {MAX_ITEMS_PER_RUN}/run; "
        f"{len(raw_items)} raccolti, {len(raw_items) - len(dedupe(raw_items, set()))} duplicati e "
        f"{len(set(it['url'] for it in raw_items) & already_seen_urls)} già valutati in run precedenti)."
    )

    new_briefs: list[dict] = []
    relevant_items_this_run: list[dict] = []  # pertinenti secondo il modello, usati sotto per le Flash News
    retry_queue: list[dict] = []              # falliti per errori temporanei: secondo passaggio a fine run
    stats = {"evaluated": 0}
    abort_reason = None

    def evaluate(item: dict) -> bool:
        """Valuta un articolo e ne registra l'esito. Ritorna True se la chiamata
        LLM è FALLITA (l'articolo non viene segnato come visto, così un run
        successivo può riprovarlo). Solleva LLMPermanentError se non ha senso
        continuare il run."""
        try:
            brief, call_failed, is_relevant = structure_item(item)
        except LLMPermanentError:
            raise
        except Exception as exc:  # noqa: BLE001 - un errore imprevisto su UN articolo non deve fermare l'intero run
            print(f"[WARN] Errore imprevisto su questo articolo, lo salto: {exc}", file=sys.stderr)
            return True
        if call_failed:
            return True
        stats["evaluated"] += 1
        seen[item["url"]] = dt.datetime.now(dt.timezone.utc).isoformat()
        if brief:
            new_briefs.append(brief)
        if is_relevant:
            relevant_items_this_run.append(item)
        return False

    consecutive_failures = 0
    try:
        for i, item in enumerate(fresh_items, start=1):
            print(f"[INFO] ({i}/{len(fresh_items)}) elaboro: {item['title'][:70]}...")
            if evaluate(item):
                retry_queue.append(item)
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    abort_reason = (
                        f"{MAX_CONSECUTIVE_FAILURES} articoli di fila non elaborabili dall'LLM "
                        "(vedi i [WARN] sopra per il motivo)"
                    )
                    break
            else:
                consecutive_failures = 0
            time.sleep(1.5)  # margine di cortesia sui rate limit del free tier

        # Secondo passaggio: gli articoli falliti per errori TEMPORANEI (503,
        # timeout, rate limit) spesso riescono dopo una pausa. Prima bastavano
        # 3 fallimenti di fila per chiudere il run lasciando fuori tutto il resto.
        if retry_queue and not abort_reason:
            print(
                f"[INFO] Secondo passaggio su {len(retry_queue)} articoli falliti per errori temporanei "
                f"(pausa di {RETRY_PASS_COOLDOWN_SECONDS}s)..."
            )
            time.sleep(RETRY_PASS_COOLDOWN_SECONDS)
            recovered, still_failing = 0, 0
            for item in retry_queue:
                if evaluate(item):
                    still_failing += 1
                    if still_failing >= 5:
                        print("[WARN] Secondo passaggio interrotto: l'LLM continua a non rispondere.", file=sys.stderr)
                        break
                else:
                    recovered += 1
                time.sleep(1.5)
            print(f"[INFO] Secondo passaggio: {recovered} recuperati, {len(retry_queue) - recovered} ancora falliti.")
    except LLMPermanentError as exc:
        abort_reason = str(exc)

    if abort_reason:
        print(
            f"[ERROR] Run interrotto: {abort_reason}. Gli articoli valutati fin qui "
            "vengono comunque pubblicati; gli altri saranno ritentati al prossimo sync.",
            file=sys.stderr,
        )

    # La cache degli URL visti va salvata sempre, anche a run interrotto:
    # evita di rivalutare (e ripagare in quota) gli articoli già fatti.
    try:
        save_seen(seen)
    except Exception as exc:  # noqa: BLE001 - la cache è solo un'ottimizzazione
        print(f"[WARN] Impossibile salvare la cache degli URL visti: {exc}", file=sys.stderr)

    # Contatore cumulativo per l'indicatore "ARTICLES ANALYZED" in home: non va
    # mai potato (a differenza di seen_urls.json), cresce di quanti articoli
    # sono stati DAVVERO valutati in QUESTO run (stats["evaluated"]).
    try:
        totals = load_totals()
        totals["count"] += stats["evaluated"]
        totals["funnel"]["analyzed"] += stats["evaluated"]
        totals["funnel"]["relevant"] += len(relevant_items_this_run)
        totals["funnel"]["substantive"] += len(new_briefs)
        save_totals(totals)
        save_stats_manifest(totals)
    except Exception as exc:  # noqa: BLE001 - gli indicatori sono un extra, non devono bloccare il run
        print(f"[WARN] Impossibile aggiornare il contatore/manifest per gli indicatori: {exc}", file=sys.stderr)

    merged = merge_and_prune(existing, new_briefs)
    save_json(OUTPUT_PATH, merged)

    fresh_cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=FRESH_WINDOW_HOURS)
    n_fresh = sum(1 for b in merged if dt.datetime.fromisoformat(b["published_at"]) >= fresh_cutoff)
    print(f"[INFO] {len(new_briefs)} notizie pertinenti E sostanziali su {stats['evaluated']} valutate "
          f"(le altre sono state scartate per pertinenza o qualità insufficiente — vedi i log [INFO]/[WARN] sopra).")
    print(f"[INFO] In {OUTPUT_PATH}: {n_fresh} notizie fresche (<{FRESH_WINDOW_HOURS}h, il sito ne mostra le migliori "
          f"{PUBLISH_TOP_N}) + {len(merged) - n_fresh} di archivio.")
    if n_fresh < PUBLISH_TOP_N:
        print(
            f"[WARN] Solo {n_fresh} notizie fresche disponibili, meno delle {PUBLISH_TOP_N} attese: "
            "o oggi le fonti hanno pubblicato poco, o il run è stato interrotto (vedi [ERROR]/[WARN] sopra).",
            file=sys.stderr,
        )

    # Flash News: sempre DOPO che la top 10 è già stata salvata sopra, e
    # sempre in un blocco isolato — qualunque errore qui (LLM, I/O, parsing)
    # viene solo loggato, non deve mai far fallire il run né toccare news.json.
    try:
        published_urls = {b["source_url"] for b in merged}
        flash_candidates = [it for it in relevant_items_this_run if it["url"] not in published_urls]
        print(f"[INFO] {len(flash_candidates)} candidati per le Flash News (pertinenti, non pubblicati in {OUTPUT_PATH}).")

        new_flashes = build_flash_news(flash_candidates)
        existing_flash = load_existing_flash()
        merged_flash = merge_and_prune_flash(existing_flash, new_flashes, published_urls)
        save_json(FLASH_OUTPUT_PATH, merged_flash)

        print(f"[INFO] {len(merged_flash)} Flash News pubblicate (tetto: {FLASH_MAX_ITEMS}) in {FLASH_OUTPUT_PATH}.")
    except Exception as exc:  # noqa: BLE001 - le Flash News sono un extra: un loro fallimento non deve mai
        # compromettere la top 10, già salvata correttamente qui sopra.
        print(f"[WARN] Generazione Flash News fallita, le notizie principali restano comunque pubblicate: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
