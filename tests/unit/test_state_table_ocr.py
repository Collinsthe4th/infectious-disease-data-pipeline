from src.extraction.state_table_ocr import StateTable, match_state, to_rows

ALIASES = {"ondo": "NG029", "edo": "NG012", "kogi": "NG023", "fct": "NG015", "kwara": "NG024",
           "akwa ibom": "NG003", "bauchi": "NG005", "ekiti": "NG013", "ogun": "NG028", "jigawa": "NG018"}


def test_match_state_tolerates_common_ocr_slips():
    assert match_state("Ondo", ALIASES) == "ondo"
    assert match_state("Bauch!", ALIASES) == "bauchi"
    assert match_state("Kogl", ALIASES) == "kogi"
    assert match_state("Fet", ALIASES) == "fct"
    assert match_state("a|Edo", ALIASES) == "edo"  # stray letters from the row number
    assert match_state("Kw ara", ALIASES) == "kwara"
    assert match_state("Akwa lbom", ALIASES) == "akwa ibom"


def test_match_state_refuses_unknown_names():
    assert match_state("Jos", ALIASES) is None  # a city NCDC sometimes lists; not a state
    assert match_state("", ALIASES) is None


def test_to_rows_maps_columns_and_skips_unmatched():
    table = StateTable(
        rows={"Ondo": {"wk_suspected": 24, "wk_confirmed": 4, "wk_deaths": 1, "cum_suspected": 2939,
                       "cum_confirmed": 396, "cum_deaths": 53, "wk_probable": 0},
              "Jos": {"cum_suspected": 1}},
        total={}, names={"Ondo": "ondo", "Jos": None})
    rows = to_rows(table)
    assert rows == [{"location_raw": "ondo", "suspected": "24", "confirmed": "4", "deaths": "1",
                     "cum_suspected": "2939", "cum_confirmed": "396", "cum_deaths": "53"}]


def test_table_with_problems_is_not_ok():
    assert StateTable({}, {}, {}, problems=["cum_confirmed: states add up to 1 but the Total row says 2"]).ok is False
    assert StateTable({}, {}, {}).ok is True
