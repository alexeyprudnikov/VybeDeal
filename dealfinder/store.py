"""Tiny JSON-on-disk store: favourites, saved searches, price history.

No database – a handful of small files under ``data/`` (git-ignored) that a
single Streamlit process can read/write safely, and that a user can inspect or
delete by hand.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .models import Deal, Listing

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("VYBEDEAL_DATA_DIR", "data"))


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M")


def _path(name: str) -> Path:
    return DATA_DIR / name


def _load(name: str, default):
    path = _path(name)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("could not read %s: %s", path, exc)
        return default


def _save(name: str, payload) -> None:
    path = _path(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as exc:  # read-only volume etc.
        log.warning("could not write %s: %s", path, exc)


# --------------------------------------------------------------------------- #
# Favourites / watchlist
# --------------------------------------------------------------------------- #
def load_favourites() -> dict[str, dict]:
    return _load("favourites.json", {})


def toggle_favourite(listing: Listing, note: str = "") -> bool:
    """Add/remove; returns True when the listing is now a favourite."""
    favs = load_favourites()
    if listing.ad_id in favs:
        favs.pop(listing.ad_id, None)
        _save("favourites.json", favs)
        return False
    favs[listing.ad_id] = {
        "ad_id": listing.ad_id,
        "title": listing.title,
        "url": listing.url,
        "price_eur": listing.price_eur,
        "price_label": listing.price_label,
        "image_url": listing.image_url,
        "location": listing.location,
        "note": note,
        "added_at": _now(),
    }
    _save("favourites.json", favs)
    return True


def is_favourite(ad_id: str) -> bool:
    return ad_id in load_favourites()


# --------------------------------------------------------------------------- #
# Price history (detects price drops between runs)
# --------------------------------------------------------------------------- #
def load_history() -> dict[str, dict]:
    return _load("price_history.json", {})


def record_sightings(listings: list[Listing]) -> dict[str, dict]:
    """Store the price of every listing seen now; returns previous state."""
    history = load_history()
    previous = {k: dict(v) for k, v in history.items()}
    stamp = _now()
    for l in listings:
        if l.price_eur is None:
            continue
        entry = history.setdefault(
            l.ad_id, {"title": l.title, "url": l.url, "prices": []}
        )
        entry.update({"title": l.title, "url": l.url, "last_seen": stamp})
        entry.setdefault("first_seen", stamp)
        entry["last_price"] = l.price_eur
        points = entry["prices"]
        if not points or points[-1].get("price") != l.price_eur:
            points.append({"at": stamp, "price": l.price_eur})
            entry["prices"] = points[-40:]
    _save("price_history.json", history)
    return previous


def price_drops(deals: list[Deal], previous: dict[str, dict]) -> dict[str, float]:
    """``{ad_id: drop_in_eur}`` for listings that got cheaper since last seen."""
    drops: dict[str, float] = {}
    for d in deals:
        ad_id = d.listing.ad_id
        old = (previous.get(ad_id) or {}).get("last_price")
        new = d.listing.price_eur
        if old and new is not None and new < old:
            drops[ad_id] = round(old - new, 2)
    return drops


# --------------------------------------------------------------------------- #
# Saved searches
# --------------------------------------------------------------------------- #
def load_searches() -> list[dict]:
    return _load("saved_searches.json", [])


def save_search(name: str, params: dict) -> list[dict]:
    items = [s for s in load_searches() if s.get("name") != name]
    items.insert(0, {"name": name, "params": params, "saved_at": _now()})
    _save("saved_searches.json", items[:20])
    return items[:20]


def delete_search(name: str) -> list[dict]:
    items = [s for s in load_searches() if s.get("name") != name]
    _save("saved_searches.json", items)
    return items
