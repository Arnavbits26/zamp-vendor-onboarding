import json
from pathlib import Path

from app.validators import (
    FAIL, PASS, gstin_check_char, run_format_checks,
    validate_gstin, validate_ifsc, validate_pan,
)

ROOT = Path(__file__).resolve().parents[1]


def statuses(results):
    return {r.rule_id: r.status for r in results}


# --- GSTIN checksum against publicly documented example GSTINs ------------
def test_checksum_known_examples():
    assert gstin_check_char("27AAPFU0939F1Z") == "V"
    assert gstin_check_char("29AAGCB7383J1Z") == "4"


def test_gstin_valid():
    assert statuses(validate_gstin("27AAPFU0939F1ZV")) == {
        "FMT-GSTIN-01": PASS, "FMT-GSTIN-02": PASS, "FMT-GSTIN-03": PASS}


def test_gstin_single_typo_caught_by_checksum():
    # one digit of the PAN part mistyped (0939 -> 0989)
    s = statuses(validate_gstin("27AAPFU0989F1ZV"))
    assert s["FMT-GSTIN-01"] == PASS and s["FMT-GSTIN-03"] == FAIL


def test_gstin_bad_state_code():
    g = "99AAPFU0939F1Z"
    s = statuses(validate_gstin(g + gstin_check_char(g)))
    assert s["FMT-GSTIN-02"] == FAIL


def test_gstin_wrong_length_and_missing():
    assert statuses(validate_gstin("27AAPFU0939F1Z"))["FMT-GSTIN-01"] == FAIL
    assert statuses(validate_gstin(None))["FMT-GSTIN-01"] == FAIL


def test_gstin_tolerates_spaces_and_lowercase():
    assert statuses(validate_gstin(" 27aapfu0939f1zv "))["FMT-GSTIN-03"] == PASS


# --- PAN --------------------------------------------------------------------
def test_pan_valid_and_holder_type():
    r = validate_pan("BQRPS7712D")[0]
    assert r.status == PASS and r.evidence["holder_type_code"] == "P"


def test_pan_invalid():
    assert validate_pan("BQRXS7712D")[0].status == FAIL   # X is not a holder type
    assert validate_pan("BQRPS771D")[0].status == FAIL    # too short


# --- IFSC -------------------------------------------------------------------
def test_ifsc():
    assert validate_ifsc("HDFC0004521")[0].status == PASS
    assert validate_ifsc("HDFC1004521")[0].status == FAIL  # 5th char must be 0
    assert validate_ifsc("BARB0BANJAR")[0].status == PASS  # alphanumeric branch code


# --- Every generated test case passes format checks ------------------------
# (Edge cases are designed to fail later stages, not basic formatting.)
def test_all_generated_cases_are_well_formed():
    for sub_file in sorted((ROOT / "test_cases").glob("*/submission.json")):
        sub = json.loads(sub_file.read_text())
        fails = [r.rule_id for r in run_format_checks(sub) if r.status == FAIL]
        assert not fails, f"{sub_file.parent.name}: {fails}"
