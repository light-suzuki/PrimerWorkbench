import time
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.application import execute
from app.services import primer_engine, primer_service
from primerblast_oss import workbench_design


def test_all_workbench_design_consumers_use_installed_engine():
    assert primer_service.design_primers is workbench_design.design_primers
    assert primer_service.get_primer3_version is workbench_design.get_primer3_version


def test_agent_and_gui_job_share_engine_without_reclassifying_evidence(monkeypatch):
    calls = []
    evidence = {"mode": "design", "templates": [{"template_id": "synthetic",
        "template_sequence": "ACGT", "pairs": [], "search_completeness": "incomplete"}]}
    def engine(operation, params):
        calls.append((operation, params))
        return evidence
    monkeypatch.setattr(primer_engine, "run_engine", engine)
    params = {"template": "ACGT", "db": ["synthetic-db"], "product_size": "70-100"}
    assert execute("primerblast.design", params) == evidence
    with TestClient(create_app()) as client:
        response = client.post("/primers/screen_job", json=params)
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = client.get("/jobs/" + job_id).json()
            if job["status"] in {"succeeded", "failed", "canceled"}:
                break
            time.sleep(0.01)
        assert job["status"] == "succeeded", job
        assert client.get("/jobs/" + job_id + "/result").json() == evidence
    assert len(calls) == 2 and all(op == "design" for op, _ in calls)
    assert all(p["db"] == ["synthetic-db"] and p["max_target_seqs"] == 5000 for _, p in calls)


def test_embedded_agent_rejects_database_creation_and_unbounded_candidates():
    with pytest.raises(ValueError):
        execute("primerblast.makedb", {})
    with pytest.raises(ValueError):
        execute("primerblast.design", {"template": "ACGT", "db": ["synthetic"], "num_return": 1000})
