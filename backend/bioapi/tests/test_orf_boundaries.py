"""ORF lengths count translated residues, not the terminal stop codon."""
import pytest
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
