"""Local reference application services, independent of HTTP and GUI."""
import gzip
from pathlib import Path
from typing import Any
from Bio.Seq import Seq
from .annotation_index import gene_annotation
from .genome import Genome


class ReferenceError(ValueError):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def load_local_sequence(fasta_path: Path, seqid: str, start: int = 1, end: int | None = None) -> str:
    """
    Read only the requested range; use an existing .fai without creating one.
    """
    if not fasta_path.exists():
        raise ReferenceError(status_code=500, detail=f"FASTA が見つかりません: {fasta_path}")

    if fasta_path.suffix != ".gz" and Path(str(fasta_path) + ".fai").exists():
        genome = Genome(str(fasta_path))
        if seqid not in genome:
            raise ReferenceError(status_code=404, detail="Sequence ID not found in local FASTA index.")
        stop = end if end is not None else genome.length(seqid)
        if start < 1 or stop < start or stop > genome.length(seqid):
            raise ReferenceError(status_code=400, detail="Gene range exceeds the local FASTA sequence.")
        return genome.fetch(seqid, start, stop)
    opener = gzip.open if fasta_path.suffix.endswith("gz") else open
    found, position, chunks = False, 0, []
    with opener(fasta_path, "rt") as fh:
        for line in fh:
            if line.startswith(">"):
                if found:
                    break
                found = line[1:].split()[0] == seqid
                continue
            if not found:
                continue
            bases = "".join(line.split())
            next_position = position + len(bases)
            left = max(0, start - 1 - position)
            right = min(len(bases), end - position if end is not None else len(bases))
            if left < right:
                chunks.append(bases[left:right])
            position = next_position
            if end is not None and position >= end:
                return "".join(chunks)
    if found:
        if end is not None and position < end:
            raise ReferenceError(status_code=400, detail="Gene range exceeds the local FASTA sequence.")
        return "".join(chunks)

    raise ReferenceError(status_code=404, detail=f"FASTA に {seqid} が見つかりませんでした。")


def parse_attrs(attr_str: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for field in attr_str.split(";"):
        if not field:
            continue
        if "=" in field:
            k, v = field.split("=", 1)
            attrs[k.strip()] = v.strip()
    return attrs


def local_structure_from_gff(
    gene_id: str,
    species: str | None,
    gff_path: Path,
    fasta_path: Path,
) -> dict[str, Any]:
    if not gff_path.exists():
        raise ReferenceError(status_code=500, detail=f"GFF3 が見つかりません: {gff_path}")

    try:
        gene = gene_annotation(gff_path, gene_id).gene(gene_id)
    except ValueError as exc:
        raise ReferenceError(status_code=400, detail=str(exc)) from exc
    if gene is None:
        raise ReferenceError(status_code=404, detail=f"Gene ID not found in local annotation: {gene_id}")
    exons: list[dict[str, int]] = []
    cds_list: list[dict[str, int]] = []
    pending = list(gene.children)
    visited: set[int] = set()
    while pending:
        feature = pending.pop()
        if id(feature) in visited:
            continue
        visited.add(id(feature))
        pending.extend(feature.children)
        if feature.type == "exon":
            exons.append({"start": feature.start, "end": feature.end})
        if feature.type == "CDS":
            cds_list.append({"start": feature.start, "end": feature.end})
    seqid = gene.seqid
    strand = -1 if gene.strand == "-" else 1
    gene_start, gene_end = gene.start, gene.end

    if not exons and not cds_list:
        raise ReferenceError(status_code=404, detail=f"GFF3 に {gene_id} を含むエントリが見つかりませんでした。")
    if seqid is None or gene_start is None or gene_end is None:
        raise ReferenceError(status_code=404, detail="GFF3 から座標を解釈できませんでした。")

    # 配列取得
    region_seq = load_local_sequence(fasta_path, seqid, gene_start, gene_end)
    if strand == -1:
        region_seq = str(Seq(region_seq).reverse_complement())

    length = len(region_seq)

    def norm(r: dict[str, int]) -> dict[str, int]:
        # gene start基準 1-based に直し、逆鎖なら反転
        if strand == 1:
            return {"start": r["start"] - gene_start + 1, "end": r["end"] - gene_start + 1}
        new_start = length - (r["end"] - gene_start + 1) + 1
        new_end = length - (r["start"] - gene_start + 1) + 1
        return {"start": min(new_start, new_end), "end": max(new_start, new_end)}

    return {
        "seq_region_name": seqid,
        "start": 1,
        "end": length,
        "strand": strand,
        "length": length,
        "sequence": region_seq,
        "exons": [norm(r) for r in exons],
        "cds": [norm(r) for r in cds_list],
        "source": "local",
    }
