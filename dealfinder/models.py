"""Data models shared across the VybeDeal pipeline.

Everything is plain ``dataclasses`` + ``dict`` so it round-trips cleanly through
JSON (cache, disk persistence, LLM payloads) without extra dependencies.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SearchParams:
    """A fully resolved search request against Kleinanzeigen."""

    query: str
    min_price: int | None = None
    max_price: int | None = None
    plz: str | None = None
    radius_km: int = 0
    sort: str = "relevance"          # relevance | newest | price_asc | price_desc
    max_results: int = 30
    private_only: bool = False
    exclude_pro: bool = True
    hide_wanted: bool = True
    exclude_keywords: tuple[str, ...] = ()

    def key(self) -> str:
        """Stable cache key for the *scrape* step."""
        return "|".join(
            [
                self.query.strip().lower(),
                str(self.min_price or ""),
                str(self.max_price or ""),
                str(self.plz or ""),
                str(self.radius_km),
                self.sort,
                str(self.max_results),
                "priv" if self.private_only else "all",
                "nopro" if self.exclude_pro else "pro",
                "nowanted" if self.hide_wanted else "wanted",
                ",".join(sorted(k.lower() for k in self.exclude_keywords)),
            ]
        )


@dataclass
class Listing:
    """One Kleinanzeigen ad, enriched with detail-page data."""

    ad_id: str
    title: str
    url: str
    price_label: str = ""
    price_eur: float | None = None
    price_type: str = "unknown"      # fixed | negotiable | from | free | on_request | unknown
    location: str = ""
    plz: str = ""
    posted_date: str = ""
    snippet: str = ""
    description: str = ""
    attributes: dict[str, str] = field(default_factory=dict)
    image_url: str = ""
    is_pro: bool = False
    seller: str = ""
    shipping: str = ""
    is_wanted: bool = False
    detail_ok: bool = False

    def to_prompt_dict(self) -> dict[str, Any]:
        """Compact view sent to the LLM (no image/url noise)."""
        return {
            "ad_id": self.ad_id,
            "title": self.title,
            "price": self.price_label or (f"{self.price_eur} €" if self.price_eur else ""),
            "price_eur": self.price_eur,
            "price_type": self.price_type,
            "location": self.location,
            "posted_date": self.posted_date,
            "seller": "commercial" if self.is_pro else "private",
            "shipping": self.shipping,
            "attributes": self.attributes,
            "description": (self.description or self.snippet)[:1500],
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Deal:
    """A listing joined with its evaluation (AI or heuristic)."""

    listing: Listing
    deal_score: int = 0
    reason: str = ""
    resale_estimate_eur: float | None = None
    confidence: str = ""
    liquidity: str = ""
    condition: str = ""
    red_flags: tuple[str, ...] = ()
    negotiation_tip: str = ""
    source: str = "heuristic"        # ai | heuristic

    # ---------------------------------------------------------------- metrics
    @property
    def margin_eur(self) -> float | None:
        if self.resale_estimate_eur is None or self.listing.price_eur is None:
            return None
        return round(self.resale_estimate_eur - self.listing.price_eur, 2)

    @property
    def roi_pct(self) -> float | None:
        margin = self.margin_eur
        price = self.listing.price_eur
        if margin is None or not price:
            return None
        return round(margin / price * 100, 1)

    def to_row(self) -> dict[str, Any]:
        """Flat dict for tables / CSV export."""
        lst = self.listing
        return {
            "score": self.deal_score,
            "title": lst.title,
            "price_eur": lst.price_eur,
            "price": lst.price_label,
            "resale_eur": self.resale_estimate_eur,
            "margin_eur": self.margin_eur,
            "roi_pct": self.roi_pct,
            "confidence": self.confidence,
            "liquidity": self.liquidity,
            "condition": self.condition,
            "seller": "Gewerblich" if lst.is_pro else "Privat",
            "location": lst.location,
            "posted_date": lst.posted_date,
            "red_flags": "; ".join(self.red_flags),
            "reason": self.reason,
            "negotiation_tip": self.negotiation_tip,
            "source": self.source,
            "price_drop_eur": None,  # filled in by the UI/export from price history
            "url": lst.url,
        }
