import pytest
from fastapi.testclient import TestClient

from app.main import app
from scripts.run_triage import main as run_triage


@pytest.fixture(scope="module")
def client():
    run_triage()
    return TestClient(app)


def test_queue_sorted_by_risk(client):
    cases = client.get("/api/cases").json()
    assert len(cases) == 50
    scores = [c["score"] for c in cases]
    assert scores == sorted(scores, reverse=True)


def test_case_detail_includes_claim_data(client):
    case = client.get("/api/cases/C1031").json()
    assert case["claim"]["claim_number"] == "LTC-2034786"
    assert case["shares_claim_number_with"] == ["C1001"]


def test_unknown_case_is_404(client):
    assert client.get("/api/cases/NOPE").status_code == 404
