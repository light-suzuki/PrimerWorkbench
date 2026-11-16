import json
import subprocess
import sys
import io
import pytest
from app import cli as agent_cli

from fastapi.testclient import TestClient
from app.application import execute, capabilities, caps_payload
from app.main import create_app
from app.services import primer_service, blast_service, caps_service
from app.models.schemas import CapsDesignRequest


def cli(request):
    return subprocess.run([sys.executable, "-m", "app.cli", "run"], input=json.dumps(request),
                          capture_output=True, encoding="utf-8")


def test_cli_http_and_application_return_identical_restriction_evidence():
    params = {"sequence": "GGGAATTCCC", "enzymes": ["ＥｃｏＲＩ－ＨＦ"]}
    response = TestClient(create_app()).post("/sequence/analyze/restriction", json=params)
    result = cli({"operation": "sequence.restriction", "params": params, "request_id": "r1"})
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["request_id"] == "r1"
    assert output["result"] == response.json() == execute("sequence.restriction", params)


def test_schema_and_unknown_inputs_do_not_start_servers_or_execute_tools():
    schema = capabilities()
    assert not schema["starts_gui"] and not schema["uploads_data"]
    assert all(not operation["writes_database"] for operation in schema["operations"])
    result = cli({"operation": "sequence.basic", "params": {"sequence": "ACGT", "typo": True}})
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "invalid_request"
    result = cli({"operation": "blast.local", "params": {"sequence": "ACGT", "db": "synthetic", "backend": "ncbi"}})
    assert result.returncode == 2


def test_primer_adapter_preserves_target_parameter_mapping(monkeypatch):
    seen = []
    def design(**params):
        seen.append(params)
        return []
    monkeypatch.setattr(primer_service, "design_primers", design)
    params = {"sequence": "ACGT" * 100, "target_start": 20, "target_length": 30}
    core = execute("primers.design", params)
    response = TestClient(create_app()).post("/primers/design", json=params)
    assert response.status_code == 200 and response.json() == core
    assert all(row["target_start_1based"] == 20 and row["target_length"] == 30 for row in seen)


def test_local_blast_metadata_matches_http(monkeypatch):
    monkeypatch.setattr(blast_service, "run_blastn_sync", lambda *args: [])
    params = {"sequence": "ACGT", "db": "synthetic"}
    core = execute("blast.local", params)
    response = TestClient(create_app()).post("/blast/run", json=params)
    assert response.status_code == 200 and response.json() == core


def test_caps_shared_boundary_preserves_report_and_job_callbacks(monkeypatch):
    seen = []
    payload = {"ref_db": "synthetic-a", "ref_entry": "chr1", "ref_start": 1, "ref_end": 500,
               "ref_length": 500, "alt_db": "synthetic-b", "alt_entry": "chr1", "alt_start": 1,
               "alt_end": 500, "alt_length": 500, "alt_strand": "plus", "mapped_by_blast": False,
               "primer_pairs_generated": 0, "markers": [], "warnings": ["Wet unverified"],
               "metadata": {"schema_version": "caps-report/1", "app_version": "synthetic",
                            "primer3": {"identity": "primer3_core", "version": "synthetic"},
                            "blast": {"identity": "blastn", "version": "synthetic"}}}
    def design(**params):
        seen.append(params)
        return payload
    monkeypatch.setattr(caps_service, "design_caps_markers", design)
    params = {"ref_db": "synthetic-a", "ref_entry": "chr1", "ref_start": 1, "ref_end": 500,
              "alt_db": "synthetic-b"}
    core = execute("caps.design", params)
    response = TestClient(create_app()).post("/caps/design", json=params)
    assert response.status_code == 200 and response.json() == core
    progress, cancel = lambda *_: None, lambda: True
    caps_payload(CapsDesignRequest(**params), progress_cb=progress, cancel_cb=cancel)
    assert seen[-1]["progress_cb"] is progress and seen[-1]["cancel_cb"] is cancel


def test_cli_module_does_not_import_http_application():
    result = subprocess.run([sys.executable, "-c", "import app.cli, sys; assert 'app.main' not in sys.modules; assert 'app.routers.blast' not in sys.modules"],
                            capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("result", [float("nan"), {"not_json": {1, 2}}])
def test_unencodable_engine_result_returns_json_error(monkeypatch, capsys, result):
    monkeypatch.setattr(agent_cli, "execute", lambda *_: result)
    monkeypatch.setattr(sys, "stdin", io.StringIO('\ufeff{"operation":"sequence.basic","params":{},"request_id":"r1"}'))
    assert agent_cli.main(["run"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["code"] == "execution_failed" and output["request_id"] == "r1"


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_input_is_rejected_before_execution(monkeypatch, capsys, number):
    monkeypatch.setattr(agent_cli, "execute", lambda *_: pytest.fail("must not execute"))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"operation":"sequence.basic","params":{"value":' + number + '}}'))
    assert agent_cli.main(["run"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "invalid_request"
