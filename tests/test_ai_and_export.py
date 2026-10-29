import json

import pytest

from dealfinder.ai import merge_deals
from dealfinder.export import to_csv, to_json
from dealfinder.models import Deal, Listing


def L(ad_id, price=100, title="Artikel"):
    return Listing(ad_id=ad_id, title=title, url=f"https://x/{ad_id}", price_eur=price, price_label=f"{price} €")


def test_verdicts_join_by_ad_id_only():
    listings = [L("1"), L("2")]
    verdicts = {
        "1": {"ad_id": "1", "deal_score": 9, "resale_estimate_eur": 250.0,
              "reason": "günstig", "red_flags": ["iCloud prüfen"],
              "confidence": "hoch", "liquidity": "hoch", "condition": "sehr gut",
              "negotiation_tip": "10 % unter Angebot"}
    }
    deals = merge_deals(listings, verdicts)
    assert deals[0].source == "ai" and deals[0].deal_score == 9
    assert deals[0].margin_eur == 150 and deals[0].roi_pct == 150.0
    assert deals[0].red_flags == ("iCloud prüfen",)
    # listing 2 got no verdict and no fallback -> unanalysed, never dropped
    assert deals[1].source == "none" and deals[1].deal_score == 0


def test_fallback_fills_missing_verdicts():
    listings = [L("1"), L("2")]
    fallback = [Deal(listing=L("1"), deal_score=4, source="heuristic"),
                Deal(listing=L("2"), deal_score=6, source="heuristic")]
    deals = merge_deals(listings, {"2": {"ad_id": "2", "deal_score": 8, "resale_estimate_eur": 200, "reason": "ok"}}, fallback)
    assert deals[0].source == "heuristic"
    assert deals[1].source == "ai"


def test_junk_verdict_values_are_survivable():
    deals = merge_deals([L("1")], {"1": {"ad_id": "1", "deal_score": "acht", "resale_estimate_eur": "keine Angabe", "reason": None, "red_flags": "ein Flag"}})
    d = deals[0]
    assert d.deal_score == 1
    assert d.resale_estimate_eur is None
    assert d.red_flags == ("ein Flag",)


def test_scores_are_clamped():
    d = merge_deals([L("1")], {"1": {"ad_id": "1", "deal_score": 42, "resale_estimate_eur": 5, "reason": "x"}})[0]
    assert d.deal_score == 10


def test_csv_has_bom_and_rows():
    deals = [Deal(listing=L("1", 120, "Grüße"), deal_score=7, resale_estimate_eur=200)]
    blob = to_csv(deals, {"1": 20.0})
    assert blob.startswith(b"\xef\xbb\xbf")          # Excel + Umlaute
    text = blob.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("score,title")
    assert "Grüße" in text and "20.0" in text


def test_json_export_is_roundtrippable():
    deals = [Deal(listing=L("1"), deal_score=5, resale_estimate_eur=180, reason="okay")]
    payload = json.loads(to_json(deals, {"1": 5.0}))
    assert payload[0]["ad_id"] == "1"
    assert payload[0]["margin_eur"] == 80
    assert payload[0]["price_drop_eur"] == 5.0


def test_fatal_api_errors_are_recognised():
    from dealfinder.ai import _fatal_hint

    assert "gesperrt" in _fatal_hint("403 PERMISSION_DENIED. reported as leaked")
    assert _fatal_hint("api key not valid").startswith("API-Key ist ungültig")
    assert _fatal_hint("rate limit exceeded (429)")
    assert _fatal_hint("some transient socket error") == ""


def test_analyze_fails_fast_on_dead_key():
    from dealfinder.ai import AIUnavailable, analyze

    class DeadClient:
        class models:
            @staticmethod
            def generate_content(**kw):
                raise RuntimeError("403 PERMISSION_DENIED: key reported as leaked")

    with pytest.raises(AIUnavailable):
        analyze([L("1")], query="x", api_key="", client=DeadClient())


def test_analyze_retries_transient_errors_then_keeps_going():
    from dealfinder.ai import analyze

    calls = {"n": 0}

    class Flaky:
        class models:
            @staticmethod
            def generate_content(**kw):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("connection reset")
                return type("R", (), {"text": '[{"ad_id":"1","deal_score":6,"resale_estimate_eur":150,"reason":"ok"}]'})()

    verdicts = analyze([L("1")], query="x", api_key="", client=Flaky(), retries=1)
    assert verdicts["1"]["deal_score"] == 6


def test_key_from_file_is_the_single_source(tmp_path, monkeypatch):
    from dealfinder.ai import resolve_key

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_FILE", raising=False)
    key_file = tmp_path / "gemini.key"
    key_file.write_text("  from-file  \n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY_FILE", str(key_file))

    key, source, stale = resolve_key({})
    assert key == "from-file"                      # whitespace wird entfernt
    assert source.endswith("gemini.key")
    assert stale == ""


def test_env_var_works_alone(tmp_path, monkeypatch):
    from dealfinder.ai import resolve_key

    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    monkeypatch.delenv("GEMINI_API_KEY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)                    # kein secrets/gemini.key hier
    assert resolve_key({})[:2] == ("from-env", "Env-Variable GEMINI_API_KEY")


def test_two_sources_abort_instead_of_guessing(tmp_path, monkeypatch):
    from dealfinder.ai import AIUnavailable, resolve_key

    key_file = tmp_path / "gemini.key"
    key_file.write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")

    with pytest.raises(AIUnavailable) as err:
        resolve_key({})
    assert "Mehrere Key-Quellen" in str(err.value)
    assert "gemini.key" in str(err.value) and "GEMINI_API_KEY" in str(err.value)


def test_secrets_toml_is_no_longer_a_source(tmp_path, monkeypatch):
    """Der alte Ort liefert keinen Key mehr, wird aber als Aufräum-Hinweis gemeldet."""
    from dealfinder.ai import resolve_key

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    key, source, stale = resolve_key({"GEMINI_API_KEY": "alt"})
    assert key == "" and source == ""
    assert "secrets.toml" in stale


def test_no_source_at_all(tmp_path, monkeypatch):
    from dealfinder.ai import resolve_key

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    assert resolve_key({}) == ("", "", "")
