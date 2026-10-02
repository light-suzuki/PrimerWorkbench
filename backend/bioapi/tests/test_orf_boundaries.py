"""ORF lengths count translated residues, not the terminal stop codon."""
from itertools import product

import pytest
from Bio.Seq import Seq
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.sequence_service import find_orfs

@pytest.mark.parametrize("prefix", ["", "C", "CC"])
@pytest.mark.parametrize("stop", ["TAA", "TAG", "TGA"])
def test_orf_minimum_excludes_terminal_stop(prefix, stop):
    below = prefix + "ATG" + "GCT" * 28 + stop
    exact = prefix + "ATG" + "GCT" * 29 + stop
    assert find_orfs(below, min_aa_length=30) == []
    result = find_orfs(exact, min_aa_length=30)
    assert len(result) == 1
    orf = result[0]
    assert orf["length_aa"] == len(orf["protein_sequence"]) == 30
    assert orf["length_nt"] == 93
    assert orf["start"] == len(prefix) + 1
    assert orf["end"] == len(prefix) + 93

def test_one_residue_orf_keeps_inclusive_stop_coordinates():
    orf = find_orfs("ATGTAA", min_aa_length=1)[0]
    assert orf["length_aa"] == len(orf["protein_sequence"]) == 1
    assert (orf["start"], orf["end"], orf["length_nt"]) == (1, 6, 6)


@pytest.mark.parametrize("stop,expansions", [("TAR", {"TAA", "TAG"}), ("TRA", {"TAA", "TGA"})])
@pytest.mark.parametrize("prefix", ["", "C", "CC"])
def test_certain_ambiguous_stop_cannot_inflate_orf(stop, expansions, prefix):
    # Independent concrete expansion, plus the translation library's behavior.
    bases = {"T": "T", "A": "A", "R": "AG"}
    assert {"".join(c) for c in product(*(bases[b] for b in stop))} == expansions
    assert expansions <= {"TAA", "TAG", "TGA"}
    assert str(Seq(stop).translate()) == "*"

    sequence = prefix + "ATG" + stop + "GCT" * 29 + "TAA"
    assert find_orfs(sequence, min_aa_length=30) == []
    assert find_orfs(sequence, min_aa_length=1) == [{
        "frame": len(prefix), "start": len(prefix) + 1, "end": len(prefix) + 6,
        "length_nt": 6, "length_aa": 1, "protein_sequence": "M",
    }]


@pytest.mark.parametrize("stop", ["TAR", "TRA"])
def test_certain_ambiguous_stop_at_minimum_boundary(stop):
    assert find_orfs("ATG" + "GCT" * 28 + stop, min_aa_length=30) == []
    orf = find_orfs("ATG" + "GCT" * 29 + stop, min_aa_length=30)[0]
    assert (orf["length_aa"], orf["length_nt"], orf["end"]) == (30, 93, 93)
    assert orf["protein_sequence"] == "M" + "A" * 29


@pytest.mark.parametrize("codon,amino_acid", [("GCN", "A"), ("ATH", "I"), ("TGR", "X"), ("NNN", "X")])
def test_other_ambiguous_codons_remain_candidate_residues(codon, amino_acid):
    orf = find_orfs("ATG" + codon + "GCT" * 28 + "TAA", min_aa_length=30)[0]
    assert orf["length_aa"] == len(orf["protein_sequence"]) == 30
    assert orf["protein_sequence"] == "M" + amino_acid + "A" * 28


def test_nested_starts_retain_individual_orfs_and_terminal_partial_bases():
    sequence = "ATGATG" + "GCT" * 29 + "TAA"
    expected = find_orfs(sequence, min_aa_length=30)
    assert [(r["start"], r["end"], r["length_aa"]) for r in expected] == [(1, 96, 31), (4, 96, 30)]
    for suffix in ["A", "AT"]:
        assert find_orfs(sequence + suffix, min_aa_length=30) == expected
    assert find_orfs("ATG" + "GCT" * 29 + "TA", min_aa_length=30) == []


def test_http_certain_ambiguous_stop_rejects_false_minimum():
    client = TestClient(create_app())
    for stop in ["TAR", "TRA"]:
        response = client.post("/sequence/analyze/orfs", json={
            "sequence": "ATG" + stop + "GCT" * 29 + "TAA", "min_aa_length": 30,
        })
        assert response.status_code == 200
        assert response.json() == {"orfs": []}
