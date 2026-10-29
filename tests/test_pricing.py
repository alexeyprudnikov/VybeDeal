from dealfinder.pricing import fmt_eur, parse_price, score_badge


def test_plain_price():
    assert parse_price("279 €") == (279.0, "fixed")


def test_negotiable():
    amount, kind = parse_price("210 € VB")
    assert amount == 210.0
    assert kind == "negotiable"


def test_thousands_and_decimals():
    assert parse_price("1.299,50 €")[0] == 1299.50
    assert parse_price("1.299 €")[0] == 1299.0


def test_from_price():
    assert parse_price("ab 100 €") == (100.0, "from")


def test_no_number_labels():
    assert parse_price("Verhandlung") == (None, "on_request")
    assert parse_price("Zu verschenken") == (0.0, "free")
    assert parse_price("") == (None, "unknown")
    assert parse_price(None) == (None, "unknown")


def test_zero_is_free():
    assert parse_price("0 €") == (0.0, "free")


def test_fmt_eur():
    assert fmt_eur(312.0) == "312 €"
    assert fmt_eur(1299.0) == "1.299 €"
    assert fmt_eur(None) == "–"


def test_badge():
    assert score_badge(9) == "🔥"
    assert score_badge(2) == "🚫"
    assert score_badge(None) == "⚪"
