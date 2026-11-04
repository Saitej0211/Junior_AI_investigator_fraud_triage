import pytest

from triage.cross_case import duplicate_claim_numbers
from triage.data import load_cases
from triage.scoring import score_all


@pytest.fixture(scope="module")
def df():
    return load_cases()


@pytest.fixture(scope="module")
def scores(df):
    return score_all(df)


def test_all_cases_scored(df, scores):
    assert len(scores) == len(df) == 50


def test_clean_case_is_low(scores):
    assert scores["C1002"].tier == "Low"
    assert scores["C1002"].triggered == []


def test_extreme_case_is_high(scores):
    assert scores["C1030"].tier == "High"


def test_hard_evidence_never_auto_cleared(scores):
    for cs in scores.values():
        hard = {t.column for t in cs.triggered} & {
            "duplicate_service_billed", "service_overlap_other_provider"}
        if hard:
            assert cs.tier != "Low", cs.case_id


def test_duplicate_claim_number_detected(df):
    dupes = duplicate_claim_numbers(df)
    assert dupes["C1001"] == ["C1031"]
    assert dupes["C1031"] == ["C1001"]
