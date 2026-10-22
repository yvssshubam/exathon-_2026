"""AMLworld loader pieces that need no database or download (FR-01, DECISIONS D-37)."""
import yaml

from pipeline import load_amlworld as aml
from pipeline.run_sql import split_statements

PATTERNS = """BEGIN LAUNDERING ATTEMPT - FAN-OUT:  Max 2-degree Fan-Out
2022/09/01 01:50,0241079,80EF0F190,0123288,80F5B71F0,13074.86,US Dollar,13074.86,US Dollar,ACH,1
2022/09/04 17:47,0241079,80EF0F190,0217496,806F3FE20,1052179.48,Ruble,1052179.48,Ruble,ACH,1
END LAUNDERING ATTEMPT - FAN-OUT

BEGIN LAUNDERING ATTEMPT - CYCLE:  Max 3 hops
2022/09/02 10:00,001,800AAA,002,800BBB,10.00,Euro,10.00,Euro,Wire,1
END LAUNDERING ATTEMPT - CYCLE
"""


def test_patterns_parse_into_attempts_with_type_and_detail():
    rows = aml.parse_patterns(PATTERNS.splitlines())
    assert [(r[0], r[1], r[2]) for r in rows] == ([(1, "FAN-OUT", "Max 2-degree Fan-Out")] * 2
                                                   + [(2, "CYCLE", "Max 3 hops")])
    assert rows[0][3:] == ("2022/09/01 01:50", "0241079", "80EF0F190", "0123288", "80F5B71F0", "13074.86", "ACH")


def test_patterns_reject_a_changed_file():
    import pytest

    with pytest.raises(aml.ShapeError):
        aml.parse_patterns(["2022/09/01 01:50,001,800AAA,002,800BBB,1,US Dollar,1,US Dollar,ACH,1"])
    with pytest.raises(aml.ShapeError):
        aml.parse_patterns(["BEGIN LAUNDERING ATTEMPT - STACK:", "2022/09/01 01:50,001,800AAA,002"])


def test_count_rows_and_header(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text(",".join(aml.TRANS_HEADER) + "\n" + "a\nb\nc", encoding="utf-8")     # no newline at the end
    assert aml.count_rows(f) == 3 and aml.header(f) == aml.TRANS_HEADER
    f.write_bytes((",".join(aml.ACCOUNTS_HEADER) + "\r\n" + "x\r\ny\r\n").encode())    # Windows line endings
    assert aml.count_rows(f) == 2 and aml.header(f) == aml.ACCOUNTS_HEADER


def test_missing_files_are_reported_not_crashed():
    cfg = {"files": {k: aml.ROOT / "data" / "raw" / f"missing-{k}.csv" for k in ("transactions", "accounts",
                                                                                   "patterns")}}
    problems = aml.check_files(cfg)
    assert len(problems) == 2 and all("download it from Kaggle" in p for p in problems)


def test_config_maps_every_amlworld_payment_format():
    cfg = yaml.safe_load(aml.CONFIG.read_text(encoding="utf-8"))
    assert set(cfg["channels"]) == {"ACH", "Wire", "Cheque", "Credit Card", "Cash", "Bitcoin", "Reinvestment"}
    assert cfg["channels"]["Reinvestment"] is None
    assert all(len(v) <= 10 for v in cfg["channels"].values() if v)       # PH.TXN.channel is VARCHAR(10)
    assert str(cfg["last_day"]) < "2026-10-18"                            # ends before the case day (D-37)
    assert cfg["usd_to_inr"] > 0
    assert cfg["fallback_usd_per_unit"]["US Dollar"] == 1


def test_sql_files_split_and_take_only_known_parameters():
    import re

    for name, allowed in (("010_stage.sql", set()), ("020_rates.sql", set()),
                          ("030_map.sql", {"last_day", "usd_to_inr"})):
        text = (aml.SQL / name).read_text(encoding="utf-8")
        assert split_statements(text, aml.ROOT)
        assert set(re.findall(r"\{(\w+)(?:![a-z])?\}", text)) == allowed, name


def test_amlworld_ids_cannot_collide_with_case_ids():
    from pipeline.cases import DEMO_CASE, EXTRA_CASES, LOOP_CASE, load_case

    for path in (DEMO_CASE, EXTRA_CASES, LOOP_CASE):
        case = load_case(path)
        assert not any(a["account_id"].startswith("AML") for a in case.accounts)
        assert not any(b["bank_id"].startswith("AML") for b in case.banks)
        assert not any(t.txn_id.startswith("AML") or t.utr.startswith("AMLW") for t in case.txns)