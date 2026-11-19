import pytest

from src.extraction.standardise import canonical_header, clean_count, clean_percent, header_period, resolve_location


@pytest.mark.parametrize("raw,expected", [
    ("1,234", 1234), ("12*", 12), (" 7 ", 7), ("0", 0), ("12 (3%)", 12), (5, 5), (5.0, 5), ("1 234", 1234),
])
def test_clean_count_numbers(raw, expected):
    assert clean_count(raw) == expected


@pytest.mark.parametrize("raw", ["", "-", "–", "N/A", None, float("nan")])
def test_missing_is_none_not_zero(raw):
    assert clean_count(raw) is None


def test_dash_can_mean_zero_when_template_says_so():
    assert clean_count("-", dash_is_zero=True) == 0


@pytest.mark.parametrize("raw", ["12.5", "abc", "1.2.3"])
def test_non_integers_raise(raw):
    with pytest.raises(ValueError):
        clean_count(raw)


def test_clean_percent():
    assert clean_percent("30.8%") == 30.8
    assert clean_percent("-") is None


@pytest.mark.parametrize("header,name", [
    ("Suspected cases", "suspected"), ("Confirmed cases", "confirmed"), ("Deaths (Confirmed cases)", "deaths"),
    ("Case Fatality Rate (CFR)", "cfr"), ("States", "location"), ("Probable cases", "probable"),
    ("States and LGAs affected (Confirmed cases)", "affected"), ("Week 44 Suspected", "suspected"),
])
def test_canonical_header(header, name):
    assert canonical_header(header) == name


def test_header_period():
    assert header_period("Cumulative Confirmed") == "cumulative"
    assert header_period("Week 44 Confirmed") == "weekly"
    assert header_period("Confirmed") == "unknown"


@pytest.mark.parametrize("name,pcode", [
    ("FCT", "NG015"), ("Abuja", "NG015"), ("Federal Capital Territory", "NG015"), ("Akwa-Ibom", "NG003"),
    ("Nassarawa", "NG026"), ("Edo State", "NG012"), ("  ONDO ", "NG029"), ("Total", "NG"), ("Cross River*", "NG009"),
])
def test_resolve_location(name, pcode, alias_map):
    assert resolve_location(name, alias_map) == pcode


def test_unknown_location_is_none(alias_map):
    assert resolve_location("Atlantis", alias_map) is None


def test_clean_count_refuses_two_numbers_run_together():
    """Real case: Lassa 2022 week 29, where '5890 857' (suspected, confirmed) shared one cell."""
    import pytest

    from src.extraction.standardise import clean_count

    assert clean_count("1,234") == 1234
    assert clean_count("12 345") == 12345
    assert clean_count("5,890 (12%)") == 5890
    for bad in ("5890 857", "134 10", "12,34"):
        with pytest.raises(ValueError):
            clean_count(bad)
