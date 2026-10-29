from dealfinder.models import Listing, SearchParams
from dealfinder.scraper import (
    build_search_url,
    parse_detail,
    parse_listings,
    parse_total_results,
)


# --------------------------------------------------------------------- parsing
def test_parse_listings_reads_current_markup(search_html):
    items = parse_listings(search_html)
    assert items, "current Kleinanzeigen markup must yield listings"
    first = items[0]
    assert first.ad_id and first.url.startswith("https://www.kleinanzeigen.de/s-anzeige/")
    assert first.title
    assert first.price_eur and first.price_type in {"fixed", "negotiable", "from"}
    assert first.location
    assert first.plz == "10247"
    # PRO-Anzeigen zeigen den Shopnamen statt eines Datums – deshalb eines der
    # Privat-Inserate hier als Anker
    private = [i for i in items if not i.is_pro]
    assert private and private[0].posted_date
    assert any(i.shipping for i in items)
    assert first.image_url.startswith("http")


def test_parse_listings_flags_commercial_ads(search_html):
    items = parse_listings(search_html)
    assert any(i.is_pro for i in items), "PRO/shop listing should be flagged"


def test_parse_listings_snippet_present(search_html):
    assert all(i.snippet for i in parse_listings(search_html))


def test_parse_total_results(search_html):
    assert parse_total_results(search_html) == 20551


def test_parse_listings_ignores_non_ad_articles():
    assert parse_listings("<html><body><article>xyz</article></body></html>") == []


def test_parse_detail_merges_description_and_attributes(detail_html):
    listing = Listing(ad_id="3555397986", title="", url="https://x")
    parse_detail(detail_html, listing)
    assert listing.detail_ok
    assert "iphone 13" in listing.description.lower()
    assert listing.attributes.get("Marke") == "Apple"
    assert listing.attributes.get("Zustand") == "Sehr Gut"
    assert listing.price_eur == 210.0
    assert listing.price_type == "negotiable"
    assert listing.posted_date == "29.10.2026"


def test_parse_detail_tolerates_empty_page():
    listing = Listing(ad_id="1", title="T", url="u")
    parse_detail("<html></html>", listing)
    assert not listing.detail_ok
    assert listing.attributes == {}


# ------------------------------------------------------------------------ URLs
def test_url_price_filter_only():
    p = SearchParams(query="iPhone 13", max_price=450)
    assert build_search_url(p) == "https://www.kleinanzeigen.de/s-preis:0::450/iPhone-13/k0"


def test_url_combines_filters_and_page():
    p = SearchParams(
        query="ps5 slim", min_price=100, max_price=300, plz="10115",
        radius_km=20, sort="newest", max_results=60,
    )
    url = build_search_url(p, page=2)
    assert "s-preis:100::300" in url
    assert "s-plz:10115::20" in url
    assert "s-sortierung:neueste" in url
    assert "s-seite:2" in url
    assert url.endswith("/ps5-slim/k0")


def test_url_is_escaped():
    url = build_search_url(SearchParams(query="fahrrad & co"))
    assert "%26" in url


def test_params_cache_key_changes_with_filters():
    base = SearchParams(query="x")
    assert base.key() != SearchParams(query="x", max_price=10).key()
    assert base.key() == SearchParams(query="x").key()
