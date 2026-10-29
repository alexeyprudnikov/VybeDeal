"""Heuristic (no-API-key) deal scoring.

This is the fallback that keeps VybeDeal useful without a Gemini key: it scores
each listing against the *cohort* it was found in (price percentile), plus
condition / seller / freshness signals from the title, attributes and text.

It is deliberately transparent — every score comes with the signals that caused
it — so it can be compared against the AI verdict later.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from .models import Deal, Listing

POSITIVE = {
    "ovp": 0.6, "originalverpackung": 0.6, "verpackung": 0.3,
    "rechnung": 0.7, "rechnung vorhanden": 0.6, "kas": 0.4,
    "neuwertig": 0.8, "wie neu": 0.7, "sehr gut": 0.5, "top zustand": 0.5,
    "gepflegt": 0.3, " Garantie": 0.5, "gewährleistung": 0.5,
    "zubehör": 0.2, "kabel": 0.1, "Hülle": 0.1, "Schutzfolie": 0.1,
    "unbeschädigt": 0.3, "funktioniert einwandfrei": 0.4,
}

NEGATIVE = {
    "defekt": -3.0, "defekt ": -2.5, "bastler": -2.5, "ersatzteil": -2.5,
    "ersatzteile": -2.5, "displaybruch": -1.8, "display gebrochen": -1.8,
    "sprung": -1.2, "kratz": -0.4, "gebiss": -0.3, "akku": -0.6,
    "icloud": -2.0, "sim-lock": -1.5, "simlock": -1.5, "brandneu": 0.0,
    "nur abholung": -0.3, "wie gesehen": -0.6, "umtausch": -0.5,
    "reparatur": -1.0, "wasserschaden": -2.2, "liquid": -1.5,
    "ohne zubehör": -0.4, "nicht funktionsfähig": -3.0,
    "nicht funktionstüchtig": -3.0, "verschleiß": -0.4,
}

_GOOD_CONDITION = ("neuwertig", "sehr gut", "wie neu", "top zustand", "gut")


def _haystack(listing: Listing) -> str:
    parts = [listing.title, listing.description or listing.snippet]
    parts += [f"{k} {v}" for k, v in listing.attributes.items()]
    return " ".join(p for p in parts if p).lower()


def _median(values: Iterable[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.median(vals) if vals else None


def heuristic_estimate(listing: Listing, cohort_median: float | None) -> float | None:
    """Market proxy: cohort median, nudged by condition & included extras."""
    if listing.price_eur is None:
        return None
    base = cohort_median if cohort_median else listing.price_eur
    text = _haystack(listing)

    factor = 1.0
    for kw, weight in POSITIVE.items():
        if kw.lower() in text:
            factor += weight * 0.08
    for kw, weight in NEGATIVE.items():
        if kw.strip().lower() in text:
            factor += weight * 0.12
    cond = " ".join(listing.attributes.values()).lower()
    if any(c in cond for c in _GOOD_CONDITION):
        factor += 0.06
    if listing.is_pro:
        factor -= 0.05

    estimate = max(base * factor, listing.price_eur * 0.5)
    return round(estimate)


def score_listing(
    listing: Listing,
    *,
    cohort_median: float | None = None,
    cohort_prices: list[float] | None = None,
) -> Deal:
    price = listing.price_eur
    score = 5.0
    flags: list[str] = []
    notes: list[str] = []
    text = _haystack(listing)

    # 1) price position inside the cohort
    if price is not None and cohort_prices and len(cohort_prices) >= 4:
        ordered = sorted(v for v in cohort_prices if v is not None)
        below = sum(1 for v in ordered if v <= price)
        pct = below / len(ordered)
        score += (0.5 - pct) * 5.0
        median = cohort_median or _median(ordered)
        if median and price < 0.75 * median:
            notes.append(f"{price:.0f} € liegt {100 - round(price / median * 100)} % unter dem Angebotsmedian ({median:.0f} €)")
        elif median and price > 1.25 * median:
            flags.append(f"teurer als der Angebotsmedian ({median:.0f} €)")

    # 2) condition & completeness signals
    pos_hits = [k for k in POSITIVE if k.strip().lower() in text]
    neg_hits = [k.strip() for k in NEGATIVE if k.strip().lower() in text and k.strip()]
    for kw in pos_hits:
        score += POSITIVE.get(kw, POSITIVE.get(kw + " ", 0.2))
    for kw in neg_hits:
        score += NEGATIVE.get(kw, NEGATIVE.get(kw + " ", -1.0))
        if NEGATIVE.get(kw, NEGATIVE.get(kw + " ", 0)) <= -1.2:
            flags.append(f"„{kw}“ im Text")
    if pos_hits:
        notes.append("Zustands-Signale: " + ", ".join(sorted(set(pos_hits))[:4]))

    # 3) seller type & negotiation headroom
    if listing.is_pro:
        score -= 1.2
        flags.append("Gewerblicher Anbieter (Marge meist dünn)")
    else:
        score += 0.6
    if listing.price_type == "negotiable":
        score += 0.5
        notes.append("VB – Preis ist verhandelbar")
    if listing.price_type == "free":
        score += 1.0
    if "versand" in (listing.shipping or "").lower():
        score += 0.4

    # 4) freshness (fresh ads still have room to talk)
    posted = (listing.posted_date or "").lower()
    if "heute" in posted or "gestern" in posted:
        score += 0.5
    elif any(u in posted for u in ("monat", "jahr", "vor 3", "vor 4")):
        score -= 0.4
        notes.append("Anzeige ist alt – Verkäufer oft gesprächsbereit")

    # 5) condition attribute
    cond = ""
    for key, val in listing.attributes.items():
        if "zustand" in key.lower():
            cond = val
            break
    if cond.lower() in ("neuwertig", "sehr gut"):
        score += 0.4
    elif cond.lower() in ("defekt", "stark gebraucht", "beschädigt"):
        score -= 1.5
        flags.append(f"Zustand: {cond}")

    deal_score = int(max(1, min(10, round(score))))
    reason = "; ".join(notes[:3]) or "Keine besonderen Signale – Preis liegt im Bereich der Suchergebnisse."
    if flags:
        reason = (reason + " | Achtung: " + ", ".join(flags[:3])).strip(" |")

    return Deal(
        listing=listing,
        deal_score=deal_score,
        reason=reason,
        resale_estimate_eur=heuristic_estimate(listing, cohort_median),
        confidence="niedrig",
        liquidity="mittel",
        condition=cond,
        red_flags=tuple(dict.fromkeys(flags)),
        negotiation_tip="VB-Anzeige: direkt einen Gegenpreis ~10 % unter Angebot ansetzen."
        if listing.price_type == "negotiable"
        else "",
        source="heuristic",
    )


def score_cohort(listings: list[Listing]) -> list[Deal]:
    prices = [l.price_eur for l in listings if l.price_eur]
    median = _median(prices)
    return [
        score_listing(l, cohort_median=median, cohort_prices=prices) for l in listings
    ]
