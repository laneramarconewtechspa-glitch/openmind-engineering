# OpenMind Engineering

Aggregatore quotidiano di notizie di ingegneria — 100% gratuito, senza server da mantenere.

- **Raccolta:** oltre 30 fonti tra feed RSS e API scientifiche (ScienceDaily,
  IEEE Spectrum, MIT News/Technology Review, New Atlas, The Robot Report,
  SpaceNews, EE Times, New Civil Engineer, MD+DI, arXiv, Semantic Scholar...) —
  l'elenco completo, con il perché di ogni scelta, è la lista `SOURCES` in
  `fetch_news.py`.
- **Struttura ogni notizia** secondo lo schema BLUF · Intro (problema/idea/piano) ·
  R1/R2/R3 · Conclusione, usando l'API Gemini (Google AI Studio, livello gratuito).
- **Oggi e ieri**: il sito mostra le migliori 10 notizie delle ultime 24h; il
  pulsante "Learn from yesterday to act today" mostra le migliori 10 della fascia
  24-48h. `news.json` conserva quindi due fasce separate: una riserva più ampia di
  notizie "fresche" (così a qualunque ora ce ne sono almeno 10 ancora in finestra)
  e un archivio di ieri.
- **Flash News**: i candidati pertinenti ma esclusi dalla top 10 (contenuto
  troppo leggero per l'analisi completa, o sostanziosi ma fuori classifica per
  punteggio) diventano una striscia "Breaking Loop" in fondo alla pagina — solo
  titolo + sintesi breve, generati in batch in `docs/flash.json`.
- **Frontend**: HTML/CSS/JS puro — un cervello centrale ("Open your mind") che,
  al click, apre una rete di neuroni cliccabili, uno per notizia del giorno.
- **Esecuzione**: uno script Python lanciato 2 volte al giorno da GitHub Actions
  (gratis su repo pubblici), che scrive `docs/news.json` e `docs/flash.json`.
  Gli errori temporanei dell'LLM (503, timeout, rate limit) vengono ritentati,
  la quota giornaliera esaurita fa passare a un modello Gemini di riserva, e gli
  URL già valutati finiscono in `data/seen_urls.json` per non rivalutarli (e non
  consumare quota) a ogni sync.
- **Hosting**: GitHub Pages, servito direttamente dalla cartella `docs/`.

## 1. Crea il repository

1. Crea un nuovo repository **pubblico** su GitHub (es. `openmind-engineering`).
2. Carica tutti i file di questo progetto mantenendo la struttura delle cartelle
   (`.github/workflows/sync-news.yml`, `docs/`, `fetch_news.py`, `requirements.txt`).

## 2. Ottieni la chiave API Gemini (gratis)

1. Vai su **https://aistudio.google.com/apikey** e accedi con un account Google.
2. Clicca **Create API key** (in italiano a volte "Crea chiave API").
3. Se richiesto, lascia che Google crei un nuovo progetto Google Cloud collegato
   alla chiave (non serve una carta di credito per il livello gratuito).
4. Copia la chiave generata (inizia con `AIza...`) e conservala: non potrai
   rivederla per intero altrove se la perdi, dovrai crearne una nuova.

**Nota per chi è in UE/SEE/UK/Svizzera**: le condizioni di Google per il livello
gratuito dell'API Gemini possono differire per l'uso commerciale in queste aree.
Per un progetto personale come questo è quasi certamente nella norma, ma se in
fase di creazione della chiave Google ti chiede di attivare la fatturazione,
fammelo sapere: lo script è scritto in modo che la funzione `call_gemini` sia
facile da sostituire con un provider equivalente (es. Groq), senza dover
riscrivere il resto della pipeline.

## 3. Aggiungi la chiave come secret su GitHub

1. Nel repository, vai su **Settings → Secrets and variables → Actions**.
2. Clicca **New repository secret**.
3. Nome: `GEMINI_API_KEY` — Valore: la chiave copiata al passo 2.
4. Salva.

## 4. Abilita GitHub Pages

1. Vai su **Settings → Pages**.
2. In "Build and deployment" → "Source" scegli **Deploy from a branch**.
3. Branch: `main`, cartella: **/docs**. Salva.
4. Dopo un paio di minuti il sito sarà live su
   `https://<tuo-utente>.github.io/openmind-engineering/`.

## 5. Prima sincronizzazione manuale

1. Vai sulla tab **Actions** del repository.
2. Seleziona il workflow **Sync engineering news** nella lista a sinistra.
3. Clicca **Run workflow → Run workflow**.
4. Dopo 1-3 minuti (dipende da quanti articoli ci sono nelle ultime ore) il job
   finisce e committa `docs/news.json` aggiornato: ricarica il sito e clicca
   "Open your mind".

Da qui in poi il workflow gira da solo alle 06:00 e alle 14:00 (ora Italia),
ma puoi sempre rilanciarlo a mano dalla tab Actions.

## 6. Uso in locale (facoltativo, per test)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY="la-tua-chiave"
python fetch_news.py
# apri docs/index.html con un server statico locale, es:
python -m http.server 8000 --directory docs
```

## Struttura del progetto

```
openmind-engineering/
├── fetch_news.py                  # raccolta + filtro + strutturazione via Gemini
├── requirements.txt
├── data/
│   └── seen_urls.json             # cache degli URL già valutati, generata automaticamente
├── .github/workflows/sync-news.yml  # cron 2x/giorno + pulsante "Run workflow"
└── docs/
    ├── index.html
    ├── style.css
    ├── script.js
    ├── news.json                  # top 10 con analisi completa, generato/aggiornato automaticamente
    └── flash.json                 # Flash News (titolo+sintesi, no analisi), generato/aggiornato automaticamente
```

## Personalizzazioni comuni

- **Cambiare orari di sync**: modifica le due righe `cron:` in
  `.github/workflows/sync-news.yml` (formato UTC).
- **Aggiungere/rimuovere fonti**: modifica la lista `SOURCES` in `fetch_news.py`.
- **Cambiare modello Gemini**: imposta la variabile d'ambiente `GEMINI_MODEL`
  (i modelli vengono ritirati spesso e la quota gratuita è per modello: se
  quello scelto sparisce o finisce la quota giornaliera, il run passa da solo ai
  modelli elencati in `GEMINI_FALLBACK_MODELS`, separati da virgola).
- **Quante notizie mostrare**: `PUBLISH_TOP_N` in `fetch_news.py` **e**
  `MAX_PAPERS` in `docs/script.js` (tenerli uguali, es. entrambi 15).
- **Forzare la rivalutazione di tutto** (es. dopo aver cambiato il prompt):
  lancia con `IGNORE_SEEN=1`, oppure cancella `data/seen_urls.json`.
- **Controllo qualità manuale**: apri `docs/news.json` (è testo semplice) per
  vedere o correggere a mano una notizia già pubblicata.
- **Flash News**: numero massimo (`FLASH_MAX_ITEMS`) e finestra di retention
  (`FLASH_RETAIN_WINDOW_HOURS`) si regolano in `fetch_news.py`, vicino alle
  costanti `PUBLISH_TOP_N`/`RETAIN_WINDOW_HOURS` della top 10.
