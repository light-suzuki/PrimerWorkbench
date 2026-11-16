"""Primerblast-oss catalog adapter; retain Gene Research's Biopython coordinates."""
import re

from Bio.Restriction import AllEnzymes

from .enzyme_catalog import catalog, cleavage_pattern, enzyme_info, normalize_enzyme_name

IUPAC = dict(zip("ACGTRYSWKMBDHVN", ["A", "C", "G", "T", "AG", "CT", "CG", "AT", "GT", "AC", "CGT", "AGT", "ACT", "ACG", "ACGT"]))
COMPLEMENT = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")
AVAILABLE = {normalize_enzyme_name(enzyme.__name__): enzyme for enzyme in AllEnzymes}


def resolve_enzyme(name):
    info = enzyme_info(name)
    canonical = info["name"] if info else name
    return AVAILABLE.get(normalize_enzyme_name(canonical))


def enzyme_detail(name, sequence=None):
    info = enzyme_info(name)
    if info is None:
        return None
    result = {key: info[key] for key in (
        "name", "recognition", "cuts", "pattern", "same_cut_enzymes",
        "different_cut_enzymes", "prediction_supported", "reference_url")}
    result["product_names"] = info.get("product_names", [])
    result["source"] = catalog()["release"] + " / REBASE " + catalog()["rebase_version"]
    result["windows"] = []
    if not sequence or not info["prediction_supported"]:
        return result
    motif = info["recognition"]
    reverse = motif.translate(COMPLEMENT)[::-1]
    orientations = [("+", motif)] + ([("-", reverse)] if reverse != motif else [])
    for strand, recognition in orientations:
        regex = "(?=(" + "".join("[" + IUPAC[base] + "]" for base in recognition) + "))"
        for match in re.finditer(regex, sequence.upper()):
            start = match.start()
            top, bottom = info["cuts"][0]
            if strand == "-":
                top, bottom = len(motif) - bottom, len(motif) - top
            top, bottom = start + top, start + bottom
            if not 0 <= top <= len(sequence) or not 0 <= bottom <= len(sequence):
                continue
            left = max(0, min(start, top, bottom) - 8)
            right = min(len(sequence), max(start + len(motif), top, bottom) + 8)
            result["windows"].append({
                "site_start": start + 1, "strand": strand,
                "top_boundary": top, "bottom_boundary": bottom,
                "window_start": left + 1, "window_end": right,
                "pattern": cleavage_pattern(sequence[left:right], [[top-left, bottom-left]]),
            })
    return result
