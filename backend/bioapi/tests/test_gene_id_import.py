import gzip
import pytest
from fastapi import HTTPException
from app.routers.gene_structure import local_structure_from_gff
from app.services.gff3 import parse_gff3


def test_local_gene_ids_keep_dots_and_normalize_width_case(tmp_path, monkeypatch):
    monkeypatch.setenv("SEQWB_REFERENCE_CACHE_DIR", str(tmp_path / "cache"))
    gff = tmp_path / "annotation.gff3.gz"
    rows = "\n".join([
        "chr1\ttest\tgene\t1\t10\t.\t+\t.\tID=Psat.test.1;Alias=shared",
        "chr1\ttest\texon\t2\t5\t.\t+\t.\tParent=Psat.test.1",
        "chr1\ttest\tgene\t21\t30\t.\t-\t.\tID=Psat.test.2;Alias=shared",
        "chr1\ttest\texon\t22\t25\t.\t-\t.\tParent=Psat.test.2",
    ])
    with gzip.open(gff, "wt") as handle:
        handle.write(rows)
    fasta = tmp_path / "sequence.fa"
    fasta.write_text(">chr1\n" + "A" * 40 + "\n")
    first = local_structure_from_gff(" ＰＳＡＴ．ＴＥＳＴ．１ ", None, gff, fasta)
    assert first["exons"] == [{"start": 2, "end": 5}]
    second = local_structure_from_gff("psat.test.2", None, gff, fasta)
    assert second["sequence"] == "T" * 10
    assert second["exons"] == [{"start": 6, "end": 9}]
    with pytest.raises(ValueError, match="Ambiguous"):
        parse_gff3(str(gff)).gene("shared")
    with pytest.raises(HTTPException) as error:
        local_structure_from_gff("Psat.test", None, gff, fasta)
    assert error.value.status_code == 404
