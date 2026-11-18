"""Each rule of Table 3.4, and the engine's quarantine / flag routing."""

from datetime import date

import pandas as pd

from src.validation import rules as R
from src.validation.engine import ValidationContext, validate_report


def frame(**overrides):
    base = dict(disease_code="LASSA", location_raw="Edo", pcode="NG012", epi_year=2024, epi_week=5,
                suspected=20, confirmed=5, deaths=1, cum_suspected=None, cum_confirmed=None, cum_deaths=None)
    return pd.DataFrame([{**base, **overrides}])


def test_rule_01_missing_columns():
    assert R.rule_01_schema(pd.DataFrame({"x": [1]}))[0].rule_id == 1


def test_rule_02_negative_and_fraction():
    assert R.rule_02_non_negative(frame(suspected=-1))[0].rule_id == 2
    assert R.rule_02_non_negative(frame(suspected="12.5"))[0].rule_id == 2
    assert R.rule_02_non_negative(frame()) == []


def test_rule_03_and_04(denominators):
    assert R.rule_03_confirmed_le_suspected(frame(confirmed=30))[0].rule_id == 3
    assert R.rule_04_deaths_le_cases(frame(deaths=6), denominators)[0].rule_id == 4
    # cholera uses suspected cases as the CFR denominator
    assert R.rule_04_deaths_le_cases(frame(disease_code="CHOLERA", confirmed=0, deaths=3), denominators) == []


def test_rule_05_cfr_tolerance():
    assert R.rule_05_cfr_match(frame(cfr_pct=30.77, cfr_reported=30.8), 0.1) == []
    assert R.rule_05_cfr_match(frame(cfr_pct=30.77, cfr_reported=31.0), 0.1)[0].rule_id == 5


def test_rule_06_cumulative_decrease():
    df = pd.concat([frame(epi_week=4, cum_confirmed=10), frame(epi_week=5, cum_confirmed=8)], ignore_index=True)
    assert R.rule_06_cumulative_not_decreasing(df, None)[0].rule_id == 6


def test_rule_07_state_sum():
    df = pd.concat([frame(pcode="NG", location_raw="National", suspected=50, confirmed=9, deaths=2),
                    frame(), frame(pcode="NG029", location_raw="Ondo")], ignore_index=True)
    issues = R.rule_07_states_sum_to_national(df)
    assert {i.detail["field"] for i in issues} == {"suspected", "confirmed"}


def test_rule_08_week_and_printed_dates():
    assert R.rule_08_epi_week(frame(epi_year=2021, epi_week=53), None, None, None, None)[0].rule_id == 8
    bad = R.rule_08_epi_week(frame(), date(2024, 1, 30), date(2024, 2, 5), 2024, 5)
    assert bad and bad[0].rule_id == 8
    assert R.rule_08_epi_week(frame(), date(2024, 1, 29), date(2024, 2, 4), 2024, 5) == []


def test_rule_09_10_11():
    assert R.rule_09_location(frame(pcode=None))[0].rule_id == 9
    assert [i.index for i in R.rule_10_duplicates(pd.concat([frame(), frame()], ignore_index=True))] == [1]
    assert R.rule_11_timeliness(frame(), date(2024, 2, 25), 14)[0].detail["lag_days"] == 21


def test_engine_routes_errors_to_quarantine_and_warnings_to_flags(alias_map, denominators):
    rows = pd.DataFrame([
        dict(disease_code="LASSA", location_raw="National", epi_year=2024, epi_week=5, suspected=60, confirmed=9, deaths=2, cfr_reported=22.2),
        dict(disease_code="LASSA", location_raw="Edo", epi_year=2024, epi_week=5, suspected=20, confirmed=5, deaths=1),
        dict(disease_code="LASSA", location_raw="Ondo", epi_year=2024, epi_week=5, suspected=40, confirmed=4, deaths=1),
        dict(disease_code="LASSA", location_raw="Atlantis", epi_year=2024, epi_week=5, suspected=1, confirmed=0, deaths=0),
        dict(disease_code="LASSA", location_raw="Kano", epi_year=2024, epi_week=5, suspected=2, confirmed=5, deaths=0),
        dict(disease_code="LASSA", location_raw="Edo", epi_year=2024, epi_week=5, suspected=99, confirmed=1, deaths=0),
    ])
    ctx = ValidationContext(alias_map=alias_map, denominators=denominators, published_date=date(2024, 2, 28),
                            report_year=2024, report_week=5)
    result = validate_report(rows, ctx)
    assert sorted(q["rule_id"] for q in result.quarantined) == [3, 9, 10]
    assert sorted(result.passed["pcode"]) == ["NG", "NG012", "NG029"]
    assert {f["flag_type"] for f in result.flags} == {"R11_TIMELINESS"}
    nat = result.passed.set_index("pcode").loc["NG"]
    assert nat["cfr_pct"] == 22.22 and "R11_TIMELINESS" in nat["quality_flags"]


def test_engine_table_error(alias_map, denominators):
    result = validate_report(pd.DataFrame({"foo": [1]}), ValidationContext(alias_map, denominators))
    assert result.table_error is not None and result.passed.empty
