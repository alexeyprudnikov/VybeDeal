"""Export helpers – CSV / JSON downloads for the result table."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

from .models import Deal

COLUMNS = [
    "score", "title", "price", "price_eur", "resale_eur", "margin_eur",
    "roi_pct", "condition", "confidence", "liquidity", "seller", "location",
    "posted_date", "price_drop_eur", "red_flags", "reason",
    "negotiation_tip", "source", "url",
]


def to_csv(deals: list[Deal], drops: dict[str, float] | None = None) -> bytes:
    drops = drops or {}
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for deal in deals:
        row = deal.to_row()
        row["price_drop_eur"] = drops.get(deal.listing.ad_id, "")
        writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")  # BOM -> Excel reads umlauts


def to_json(deals: list[Deal], drops: dict[str, float] | None = None) -> bytes:
    drops = drops or {}
    payload = []
    for deal in deals:
        row = deal.to_row()
        row["ad_id"] = deal.listing.ad_id
        row["description"] = (deal.listing.description or deal.listing.snippet)[:1200]
        row["attributes"] = deal.listing.attributes
        row["price_drop_eur"] = drops.get(deal.listing.ad_id)
        payload.append(row)
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M")
