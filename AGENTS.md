# AGENTS.md: VybeDeal ("pi" Agent)

## 🤖 Agent Identity & Purpose
- **Name:** "pi" (Price Intelligence & bargain finder agent)
- **Domain:** German second-hand market (Kleinanzeigen)
- **Core function:** scrape → enrich → score → compare across runs, so a reseller
  sees which listing to buy *now* and for how much to sell it.

## 🛠️ Technology Stack
- **UI:** Streamlit (>= 1.49; `width="stretch"` instead of the deprecated
  `use_container_width`, `st.status` for progress, `st.tabs` for result views)
- **Scraping:** `curl_cffi` (`impersonate="chrome120"`) + `BeautifulSoup4` (lxml if present)
- **AI:** `google-genai`, `gemini-2.5-flash`, strict JSON via `response_schema`
- **State:** plain JSON files under `data/` (no DB), `st.cache_data` for cost control
- **Tests:** pytest + `streamlit.testing.v1.AppTest`, real-HTML fixtures

## 📂 Layout
```
app.py                    UI only: widgets → pipeline → views
dealfinder/models.py      SearchParams / Listing / Deal (margin_eur, roi_pct)
dealfinder/pricing.py     German price parsing ("210 € VB", "ab …", "Zu verschenken")
dealfinder/scraper.py     URL building, polite HTTP, list + detail parsers
dealfinder/scoring.py     keyless heuristic scorer (cohort percentile + signals)
dealfinder/ai.py          Gemini batches, fatal-vs-transient errors, merge by ad_id
dealfinder/store.py       favourites, price history, saved searches (data/*.json)
dealfinder/export.py      CSV (BOM) / JSON
tests/                    pytest; VYBEDEAL_ONLINE=1 enables the live scrape test
```

## ⚙️ Pipeline contract
1. **SearchParams** (sidebar form) → `scraper.search()`.
   URL shape: `https://www.kleinanzeigen.de/s-preis:{min}::{max}-s-plz:{plz}::{radius}`
   `-s-sortierung:neueste/s-{query}/k0`, paging via `s-seite:N` (25 ads/page).
2. **Listing markup changed in 2025/26** – the old `.aditem` classes are gone.
   Current anchors: `article[data-adid]` + `data-href`, `h3` title,
   `p.text-title3.font-strong` price, `p.text-onSurfaceSubdued` snippet,
   top-row `<span>`s = location + date (PRO ads show the shop name instead),
   `img`/JSON-LD `contentUrl` = image, `[data-gaevent*='partneradid']` = commercial.
   Detail page still uses `#viewad-title`, `#viewad-price`, `#viewad-extra-info`,
   `#viewad-description-text`, `#viewad-details li` + `.addetailslist--detail--value`.
   → change selectors **with** a fixture update in `tests/fixtures/`.
3. **Scoring:** heuristic always runs first (fallback + comparison), then Gemini
   verdicts are merged **by `ad_id` only**. The model never returns urls/prices —
   it must not be able to hallucinate a link.
4. **Filtering happens in the UI**, never in the prompt: the model scores every
   listing so the min-score slider can hide/reveal without re-spending tokens.

## 🖥️ UI flow
Sidebar form (query, price, PLZ+radius, count, filters, model) → run button →
`st.status` progress → KPI row → tabs: **Deals** (cards), **Tabelle** (+export),
**Markt-Analyse** (quartiles, scatter, score histogram, price history),
**Favoriten**, **Einzel-Check**. Results live in `st.session_state["result"]`
so favouriting/sorting reruns never re-scrape.

## 🛡️ Operational rules
- **One key source: `secrets/gemini.key`** (compose mounts it to
  `/run/secrets/gemini_key`). `GEMINI_API_KEY` env is the CI/`docker run` path
  only; `.streamlit/secrets.toml` is *not* read any more. If two sources are set
  at once, `resolve_key()` raises instead of silently picking one – keep it that
  way. Never `COPY .streamlit/` (or any secret) into the image. Container runs
  as non-root `vybe` with a healthcheck.
- Respect the site: sequential page loads, jittered delays, ≤ 120 ads per run,
  backoff on 429/403/5xx, and stop with a clear message when blocked.
- Fatal API errors (403 "reported as leaked", invalid key, quota) must fail fast
  with an actionable German hint — no retry storms.
- Anything on disk goes through `store._save()` (atomic `os.replace`) and must
  survive a corrupt/missing file.
- Keep the heuristic path working: the app is useful with zero API keys.
