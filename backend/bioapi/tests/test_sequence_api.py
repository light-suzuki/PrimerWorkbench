from fastapi.testclient import TestClient

from app.main import create_app


client = TestClient(create_app())


def test_sequence_analysis_contract():
    sequence = "ATG" + "GCT" * 50 + "TAA" + "GAATTC"
    basic = client.post("/sequence/analyze/basic", json={"sequence": sequence, "include_translation": False})
    assert basic.status_code == 200
    assert basic.json()["length"] == len(sequence)

    orfs = client.post("/sequence/analyze/orfs", json={"sequence": sequence, "min_aa_length": 50})
    assert orfs.status_code == 200
    assert orfs.json()["orfs"][0]["length_aa"] == 52

    cuts = client.post("/sequence/analyze/restriction", json={"sequence": sequence, "enzymes": ["EcoRI"]})
    assert cuts.status_code == 200
    assert cuts.json()["results"][0]["cut_positions"] == [158]


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
