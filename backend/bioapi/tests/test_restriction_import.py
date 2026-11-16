from fastapi.testclient import TestClient
from app.main import create_app
from app.services.restriction_detail import enzyme_detail, resolve_enzyme
from app.services.caps_service import _enzyme_classes

client = TestClient(create_app())


def test_catalog_and_product_aliases_are_local_and_normalized():
    data = client.get("/sequence/enzymes").json()
    assert len(data["enzymes"]) == 1088
    assert data["rebase_version"] == "404 (2024)"
    assert resolve_enzyme(" ＥｃｏＲＩ－ＨＦ ").__name__ == "EcoRI"
    assert resolve_enzyme("bsai-hfv2").__name__ == "BsaI"


def test_unknown_enzyme_is_not_reported_as_a_negative_digest():
    response = client.post("/sequence/analyze/restriction", json={"sequence":"GGGAATTCCC", "enzymes":["NotAnEnzyme"]})
    assert response.status_code == 400


def test_api_keeps_biopython_coordinates_and_adds_both_strand_windows():
    response = client.post("/sequence/analyze/restriction", json={"sequence":"GGGAATTCCC", "enzymes":["ＥｃｏＲＩ－ＨＦ"]})
    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["enzyme"] == "EcoRI"
    assert row["cut_positions"] == [4]  # first base after boundary 3
    window = row["enzyme_detail"]["windows"][0]
    assert (window["top_boundary"], window["bottom_boundary"]) == (3,7)
    assert window["pattern"]["top"].replace(" ","") == "5′GGG|AATTCCC3′"


def test_type_iis_reverse_cuts_and_incomplete_sites():
    row = enzyme_detail("BsaI", "A"*20 + "GAGACC" + "A"*10)
    event = row["windows"][0]
    assert event["strand"] == "-"
    assert (event["top_boundary"],event["bottom_boundary"]) == (15,19)
    assert enzyme_detail("BsaI", "GGTCTC")["windows"] == []


def test_same_cut_and_different_cut_relationships_are_distinct():
    info = enzyme_detail("KpnI")
    assert "Acc65I" in info["different_cut_enzymes"]
    assert "Acc65I" not in info["same_cut_enzymes"]
    classes, rejected = _enzyme_classes(["EcoRI-HF", "EcoRI", "DpnI", "MspJI", "AjuI"])
    assert [enzyme.__name__ for enzyme in classes] == ["EcoRI"]
    assert len(rejected) == 3
