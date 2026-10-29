# ⚡ VybeDeal

Kleinanzeigen-Deal-Finder: sucht, reichert an, bewertet den Wiederverkaufswert
(Gemini **oder** eingebaute Heuristik), rechnet Marge & ROI, erkennt
**Preisfälle über mehrere Läufe** und merkt sich Favoriten & Suchen.

## Features

| | |
|---|---|
| 🔍 **Suche wie auf der Seite** | Suchbegriff, Preisspanne, PLZ + Umkreis, Server-Vorsortierung, bis 100 Inserate (mehrseitig) |
| 🧠 **Kontext statt Titel** | Detailseiten werden mitgeloggt: volle Beschreibung, Zustand/Marke/Speicher aus den Attributen, Versandhinweis, Privat vs. Gewerbe |
| 🤖 **KI-Bewertung** | Gemini `gemini-2.5-flash` mit strukturierter Antwort (`response_schema`): Score 1–10, Wiederverkauf in €, Konfidenz, Liquidität, Red Flags, Verhandlungs-Tipp |
| 🧮 **Heuristik-Modus ohne Key** | Preis-Perzentil gegen die echten Suchergebnisse + Zustands-/Verkäufer-Signale – gleiche Metriken, keine API-Kosten |
| 📉 **Preisfall-Erkennung** | Jeder Lauf schreibt den Preis pro Anzeige; günstiger als beim letzten Blick → Badge & Filter |
| ⭐ **Favoriten & Suchen** | Watchlist und gespeicherte Suchen überleben Neustart (`data/*.json`) |
| 📊 **Markt-Analyse** | Median/Quartile der Angebotspreise, Streuung Angebot vs. Wiederverkauf, Score-Verteilung, Top-Margen, Preis-Historie |
| 🎯 **Einzel-Check** | Beliebige Anzeigen-URL einfügen → Score, Marge, Red Flags, Attribute |
| ⬇️ **Export** | CSV (Excel-kompatibel, Umlaute) & JSON |
| 🛡️ **Robust** | Retry mit Backoff, Bot-Schutz-Erkennung, Politeness-Delays, Teil-Caching: Re-Runs zahlen nur neue Inserate |

## Schnellstart

```bash
# einmalig: Key anlegen (ohne Key läuft die Heuristik)
mkdir -p secrets && printf '%s\n' "AIza…" > secrets/gemini.key

./run.sh            # legt .venv selbst an und öffnet http://localhost:8501
```

Von Hand (was `run.sh` intern tut):

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py        # oder: source .venv/bin/activate && streamlit run app.py
```

> `streamlit: command not found` heißt fast immer: die venv ist nicht aktiviert.
> Entweder `source .venv/bin/activate` vorher, oder direkt
> `./.venv/bin/streamlit run app.py` bzw. `python3 -m streamlit run app.py`.

Ohne Key funktioniert alles außer der KI-Bewertung; die Spalte *Quelle* zeigt
pro Karte, woher der Score kommt.

## Docker

```bash
# secrets/gemini.key existiert bereits aus Schritt 1 – compose hängt sie ein
docker compose up --build          # → http://localhost:8501
```

Ohne Compose:

```bash
docker build -t vybedeal .
docker run -p 8501:8501 \
  --mount type=bind,src=$PWD/secrets/gemini.key,dst=/run/secrets/gemini_key:ro \
  -v vybedeal-data:/app/data vybedeal
```

> **Sicherheit:** Das Image enthält bewusst keine `secrets.toml`. Früher wurde
> `.streamlit/` ins Image kopiert – damit steckt der Key in einer Image-Schicht
> und ist für jeden mit Image-Zugriff lesbar. Bitte keinen Key committen oder
> ins Bild backen; wenn ein Key geleakt ist (Google meldet „reported as
> leaked“), ist er weg – neuen Schlüssel erzeugen.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q                        # 47 Tests, offline (Fixtures)
VYBEDEAL_ONLINE=1 pytest -q      # plus echter Scraping-Lauf gegen kleinanzeigen.de
```

## Projektstruktur

```
app.py                  Streamlit-UI (Filter, Karten, Tabelle, Charts, Favoriten)
dealfinder/
  scraper.py            URL-Bau, HTTP mit Impersonation, Parser (Liste + Detail)
  pricing.py            "210 € VB" → (210.0, "negotiable"), Formatierung, Badges
  scoring.py            Heuristik: Preis-Perzentil + Signale → Score, Schätzung
  ai.py                 Gemini-Batches, Schema, Fehlerklassen, Merge auf die Listings
  store.py              data/: favourites.json, price_history.json, saved_searches.json
  export.py             CSV/JSON
  models.py             SearchParams, Listing, Deal (inkl. margin_eur / roi_pct)
tests/                  pytest + HTML-Fixtures der echten Seite
```

## Konfiguration

### 🔑 API-Key: genau eine Quelle

**`secrets/gemini.key`** – lokal und im Container derselbe Weg. Die Datei ist
git-ignoriert, liegt in keiner Image-Schicht und wird von compose nach
`/run/secrets/gemini_key` eingehängt.

```bash
mkdir -p secrets && printf '%s\n' "AIza…" > secrets/gemini.key
```

| Alternative | Wann |
|---|---|
| Env `GEMINI_API_KEY` | CI oder `docker run -e` – dann die Datei **nicht** anlegen |
| `GEMINI_API_KEY_FILE` | Key soll woanders liegen |

Zwei Quellen gleichzeitig = **Abbruch mit Klarname**, die App rät nicht.
`.streamlit/secrets.toml` wird nicht mehr gelesen (dort steckte der Key mal im
Image); liegt noch ein `GEMINI_API_KEY` drin, erscheint ein Aufräum-Hinweis.
Die Sidebar zeigt immer, welche Quelle aktiv ist.

### Weitere Variablen

| Variable | Zweck |
|---|---|
| `GEMINI_API_KEY_FILE` | Pfad zur Key-Datei (überschreibt die Standardpfade) |
| `VYBEDEAL_DATA_DIR` | Ort für Favoriten/Preis-Historie (Default `data/`) |

## Fair Use

Nur öffentliche Suchergebnisse, wenige Requests pro Suche mit Jitter und
Backoff, keine Lasttests. Kleinanzeigen kann Struktur oder Bot-Schutz jederzeit
ändern – die Parser nutzen deshalb `data-adid`-Selektoren mit Fallback auf das
alte `.aditem`-Markup und schlagen fehlbar (Test-Fixtures schlagen dann an).
