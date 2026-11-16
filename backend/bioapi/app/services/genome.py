"""Random-access genome sequence via a `.fai` index (samtools faidx format).

Lets the pipeline pull a template region straight out of a multi-hundred-Mbp
chromosome by seeking, and convert between genomic coordinates and a local
template -- the anchor that makes every primer's strand/coordinates explicit.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

_COMP = str.maketrans("ACGTNacgtnRYSWKMBDHVryswkmbdhv",
                      "TGCANtgcanYRSWMKVHDByrswmkvhdb")


def revcomp(seq: str) -> str:
    return seq.translate(_COMP)[::-1]


@dataclass(frozen=True)
class _FaiEntry:
    length: int
    offset: int
    linebases: int
    linewidth: int


@lru_cache(maxsize=8)
def _load_index(path, size, mtime_ns, ctime_ns):
    index = {}
    with open(path) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            name, length, offset, lb, lw = parts[:5]
            entry = _FaiEntry(int(length), int(offset), int(lb), int(lw))
            if entry.length < 0 or entry.offset < 0 or entry.linebases <= 0 or entry.linewidth < entry.linebases:
                raise ValueError("Invalid FASTA index layout")
            index[name] = entry
    return index


class Genome:
    """A FASTA + .fai index. Coordinates are 1-based inclusive."""

    def __init__(self, fasta: str):
        self.fasta = fasta
        self.fai_path = fasta + ".fai"
        if not Path(self.fai_path).exists():
            raise RuntimeError(
                f"FASTA index not found: {self.fai_path} (run `samtools faidx {fasta}`)")
        stat = Path(self.fai_path).stat()
        self.index: Dict[str, _FaiEntry] = dict(_load_index(
            str(Path(self.fai_path).resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))

    def __contains__(self, name: str) -> bool:
        return name in self.index

    def length(self, name: str) -> int:
        return self.index[name].length

    def chroms(self):
        return list(self.index.keys())

    def fetch(self, name: str, start: int, end: int, strand: str = "+") -> str:
        """Fetch bases [start, end] (1-based inclusive). strand '-' returns the
        reverse complement, so the result always reads 5'->3' on that strand."""
        if name not in self.index:
            raise KeyError(f"sequence '{name}' not in {self.fai_path}")
        e = self.index[name]
        start = max(1, start)
        end = min(e.length, end)
        if end < start:
            return ""
        want = end - start + 1
        start0 = start - 1
        byte_start = e.offset + (start0 // e.linebases) * e.linewidth + (start0 % e.linebases)
        end0 = end - 1
        byte_end = e.offset + (end0 // e.linebases) * e.linewidth + (end0 % e.linebases)
        with open(self.fasta, "rb") as fh:
            fh.seek(byte_start)
            raw = fh.read(byte_end - byte_start + 1)
        seq = raw.replace(b"\n", b"").replace(b"\r", b"")[:want].decode().upper()
        if len(seq) != want or ">" in seq:
            raise ValueError("FASTA index does not match the requested sequence range")
        return revcomp(seq) if strand == "-" else seq

    def local_to_genomic(self, name: str, region_start: int, strand: str,
                         local_index0: int) -> int:
        """Map a 0-based index on a template extracted at `region_start`
        (1-based genomic start of the extraction) back to a genomic coordinate.
        For '+' the template runs 5'->3' with genome; for '-' it is revcomp."""
        if strand == "-":
            # region_start here is the genomic END (higher coord) of extraction
            return region_start - local_index0
        return region_start + local_index0
