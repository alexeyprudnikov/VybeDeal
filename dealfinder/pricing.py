"""German price-string parsing and deal math."""

from __future__ import annotations

import re

_NUM = re.compile(r"(\d{1,3}(?:\.\d{3})*|\d+)(?:[,\.](\d{1,2}))?")

#: Labels that carry no usable number.
_NO_PRICE = ("verhandlung", "zu verschenken", "verschenken", "fp", "fpk", "nvp", "gb")


def parse_price(text: str | None) -> tuple[float | None, str]:
    """Parse a Kleinanzeigen price label into ``(amount_eur, price_type)``.

    Handles ``279 €``, ``210 € VB``, ``1.299,50 €``, ``ab 100 €``,
    ``Verhandlung``, ``Zu verschenken``, ``0 €``.
    """
    if not text:
        return None, "unknown"

    raw = " ".join(text.split()).strip()
    low = raw.lower()

    if any(marker in low for marker in _NO_PRICE) and not _NUM.search(raw.replace(".", "")):
        if "verschenken" in low:
            return 0.0, "free"
        return None, "on_request"

    match = _NUM.search(raw)
    if not match:
        if "verschenken" in low:
            return 0.0, "free"
        return None, "unknown"

    whole = match.group(1).replace(".", "")
    decimals = match.group(2) or "0"
    amount = float(f"{whole}.{decimals}")

    if "vb" in low or "verhandl" in low:
        kind = "negotiable"
    elif low.startswith("ab") or "ab " in low:
        kind = "from"
    elif amount == 0:
        kind = "free"
    else:
        kind = "fixed"
    return amount, kind


def fmt_eur(amount: float | None) -> str:
    """``312.5 -> '313 €'`` (German style, no false precision)."""
    if amount is None:
        return "–"
    if float(amount).is_integer():
        return f"{int(amount):,d} €".replace(",", ".")
    return f"{amount:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def score_badge(score: int | None) -> str:
    """Emoji badge for a 1-10 deal score."""
    if score is None:
        return "⚪"
    if score >= 8:
        return "🔥"
    if score >= 6:
        return "✅"
    if score >= 4:
        return "😐"
    return "🚫"
