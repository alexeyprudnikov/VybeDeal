from dealfinder import store
from dealfinder.models import Listing


def L(ad_id, price, title="Artikel"):
    return Listing(ad_id=ad_id, title=title, url=f"https://x/{ad_id}", price_eur=price)


def test_favourite_toggle_roundtrip(tmp_data):
    lst = L("1", 100)
    assert store.toggle_favourite(lst) is True
    assert store.is_favourite("1")
    assert store.load_favourites()["1"]["price_eur"] == 100
    assert store.toggle_favourite(lst) is False
    assert not store.is_favourite("1")


def test_history_records_first_and_last_price(tmp_data):
    previous = store.record_sightings([L("1", 200)])
    assert previous == {}                      # first run: nothing known yet
    previous = store.record_sightings([L("1", 170)])
    assert previous["1"]["last_price"] == 200  # what we knew *before* this run

    history = store.load_history()
    assert [p["price"] for p in history["1"]["prices"]] == [200, 170]


def test_history_does_not_duplicate_equal_prices(tmp_data):
    store.record_sightings([L("1", 200)])
    store.record_sightings([L("1", 200)])
    assert len(store.load_history()["1"]["prices"]) == 1


def test_price_drops_only_reports_decreases(tmp_data):
    store.record_sightings([L("1", 200), L("2", 50)])
    previous = store.record_sightings([L("2", 60)])   # state as known *before* this run
    from dealfinder.models import Deal

    deals = [Deal(listing=L("1", 150)), Deal(listing=L("2", 60))]
    assert store.price_drops(deals, previous) == {"1": 50.0}


def test_saved_searches_replace_same_name(tmp_data):
    store.save_search("iphone", {"query": "iphone"})
    store.save_search("iphone", {"query": "iphone 15"})
    store.save_search("rad", {"query": "rad"})
    items = store.load_searches()
    assert len(items) == 2
    assert next(s for s in items if s["name"] == "iphone")["params"]["query"] == "iphone 15"
    store.delete_search("rad")
    assert len(store.load_searches()) == 1


def test_corrupt_file_does_not_crash(tmp_data):
    (tmp_data / "favourites.json").write_text("{not json", encoding="utf-8")
    assert store.load_favourites() == {}
