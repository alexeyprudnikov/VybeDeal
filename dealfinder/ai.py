"""Gemini-based deal evaluation.

Design decisions that matter:

* The model **scores every listing** (it does not filter) – filtering happens in
  the UI, so the "min score" slider never silently hides data the model liked.
* The model only echoes ``ad_id``; price/url/location stay on our side, so a
  hallucinated URL can never end up in a card.
* Listings are sent in batches with retry/backoff, so a 60-result search does
  not fail as a single all-or-nothing request.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

from .models import Deal, Listing

log = logging.getLogger(__name__)

MODEL_DEFAULT = "gemini-2.5-flash"
BATCH_SIZE = 12

_PROMPT = """Du bist ein erfahrener Resale-Analyst für den deutschen Gebrauchtmarkt (Kleinanzeigen).
Suchbegriff des Nutzers: "{query}"

Bewerte JESES der folgenden Inserate. Filtere nichts weg, bewerte alle ad_ids.

Kriterien:
- Preis vs. realistischer Wiederbeschaffungs-/Wiederverkaufswert (resale_estimate_eur, Ganzzahl in EUR, Netto-Realitätscheck inkl. Zustand & Zubehör)
- Zustand & Vollständigkeit aus Beschreibung/Attributen
- Verkäufertyp (privat = bessere Verhandlung, gewerblich/refurbished = dünnere Marge)
- Liquidität: Wie schnell lässt sich der Artikel weiterverkaufen? (hoch/mittel/niedrig)
- Risiko: Betrugssignale, Defekte, iCloud-Sperren, fehlende Rechnung, Bastlerware

Antworte pro Inserat mit:
- ad_id: exakt die angegebene ID
- deal_score: 1-10 (10 = sofort kaufen, 1 = Finger weg)
- resale_estimate_eur: Zahl (EUR, gerundet)
- confidence: "hoch" | "mittel" | "niedrig"
- liquidity: "hoch" | "mittel" | "niedrig"
- condition: kurze Zustandseinschätzung (2-4 Wörter)
- red_flags: array kurzer deutscher Stichpunkte ([] wenn keine)
- reason: 1-2 Sätze, konkret und auf Deutsch, mit Zahlen
- negotiation_tip: ein konkreter Hebel für Preisverhandlung (deutsch, max 20 Wörter)

