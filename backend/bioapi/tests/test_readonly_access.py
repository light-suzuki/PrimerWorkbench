import gzip
import hashlib
from pathlib import Path

import pytest

from app.services import annotation_index as indexed
from app.services.gff3 import parse_gff3
from app.services.genome import Genome, _load_index
from app.routers.gene_structure import load_local_sequence
from app.services.blast_service import _fetch_sequence_from_fasta, BlastInputError


def annotation_fixture(path):
    lines = []
    for i in range(2000):
        start = 1 + 100 * i
        lines.extend([
            f"chr1\ttest\tgene\t{start}\t{start+49}\t.\t+\t.\tID=g{i};Alias=alias{i}",
            f"chr1\ttest\tmRNA\t{start}\t{start+49}\t.\t+\t.\tID=t{i};Parent=g{i}",
            f"chr1\ttest\texon\t{start}\t{start+19}\t.\t+\t.\tParent=t{i}",
        ])
    with gzip.open(path, "wt") as fh:
        fh.write("\n".join(lines))


def test_index_reuses_cache_and_reads_only_target_family(tmp_path, monkeypatch):
    source = tmp_path / "annotation.gff3.gz"
    annotation_fixture(source)
    cache = tmp_path / "separate-cache"
    monkeypatch.setenv("SEQWB_REFERENCE_CACHE_DIR", str(cache))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    legacy = parse_gff3(str(source))
    selected = indexed.gene_annotation(source, " ＡＬＩＡＳ１０００ ")
    assert len(selected.features) == 3
    assert selected.gene_region("g1000", "exon") == legacy.gene_region("g1000", "exon")
    monkeypatch.setattr(indexed, "_open_text", lambda *_: pytest.fail("warm query rescanned source"))
    region, exists = indexed.region_annotation(source, "chr1", 100010, 100020)
    assert exists and len(region.features) == 3
    assert region.gene("g1000").id == "g1000"
    empty, exists = indexed.region_annotation(source, "chr1", 300000, 300001)
    assert exists and not empty.features
    _, exists = indexed.region_annotation(source, "missing", 1, 2)
    assert not exists
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert len(list(cache.glob("*.sqlite"))) == 1
    assert set(tmp_path.iterdir()) == {source, cache}


def test_cache_invalidates_and_alias_ambiguity_is_preserved(tmp_path, monkeypatch):
    source = tmp_path / "annotation.gff3"
    monkeypatch.setenv("SEQWB_REFERENCE_CACHE_DIR", str(tmp_path / "cache"))
    source.write_text("chr1\tt\tgene\t1\t9\t.\t+\t.\tID=g1;Alias=shared\n")
    assert indexed.gene_annotation(source, "g1").gene("g1").end == 9
    source.write_text("chr1\tt\tgene\t1\t19\t.\t+\t.\tID=g1;Alias=shared\n"
                      "chr2\tt\tgene\t30\t40\t.\t-\t.\tID=g2;Alias=shared\n")
    assert indexed.gene_annotation(source, "g1").gene("g1").end == 19
    with pytest.raises(ValueError, match="Ambiguous"):
        indexed.gene_annotation(source, "shared")
    assert indexed.gene_annotation(source, "shared", seqid="chr2").gene("g2").strand == "-"


def test_existing_fai_is_reused_and_fetch_does_not_cross_records(tmp_path):
    fasta = tmp_path / "reference.fa"
    fasta.write_bytes(b">chr1\r\nACGT\r\nAC\r\n>chr2\r\nTTTT\r\n")
    fai = Path(str(fasta) + ".fai")
    fai.write_text("chr1\t6\t7\t4\t6\nchr2\t4\t24\t4\t6\n")
    before = (fasta.read_bytes(), fai.read_bytes())
    _load_index.cache_clear()
    assert Genome(str(fasta)).fetch("chr1", 2, 6) == "CGTAC"
    assert Genome(str(fasta)).fetch("chr1", 2, 6, "-") == "GTACG"
    assert _load_index.cache_info().misses == 1
    assert _load_index.cache_info().hits == 1
    assert Genome(str(fasta)).fetch("chr2", 1, 4) == "TTTT"
    assert (fasta.read_bytes(), fai.read_bytes()) == before


def test_gene_sequence_streams_unindexed_or_gzip_ranges(tmp_path):
    plain = tmp_path / "reference.fa"
    text = ">other\nAAAA\n>target description\nACG\nTACGT\nAC\n>later\nTTTT\n"
    plain.write_text(text)
    compressed = tmp_path / "reference.fa.gz"
    with gzip.open(compressed, "wt") as fh:
        fh.write(text)
    for source in (plain, compressed):
        assert load_local_sequence(source, "target", 3, 8) == "GTACGT"
        assert not Path(str(source) + ".fai").exists()


def test_blast_fallback_uses_existing_index_and_rejects_out_of_range(tmp_path):
    fasta = tmp_path / "reference.fa"
    fasta.write_bytes(b">chr1\nACGT\nAC\n")
    Path(str(fasta) + ".fai").write_text("chr1\t6\t6\t4\t5\n")
    assert _fetch_sequence_from_fasta(fasta_path=fasta, entry="chr1", start=2, end=6) == "CGTAC"
    with pytest.raises(BlastInputError, match="end exceeds"):
        _fetch_sequence_from_fasta(fasta_path=fasta, entry="chr1", start=2, end=7)


def test_unindexed_blast_layout_cache_detects_replaced_source(tmp_path):
    fasta = tmp_path / "reference.fa"
    fasta.write_bytes(b">chr1\nACGT\n")
    assert _fetch_sequence_from_fasta(fasta_path=fasta, entry="chr1", start=1, end=3) == "ACG"
    fasta.write_bytes(b">chr1 changed description\nTTTT\n")
    assert _fetch_sequence_from_fasta(fasta_path=fasta, entry="chr1", start=1, end=3) == "TTT"

