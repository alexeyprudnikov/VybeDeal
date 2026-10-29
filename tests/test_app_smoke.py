"""Headless UI smoke tests via Streamlit's AppTest.

The render-path test injects a ready-made result into ``session_state`` so the
whole UI (metrics, cards, table, charts, favourites) is exercised without
touching the network. The live scrape test needs ``VYBEDEAL_ONLINE=1``.
"""

import json
import os

import pytest
from streamlit.testing.v1 import AppTest

from dealfinder.models import Deal, Listing

APP = os.path.join(os.path.dirname(__file__), "..", "app.py")
ONLINE = os.getenv("VYBEDEAL_ONLINE") == "1"


def _listing(ad_id, price, title, **kw):
    return Listing(
        ad_id=ad_id,
        title=title,
        url=f"https://www.kleinanzeigen.de/s-anzeige/{title.lower().replace(' ', '-')}/{ad_id}-173-1",
        price_eur=price,
        price_label=f"{price} € VB",
        price_type="negotiable",
        location="10115 Berlin",
        posted_date="Heute, 09:12",
        image_url="",
        description="Gepflegt, mit Rechnung. Privatverkauf.",
        attributes={"Zustand": "Sehr Gut"},
        **kw,
    )


def _fake_result():
    deals = [
        Deal(listing=_listing("111", 180, "iPhone 13 128GB"), deal_score=9,
             resale_estimate_eur=320, reason="Deutlich unter Marktniveau.",
             confidence="hoch", liquidity="hoch", condition="sehr gut",
             negotiation_tip="10 % unter Angebot bieten.", source="ai"),
        Deal(listing=_listing("222", 400, "iPhone 13 Pro – gewerblich", is_pro=True),
             deal_score=3, resale_estimate_eur=410, reason="Marge zu dünn.",
             red_flags=("gewerblicher Anbieter",), source="ai"),
    ]
    return {
        "deals": deals,
        "query": "iPhone 13",
        "ai_used": True,
        "total_market": 1234,
        "params": {
            "query": "iPhone 13", "min_price": None, "max_price": 450,
            "plz": None, "radius_km": 0, "sort": "relevance",
            "max_results": 30, "private_only": False, "exclude_pro": True,
            "hide_wanted": True, "exclude_keywords": [],
        },
        "model": "gemini-2.5-flash",
        "ai_requested": True,
    }


def _app(with_result=False):
    at = AppTest.from_file(APP, default_timeout=120)
    if with_result:
        result = _fake_result()
        at.session_state["run"] = {"params": json.dumps(result["params"]), "ai": True, "model": "gemini-2.5-flash"}
        at.session_state["result"] = result
        at.session_state["drops"] = {"111": 20.0}
    at.run()
    return at


def test_initial_render_is_clean():
    at = _app()
    assert not at.exception
    assert "VybeDeal" in at.title[0].value
    assert at.sidebar.button or at.button


def test_result_render_path(tmp_path, monkeypatch):
    import dealfinder.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store.toggle_favourite(_listing("999", 500, "Alter Favorit"))

    at = _app(with_result=True)
    assert not at.exception, at.exception
    labels = " ".join(m.label for m in at.metric)
    assert "Inserate bewertet" in labels
    assert "Preisfälle" in labels

    texts = " ".join([m.value for m in at.markdown] + [h.value for h in at.header])
    assert "iPhone 13 128GB" in texts
    assert "Deutlich unter Marktniveau" in " ".join(s.value for s in at.success)
    assert any((b.label or "").startswith("⭐ Merken") for b in at.button)
    assert at.tabs, "result tabs should render"


def test_favourite_toggle_persists(tmp_path, monkeypatch):
    import dealfinder.store as store

    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    at = _app(with_result=True)
    before = len(store.load_favourites())
    target = next(b for b in at.button if (b.label or "").startswith("⭐ Merken"))
    target.click().run()
    assert not at.exception
    assert len(store.load_favourites()) == before + 1


def test_live_scrape_and_score():
    if not ONLINE:
        pytest.skip("set VYBEDEAL_ONLINE=1 to hit kleinanzeigen.de")
    at = _app()
    # günstig & schnell halten: wenig Inserate, KI aus
    at.sidebar.slider[0].set_value(5)
    at.toggle[0].set_value(False)
    at.run()
    submit = [b for b in at.button if "Inserate laden" in (b.label or "")]
    assert submit
    submit[0].click().run()
    assert not at.exception, at.exception
    assert at.metric, "expected KPI metrics after a live run"
    assert any("Inserate bewertet" in m.label for m in at.metric)