Inserate:
{payload}
"""

_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "ad_id": {"type": "STRING"},
            "deal_score": {"type": "INTEGER"},
            "resale_estimate_eur": {"type": "NUMBER"},
            "confidence": {"type": "STRING"},
            "liquidity": {"type": "STRING"},
            "condition": {"type": "STRING"},
            "red_flags": {"type": "ARRAY", "items": {"type": "STRING"}},
            "reason": {"type": "STRING"},
            "negotiation_tip": {"type": "STRING"},
        },
        "required": ["ad_id", "deal_score", "resale_estimate_eur", "reason"],
    },
}


class AIUnavailable(RuntimeError):
    pass


# Eindeutige Quelle: eine Schlüsseldatei. Env nur als CI-/Container-Pfad.
KEY_FILE_CANDIDATES = (
    "secrets/gemini.key",         # lokal & compose (eingehängt)
    "/run/secrets/gemini_key",    # docker compose secret
    "/run/secrets/GEMINI_API_KEY",
)


def key_file_paths() -> list[str]:
    """Alle existierenden Key-Dateien, ohne Duplikate."""
    paths = [os.getenv("GEMINI_API_KEY_FILE", "")] + list(KEY_FILE_CANDIDATES)
    out: list[str] = []
    for path in paths:
        if path and os.path.isfile(path) and path not in out:
            out.append(path)
    return out


def resolve_key(secrets=None) -> tuple[str, str, str]:
    """Der Gemini-Key – aus genau einer Quelle, sonst Abbruch mit Klarname.

    Returns ``(key, source, stale_note)``:

    * Quelle 1: Schlüsseldatei (``$GEMINI_API_KEY_FILE`` oder ``secrets/gemini.key``
      bzw. ``/run/secrets/gemini_key`` im Container)
    * Quelle 2: Env-Variable ``GEMINI_API_KEY`` (CI / ``docker run -e``)

    ``.streamlit/secrets.toml`` wird bewusst **nicht** mehr gelesen – dort lag der
    Key schon einmal im Docker-Image. Ein Restbestand wird als ``stale_note``
    gemeldet, damit die UI aufräumen kann.
    """
    found: list[tuple[str, str]] = []

    for path in key_file_paths():
        try:
            value = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.warning("key file %s unreadable: %s", path, exc)
            continue
        if value:
            found.append((path, value))

    env_key = os.getenv("GEMINI_API_KEY", "").strip()
    if env_key:
        found.append(("Env-Variable GEMINI_API_KEY", env_key))

    stale = ""
    try:
        source = secrets if secrets is not None else __import__("streamlit").secrets
        if "GEMINI_API_KEY" in source:
            stale = (
                "`.streamlit/secrets.toml` enthält noch einen GEMINI_API_KEY – "
                "die Datei wird nicht mehr gelesen und bitte löschen/umbenennen."
            )
    except Exception:
        pass

    if len(found) > 1:
        where = " und ".join(name for name, _ in found)
        raise AIUnavailable(
            f"Mehrere Key-Quellen gesetzt ({where}). Bitte genau eine behalten – "
            "empfohlen: `secrets/gemini.key`."
        )

    if not found:
        return "", "", stale
    (source_name, key_value), *_ = found
    return key_value, source_name, stale


def get_api_key(secrets=None) -> str:
    """Nur der Schlüsselwert – wirft AIUnavailable bei Mehrfach-Setzung."""
    return resolve_key(secrets)[0]


def make_client(api_key: str):
    try:
        from google import genai  # imported lazily: app works without the SDK
    except ImportError as exc:  # pragma: no cover
        raise AIUnavailable("google-genai ist nicht installiert.") from exc
    if not api_key:
        raise AIUnavailable("GEMINI_API_KEY fehlt.")
    return genai.Client(api_key=api_key)


_FATAL_MARKERS = (
    ("permission_denied", "API-Key abgelehnt (403). Der Schlüssel ist gesperrt oder "
     "wurde als geleakt gemeldet – neuen Key in `.streamlit/secrets.toml` oder als "
     "Env-Variable `GEMINI_API_KEY` setzen."),
    ("api key not valid", "API-Key ist ungültig – bitte prüfen/erneuern."),
    ("invalid api key", "API-Key ist ungültig – bitte prüfen/erneuern."),
    ("unauthenticated", "Zugriff verweigert (401) – API-Key prüfen."),
    ("quota", "Kontingent erschöpft – später erneut versuchen oder kleineres Modell nutzen."),
    ("429", "Zu viele Anfragen – kurz warten."),
)


def _fatal_hint(text: str) -> str:
    low = text.lower()
    for marker, hint in _FATAL_MARKERS:
        if marker in low:
            return hint
    return ""


def _generate(client, prompt: str, model: str) -> str:
    """One generation call; retries without the thinking-config if unsupported."""
    from google.genai import types

    base = dict(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SCHEMA,
            temperature=0.2,
        ),
    )
    try:
        cfg = base["config"]
        cfg.thinking_config = types.ThinkingConfig(thinking_budget=0)
        resp = client.models.generate_content(**base)
    except Exception as exc:
        hint = _fatal_hint(str(exc))
        if hint:
            raise AIUnavailable(hint) from exc
        log.debug("thinking_budget unsupported (%s) – retrying with defaults", exc)
        resp = client.models.generate_content(**base)
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Leere Antwort von Gemini.")
    return text


def _chunks(items: list[Listing], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def analyze(
    listings: list[Listing],
    *,
    query: str,
    api_key: str,
    model: str = MODEL_DEFAULT,
    client=None,
    batch_size: int = BATCH_SIZE,
    retries: int = 2,
    on_progress: Callable[[str, float], None] | None = None,
) -> dict[str, dict]:
    """Return ``{ad_id: verdict_dict}`` – missing ids simply stay unanalysed."""
    client = client or make_client(api_key)
    verdicts: dict[str, dict] = {}
    batches = list(_chunks(listings, batch_size))

    for bi, batch in enumerate(batches, start=1):
        payload = json.dumps(
            [l.to_prompt_dict() for l in batch], ensure_ascii=False
        )
        prompt = _PROMPT.format(query=query, payload=payload)

        for attempt in range(1, retries + 2):
            try:
                raw = _generate(client, prompt, model)
                data = json.loads(raw)
                if isinstance(data, dict):  # single-object safety net
                    data = [data]
                for row in data:
                    if isinstance(row, dict) and row.get("ad_id"):
                        verdicts[str(row["ad_id"])] = row
                break
            except AIUnavailable:
                raise                      # key/quota problems: fail fast, no retry
            except json.JSONDecodeError as exc:
                log.warning("batch %s: bad JSON (%s)", bi, exc)
                if attempt > retries:
                    break
                time.sleep(1.5 * attempt)
            except Exception as exc:
                hint = _fatal_hint(str(exc))
                if hint:
                    raise AIUnavailable(hint) from exc
                log.warning("batch %s attempt %s failed: %s", bi, attempt, exc)
                if attempt > retries:
                    break
                time.sleep(2.0 * attempt)

        if on_progress:
            on_progress(f"KI wertet {min(bi * batch_size, len(listings))}/{len(listings)} aus …", bi / len(batches))

    return verdicts


def merge_deals(
    listings: list[Listing],
    verdicts: dict[str, dict],
    fallback_deals: list[Deal] | None = None,
) -> list[Deal]:
    """Join AI verdicts onto listings; heuristic fills the gaps."""
    fallback = {d.listing.ad_id: d for d in (fallback_deals or [])}
    deals: list[Deal] = []
    for lst in listings:
        v = verdicts.get(lst.ad_id)
        if not v:
            deals.append(fallback.get(lst.ad_id) or Deal(listing=lst, source="none"))
            continue
        try:
            score = int(v.get("deal_score", 0))
        except (TypeError, ValueError):
            score = 0
        estimate = v.get("resale_estimate_eur")
        try:
            estimate = round(float(estimate), 2) if estimate is not None else None
        except (TypeError, ValueError):
            estimate = None
        flags = v.get("red_flags") or []
        if isinstance(flags, str):
            flags = [flags]
        deals.append(
            Deal(
                listing=lst,
                deal_score=max(1, min(10, score)) if score else 1,
                reason=str(v.get("reason", "")).strip(),
                resale_estimate_eur=estimate,
                confidence=str(v.get("confidence", "")).strip(),
                liquidity=str(v.get("liquidity", "")).strip(),
                condition=str(v.get("condition", "")).strip(),
                red_flags=tuple(str(f).strip() for f in flags if str(f).strip()),
                negotiation_tip=str(v.get("negotiation_tip", "")).strip(),
                source="ai",
            )
        )
    return deals
