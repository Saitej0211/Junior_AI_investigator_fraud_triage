import pytest
from fastapi.testclient import TestClient

import app.main
import scripts.run_triage


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    # Write to a temp file so tests never overwrite the real outputs/triage.json.
    triage_path = tmp_path_factory.mktemp("outputs") / "triage.json"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scripts.run_triage, "OUT_PATH", triage_path)
        mp.setattr(app.main, "TRIAGE_PATH", triage_path)
        scripts.run_triage.main(use_llm=False)
        yield TestClient(app.main.app)


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
