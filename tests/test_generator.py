"""NFR-04: the generated extra cases are deterministic and the committed file is not stale."""
import yaml

from pipeline.cases import EXTRA_CASES, load_case
from pipeline.gen_extra_cases import generate, render


def test_same_seed_same_output():
    assert render(generate(42)) == render(generate(42))


def test_committed_file_is_current():
    # Compare the data, not the text: line endings and a trailing newline can change when files are copied.
    committed = yaml.safe_load(EXTRA_CASES.read_text(encoding="utf-8"))
    assert committed == generate(42), "run: python -m pipeline.gen_extra_cases"


def test_generated_case_is_well_formed():
    case = load_case(EXTRA_CASES)
    assert len(case.complaints) == 20
    for c in case.complaints:
        assert len(c["ack_no"]) == 14 and c["ack_no"][0] == ("3" if c["source"] == "1930" else "2")
        root = [t for t in case.txns if t.utr == c["utr"]]
        assert len(root) == 1 and root[0].victim_tag == c["victim_tag"]
    assert {c["lane"] for c in case.complaints} == {"A", "B"}