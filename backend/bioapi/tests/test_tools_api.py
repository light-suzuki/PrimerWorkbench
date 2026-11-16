from fastapi.testclient import TestClient

from app.main import create_app
from app.services import caps_service


client = TestClient(create_app())


def test_tools_versions_contract():
    response = client.get("/tools/versions")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"app_version", "primer3_version", "blast_version"}
    for value in data.values():
        assert isinstance(value, str)
        assert value.strip()


def test_caps_metadata_contract():
    metadata = caps_service._build_caps_metadata(
        ref_db="/home/user/blastdb/ref_genome",
        alt_db="/home/user/blastdb/alt_genome",
        screen_dbs=["/home/user/blastdb/ref_genome"],
        product_min=200,
        product_max=800,
        primer_num_return=200,
        max_markers=200,
        enzyme_names=["EcoRI", "HindIII"],
        enzymes_per_primer=2,
        max_cuts_per_allele=3,
        min_fragment_len=30,
        require_perfect_primers_in_alt=True,
        blast_num_threads=None,
        blast_max_target_seqs=25,
        opt_tm=60.0,
        min_tm=57.0,
        max_tm=63.0,
        primer_min_size=None,
        primer_opt_size=None,
        primer_max_size=None,
        primer_min_gc=None,
        primer_max_gc=None,
        primer_salt_monovalent=None,
        primer_dna_conc=None,
    )
    assert metadata["schema_version"] == "caps-report/1"
    assert isinstance(metadata["app_version"], str) and metadata["app_version"].strip()
    for tool in (metadata["primer3"], metadata["blast"]):
        assert tool["identity"]
        assert isinstance(tool["version"], str) and tool["version"].strip()
    roles = [db["role"] for db in metadata["dbs"]]
    assert roles == ["ref", "alt", "screen"]
    for db in metadata["dbs"]:
        assert db["label"]
        assert db["db_type"] in {"nucl", "prot", "unknown"}
    assert metadata["conditions"]["product_min"] == 200
    assert metadata["conditions"]["primer3_min_size"] == 18
    assert metadata["conditions"]["primer3_opt_tm"] == 60.0
    assert metadata["specificity"]["screen_task"] == "blastn-short"
    assert metadata["specificity"]["blast_num_threads"] is None
