"""Minimal, dependency-free GFF3 parser with a gene/mRNA/exon/CDS hierarchy.

GFF3 columns are 1-based, inclusive on both ends -- this module keeps that
native convention throughout (do not confuse with BED, which is 0-based
half-open). All coordinates returned here are therefore 1-based inclusive.

Only the Python standard library is used, and both plain-text and gzip-
compressed (`.gff3.gz`) files are supported (detected by extension).
"""
from __future__ import annotations

import gzip
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


def _open_text(path: str) -> io.TextIOBase:
    """Open a possibly gzip-compressed file as UTF-8 text (by extension)."""
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "rt", encoding="utf-8", newline="")


def _unescape(value: str) -> str:
    """URL-unescape the `%XX` sequences GFF3 allows inside attribute values."""
    if "%" not in value:
        return value
    out: List[str] = []
    i = 0
    n = len(value)
    while i < n:
        c = value[i]
        if c == "%" and i + 2 < n + 1 and i + 3 <= n:
            hexs = value[i + 1 : i + 3]
            try:
                out.append(chr(int(hexs, 16)))
                i += 3
                continue
            except ValueError:
                pass
        out.append(c)
        i += 1
    return "".join(out)


def _parse_attributes(field9: str) -> Dict[str, str]:
    """Parse the 9th GFF3 column (`key=value;key=value`) into a dict."""
    attrs: Dict[str, str] = {}
    for chunk in field9.strip().split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        attrs[key.strip()] = _unescape(val.strip())
    return attrs


def normalize_gene_key(value: str) -> str:
    """Normalize typography, not biological identifier suffixes or punctuation."""
    key = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"^gene\s*:\s*", "", key).strip()


def gene_attribute_keys(attributes: Dict[str, str]) -> List[str]:
    keys = [attributes[key] for key in ("ID", "Name", "gene_id", "locus_tag")
            if attributes.get(key)]
    keys.extend(attributes.get("Alias", "").split(","))
    return [normalize_gene_key(key) for key in keys if key]


@dataclass
class Feature:
    """A single GFF3 record. Coordinates are 1-based, inclusive."""

    seqid: str
    source: str
    type: str
    start: int
    end: int
    strand: str  # '+', '-' or '.'
    attributes: Dict[str, str]
    children: List["Feature"] = field(default_factory=list)

    @property
    def length(self) -> int:
        """Span length in bp (inclusive)."""
        return self.end - self.start + 1

    @property
    def id(self) -> Optional[str]:
        return self.attributes.get("ID")

    @property
    def name(self) -> Optional[str]:
        return self.attributes.get("Name")

    @property
    def parent(self) -> Optional[str]:
        return self.attributes.get("Parent")


