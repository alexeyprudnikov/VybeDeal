"""Kleinanzeigen scraper.

The site was redesigned (Tailwind classes, ``data-adid`` articles) and the old
``.aditem`` selectors return nothing — this module parses the current markup and
falls back to the legacy one so a future redesign degrades gracefully.

Politeness: requests are sequential with jitter, retries back off on
429/5xx, and detail enrichment is capped to a small worker pool.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote

from bs4 import BeautifulSoup
from curl_cffi import requests

from .models import Listing, SearchParams
from .pricing import parse_price

log = logging.getLogger(__name__)

try:  # lxml ist 3-5x schneller; html.parser ist der Fallback
    import lxml  # noqa: F401

    _HTML_PARSER = "lxml"
except ImportError:  # pragma: no cover
    _HTML_PARSER = "html.parser"

BASE_URL = "https://www.kleinanzeigen.de"
PER_PAGE = 25

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

_WANTED_RE = re.compile(
    r"^\s*(kaufe|kaufen|suche|gesuch|ankauf|wir kaufen|aufkauf)\b", re.IGNORECASE
)

_DATE_RE = re.compile(
    r"(heute|gestern|vor \d+|\d{1,2}\.\d{1,2}\.\d{2,4}|\d{1,2}\. (jan|feb|mär|apr|mai|jun|jul|aug|sep|okt|nov|dez))",
    re.IGNORECASE,
)

_SORT_SLUG = {
    "newest": "neueste",
    "price_asc": "preis-aufsteigend",
    "price_desc": "preis-absteigend",
}


class ScrapeError(RuntimeError):
    """Raised when Kleinanzeigen could not be read at all."""


@dataclass
class SearchResult:
    listings: list[Listing] = field(default_factory=list)
    total_results: int | None = None
    pages_fetched: int = 0
    urls: list[str] = field(default_factory=list)
    blocked: bool = False
    error: str = ""


# --------------------------------------------------------------------------- #
# URL building
# --------------------------------------------------------------------------- #
def build_search_url(params: SearchParams, page: int = 1) -> str:
    parts: list[str] = []
    if params.min_price or params.max_price:
        lo = params.min_price or 0
        hi = params.max_price or ""
        parts.append(f"preis:{lo}::{hi}")
    if params.plz:
        parts.append(f"plz:{params.plz}::{params.radius_km or 0}")
    if params.sort in _SORT_SLUG:
        parts.append(f"sortierung:{_SORT_SLUG[params.sort]}")
    if page > 1:
        parts.append(f"seite:{page}")

    slug = "-".join(f"s-{p}" for p in parts)
    query = quote(params.query.strip().replace(" ", "-"), safe="-")
    tail = f"/{query}/k0" if query else "/suche/k0"
    return f"{BASE_URL}/{slug}{tail}" if slug else f"{BASE_URL}/s-{query}/k0"


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _sleep_politely(min_s: float = 0.45, max_s: float = 1.1) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _get(url: str, *, retries: int = 3, timeout: int = 25):
    last_status = 0
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                url, headers=_HEADERS, impersonate="chrome120", timeout=timeout
            )
        except Exception as exc:  # network / TLS / timeout
            log.warning("request error (%s/%s) %s: %s", attempt, retries, url, exc)
            time.sleep(1.5 * attempt + random.random())
            continue

        last_status = resp.status_code
        if resp.status_code == 200:
            return resp
        if resp.status_code in (403, 429, 500, 503):
            wait = 2.0 * attempt + random.random() * 2
            log.warning("status %s for %s – backing off %.1fs", resp.status_code, url, wait)
            time.sleep(wait)
            continue
        break  # 404 etc. – retrying is pointless

    raise ScrapeError(f"HTTP {last_status or 'network error'} für {url}")


def fetch_html(url: str, *, retries: int = 3, timeout: int = 25):
    """Public wrapper: one polite GET with retries."""
    return _get(url, retries=retries, timeout=timeout)


def _looks_blocked(html: str) -> bool:
    probe = html[:4000].lower()
    return any(m in probe for m in ("captcha", "access denied", "just a moment"))


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def _json_ld_image(article) -> dict:
    script = article.find("script", type="application/ld+json")
    if not script:
        return {}
    try:
        data = json.loads(script.string or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_price_node(article) -> str:
    # current markup: <p class="my-xsmall text-title3 font-strong text-secondary">279 €</p>
    for node in article.select("p"):
        classes = " ".join(node.get("class") or [])
        if "text-title3" in classes and "font-strong" in classes:
            txt = _text(node)
            if txt:
                return txt
    # legacy markup
    legacy = article.find("p", class_="aditem-main--middle--price-shipping--price")
    return _text(legacy)


def parse_listings(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, _HTML_PARSER)
    articles = soup.select("article[data-adid]") or soup.select("article.aditem")

    out: list[Listing] = []
    for art in articles:
        href = art.get("data-href") or ""
        if not href and (a := art.select_one("h3 a[href], a.ellipsis[href]")):
            href = a.get("href", "")
        if not href.startswith("/s-anzeige/"):
            continue

        ad_id = art.get("data-adid") or href.rsplit("/", 1)[-1].split("-")[0]
        ld = _json_ld_image(art)

        title = _text(art.select_one("h3")) or ld.get("title", "")
        price_label = _parse_price_node(art)
        price_eur, price_type = parse_price(price_label)

        snippet = ""
        for p in art.select("p"):
            classes = " ".join(p.get("class") or [])
            if "text-onSurfaceSubdued" in classes and _text(p):
                snippet = _text(p)
                break
        if not snippet:
            snippet = ld.get("description", "")[:200]

        # top row: location span, then date span
        location, posted, plz = "", "", ""
        top = art.select_one("div.items-start") or art.select_one(
            ".aditem-main--top"
        )
        if top:
            spans = [_text(s) for s in top.select("span") if _text(s)]
            if spans:
                location = spans[0]
                parts = location.split(" ", 1)
                if parts and parts[0].isdigit():
                    plz, location = parts[0], (parts[1] if len(parts) > 1 else "")
            if len(spans) > 1:
                posted = spans[-1]
        if not posted:
            # PRO/shop ads show the shop name instead of a date – scan the whole card
            for span in art.select("span"):
                txt = _text(span)
                if txt and len(txt) < 32 and _DATE_RE.search(txt):
                    posted = txt
                    break

        img = art.select_one("img")
        image_url = (img.get("src") if img else "") or ld.get("contentUrl", "")

        pro_node = art.select_one("[data-gaevent*='partneradid'], [title^='Zum shop']")
        seller = ""
        if pro_node and (shop := pro_node.select_one("span")):
            seller = _text(shop)

        shipping = ""
        for span in art.select("span"):
            txt = _text(span)
            if txt and "Versand" in txt:
                shipping = txt
                break

        out.append(
            Listing(
                ad_id=ad_id,
                title=title,
                url=BASE_URL + href,
                price_label=price_label,
                price_eur=price_eur,
                price_type=price_type,
                location=location,
                plz=plz,
                posted_date=posted,
                snippet=snippet,
                image_url=image_url,
                is_pro=bool(pro_node) or bool(seller),
                seller=seller,
                shipping=shipping,
                is_wanted=bool(_WANTED_RE.match(title or "")),
            )
        )
    return out


def parse_total_results(html: str) -> int | None:
    soup = BeautifulSoup(html, _HTML_PARSER)
    node = soup.select_one("#srp-breadcrumb-summary")
    if not node:
        return None
    txt = _text(node)
    # "1 - 25 von 20.551 Ergebnissen für …"
    if "von" in txt:
        chunk = txt.split("von", 1)[1].strip().split()[0]
        try:
            return int(chunk.replace(".", ""))
        except ValueError:
            return None
    return None


def parse_detail(html: str, listing: Listing) -> Listing:
    """Merge detail-page data (full description + attribute table) into a listing."""
    soup = BeautifulSoup(html, _HTML_PARSER)

    desc = soup.select_one("#viewad-description-text")
    if desc:
        listing.description = desc.get_text(" ", strip=True)
        listing.detail_ok = True

    title = soup.select_one("#viewad-title")
    if title and not listing.title:
        listing.title = _text(title)

    price_node = soup.select_one("#viewad-price")
    if price_node:
        label = _text(price_node)
        amount, kind = parse_price(label)
        if amount is not None or kind != "unknown":
            listing.price_label = label or listing.price_label
            listing.price_eur, listing.price_type = amount, kind

    extra = soup.select_one("#viewad-extra-info")
    if extra:
        listing.posted_date = _text(extra) or listing.posted_date

    attrs: dict[str, str] = {}
    for li in soup.select("#viewad-details li"):
        if not _text(li):
            continue
        value_node = li.select_one(
            "span.addetailslist--detail--value, span[itemprop], span"
        )
        if value_node:
            value = value_node.get_text(" ", strip=True)
            value_node.extract()
            label = li.get_text(" ", strip=True)
        elif ":" in _text(li):
            label, _, value = _text(li).partition(":")
        else:
            continue
        label, value = label.strip(), value.strip()
        if label and value and label != value:
            attrs[label] = value
    if attrs:
        listing.attributes = attrs

    if not listing.image_url:
        first = soup.select_one("#viewad-gallery img[src], #viewad-pictureContainer img[src]")
        if first:
            listing.image_url = first.get("src", "")
    return listing


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def enrich_listing(listing: Listing, *, delay: float = 0.35) -> Listing:
    if not listing.url:
        return listing
    if delay:
        time.sleep(random.uniform(delay * 0.5, delay * 1.5))
    try:
        resp = _get(listing.url, retries=2)
        parse_detail(resp.text, listing)
    except Exception as exc:  # a single dead ad must not kill the run
        log.info("detail fetch failed for %s: %s", listing.ad_id, exc)
    return listing


def search(
    params: SearchParams,
    *,
    enrich: bool = True,
    workers: int = 3,
    on_progress=None,
) -> SearchResult:
    """Scrape (and optionally enrich) listings for ``params``."""
    result = SearchResult()
    wanted = max(1, min(params.max_results, 120))
    max_pages = (wanted + PER_PAGE - 1) // PER_PAGE

    for page in range(1, max_pages + 1):
        url = build_search_url(params, page)
        if on_progress:
            on_progress(f"Seite {page} wird geladen …", page / max_pages)
        try:
            resp = _get(url)
        except ScrapeError as exc:
            result.error = str(exc)
            result.blocked = page == 1
            if page == 1:
                return result
            break

        html = resp.text
        if _looks_blocked(html):
            result.error = "Kleinanzeigen hat geantwortet, aber ohne Ergebnis-Liste (Bot-Schutz?)."
            result.blocked = True
            return result

        result.urls.append(url)
        result.pages_fetched = page
        if result.total_results is None:
            result.total_results = parse_total_results(html)

        page_items = parse_listings(html)
        if not page_items:
            break
        result.listings.extend(page_items)
        if len(result.listings) >= wanted:
            break
        if page < max_pages:
            _sleep_politely()

    result.listings = result.listings[:wanted]

    # ---- filters that the URL cannot express
    if params.exclude_pro:
        result.listings = [l for l in result.listings if not l.is_pro]
    if params.private_only:
        result.listings = [l for l in result.listings if not l.is_pro and not l.seller]
    if params.hide_wanted:
        result.listings = [l for l in result.listings if not l.is_wanted]
    if params.exclude_keywords:
        keys = [k.strip().lower() for k in params.exclude_keywords if k.strip()]
        result.listings = [
            l
            for l in result.listings
            if not any(k in (l.title + " " + l.snippet).lower() for k in keys)
        ]

    if enrich and result.listings:
        if on_progress:
            on_progress("Detailseiten werden gelesen …", 0.9)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(lambda l: enrich_listing(l), result.listings))

    return result
