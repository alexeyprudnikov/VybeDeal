from dealfinder.models import Deal, Listing
from dealfinder.scoring import heuristic_estimate, score_cohort, score_listing


def listing(price, **kw):
    return Listing(ad_id=kw.pop("ad_id", str(price)), title=kw.pop("title", f"Artikel {price}"),
                   url="https://x", price_eur=price, price_label=f"{price} €", **kw)


def test_cheap_item_outranks_expensive_one_in_same_cohort():
    cohort = [listing(p) for p in (100, 110, 120, 130, 400)]
    deals = {d.listing.price_eur: d for d in score_cohort(cohort)}
    assert deals[100].deal_score > deals[400].deal_score


def test_defect_keywords_become_red_flags():
    deal = score_listing(listing(100, title="iPhone 13 DEFekt Bastlerware"), cohort_prices=[100, 110, 120], cohort_median=110)
    assert deal.red_flags
    assert deal.deal_score <= 5


def test_good_condition_and_receipt_lift_score():
    plain = score_listing(listing(100, title="iPhone 13"))
    nice = score_listing(listing(100, title="iPhone 13 wie neu mit Rechnung OVP"))
    assert nice.deal_score > plain.deal_score


def test_commercial_seller_is_penalised():
    priv = score_listing(listing(100))
    pro = score_listing(listing(100, is_pro=True, seller="Shop"))
    assert priv.deal_score > pro.deal_score
    assert any("Gewerb" in f for f in pro.red_flags)


def test_negotiable_price_noted():
    deal = score_listing(listing(100, price_type="negotiable"))
    assert "verhandelbar" in deal.reason


def test_score_stays_in_1_10():
    for price in (0, 1, 5000):
        item = listing(price)
        deal = score_listing(item, cohort_prices=[100, 200, 300], cohort_median=200)
        assert 1 <= deal.deal_score <= 10


def test_estimate_uses_cohort_median():
    est = heuristic_estimate(listing(100), 300)
    assert est and est > 100


def test_estimate_none_without_price():
    assert heuristic_estimate(listing(None), 300) is None


def test_margin_and_roi_math():
    deal = Deal(listing=listing(200), deal_score=7, resale_estimate_eur=300)
    assert deal.margin_eur == 100
    assert deal.roi_pct == 50.0
    assert Deal(listing=listing(200)).margin_eur is None


def test_to_row_has_export_columns():
    row = Deal(listing=listing(200), deal_score=7, resale_estimate_eur=260).to_row()
    for key in ("score", "title", "price_eur", "margin_eur", "roi_pct", "url"):
        assert key in row