class Gff3:
    """Parsed GFF3 features with a gene index and hierarchy links."""

    def __init__(self, features: List[Feature]) -> None:
        self.features: List[Feature] = features
        # Index genes by their ID and (when present) their Name for lookup.
        self._by_gene_key: Dict[str, Feature] = {}
        self._by_gene_id: Dict[str, List[Feature]] = {}
        self._normalized_genes: Dict[str, List[Feature]] = {}
        # Bucket features by seqid to keep overlap queries reasonable.
        self._by_seqid: Dict[str, List[Feature]] = {}
        for feat in features:
            self._by_seqid.setdefault(feat.seqid, []).append(feat)
            if feat.type == "gene":
                if feat.id:
                    self._by_gene_key.setdefault(feat.id, feat)
                    self._by_gene_id.setdefault(feat.id, []).append(feat)
                if feat.name:
                    self._by_gene_key.setdefault(feat.name, feat)
                for key in set(gene_attribute_keys(feat.attributes)):
                    self._normalized_genes.setdefault(key, []).append(feat)

    def gene(self, gene_id: str) -> Optional[Feature]:
        """Look up a gene by its ID or Name."""
        # Exact stable IDs take precedence over aliases. Never guess between
        # two genes with a shared symbol or normalized spelling.
        literal = unicodedata.normalize("NFKC", gene_id).strip()
        exact = self._by_gene_id.get(literal, [])
        key = normalize_gene_key(gene_id)
        matches = self._normalized_genes.get(key, [])
        stable_ids = [feat for feat in matches if feat.id and normalize_gene_key(feat.id) == key]
        matches = exact or stable_ids or matches
        if len(matches) > 1:
            ids = ", ".join(feat.id or feat.name or "?" for feat in matches[:8])
            raise ValueError(f"Ambiguous gene ID '{gene_id}': {ids}. Use an exact stable gene ID.")
        return matches[0] if matches else None

    def features_in(
        self,
        seqid: str,
        start: int,
        end: int,
        types: Optional[Sequence[str]] = None,
    ) -> List[Feature]:
        """Return features overlapping the 1-based inclusive window
        [start, end] on `seqid`, optionally restricted to `types`."""
        want = set(types) if types else None
        hits: List[Feature] = []
        for feat in self._by_seqid.get(seqid, ()):
            if feat.end < start or feat.start > end:
                continue
            if want is not None and feat.type not in want:
                continue
            hits.append(feat)
        return hits

    def gene_region(
        self, gene_id: str, feature: str = "gene"
    ) -> Optional[Tuple[str, int, int, str]]:
        """Return (seqid, start, end, strand) for a gene, 1-based inclusive.

        `feature` selects which span to report:
          - 'gene': the gene feature's own span.
          - 'mrna': the span of the gene's longest mRNA child.
          - 'cds' / 'exon': over that longest mRNA, the min-start..max-end
            across its CDS (resp. exon) children.
        """
        gene = self.gene(gene_id)
        if gene is None:
            return None
        feature = feature.lower()
        if feature == "gene":
            return (gene.seqid, gene.start, gene.end, gene.strand)

        # Pick the longest mRNA child of the gene.
        mrnas = [c for c in gene.children if c.type.lower() == "mrna"]
        if not mrnas:
            return None
        mrna = max(mrnas, key=lambda m: m.length)
        if feature == "mrna":
            return (mrna.seqid, mrna.start, mrna.end, mrna.strand)

        target = "cds" if feature == "cds" else "exon"
        subs = [c for c in mrna.children if c.type.lower() == target]
        if not subs:
            return None
        start = min(c.start for c in subs)
        end = max(c.end for c in subs)
        return (mrna.seqid, start, end, mrna.strand)


def parse_gff3(path: str, seqid: Optional[str] = None) -> Gff3:
    """Parse a GFF3 file (plain or `.gz`) into a :class:`Gff3`.

    The 9 tab-separated columns are split, attributes are decoded, and a
    gene -> mRNA -> (exon/CDS/UTR) hierarchy is rebuilt by matching each
    feature's `Parent` attribute to another feature's `ID`.

    `seqid`, if given, keeps only records on that chromosome -- useful to
    bound memory/time on very large genome annotations.
    """
    features: List[Feature] = []
    by_id: Dict[str, Feature] = {}

    with _open_text(path) as fh:
        for line in fh:
            if not line or line[0] == "#":
                continue
            line = line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            cols = line.split("\t")
            if len(cols) < 9:
                continue
            if seqid is not None and cols[0] != seqid:
                continue
            try:
                start = int(cols[3])
                end = int(cols[4])
            except ValueError:
                continue
            attrs = _parse_attributes(cols[8])
            feat = Feature(
                seqid=cols[0],
                source=cols[1],
                type=cols[2],
                start=start,
                end=end,
                strand=cols[6],
                attributes=attrs,
            )
            features.append(feat)
            fid = feat.id
            if fid:
                # First definition of an ID wins as the hierarchy anchor.
                by_id.setdefault(fid, feat)

    # Link children to parents. A feature may list several Parent IDs.
    for feat in features:
        parent_field = feat.parent
        if not parent_field:
            continue
        for pid in parent_field.split(","):
            parent = by_id.get(pid.strip())
            if parent is not None:
                parent.children.append(feat)

    return Gff3(features)


if __name__ == "__main__":
    import time

    path = "pisum_v2/pisum_v2.gff3"
    t0 = time.time()
    gff = parse_gff3(path, seqid="chr1")
    print(f"parsed {len(gff.features)} features (chr1) in {time.time() - t0:.1f}s")

    gid = "Psat.cameor.v2.1g00050"
    print("gene :", gff.gene_region(gid, "gene"))
    print("mrna :", gff.gene_region(gid, "mrna"))
    print("cds  :", gff.gene_region(gid, "cds"))
    print("exon :", gff.gene_region(gid, "exon"))

    hits = gff.features_in("chr1", 5000, 7000, types=["gene", "mRNA"])
    print(f"features_in chr1:5000-7000 (gene/mRNA): {len(hits)}")
    for h in hits:
        print("  ", h.type, h.start, h.end, h.id)
