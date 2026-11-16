"""Offline restriction catalog; no runtime dependency or network access."""
import json
import unicodedata
from functools import lru_cache
from pathlib import Path

# These cannot be modeled as an ordinary, unmethylated, single-site PCR digest.
SPECIAL_SUBSTRATES = {
    "DpnI", "AbaSI", "FspEI", "LpnPI", "MspJI", "SgrTI", "McrBC",
    "SauUSI", "SgeI", "EcoP15I", "EcoPI",
}


def normalize_enzyme_name(name):
    return "".join(unicodedata.normalize("NFKC", str(name)).split()).casefold()


def cleavage_pattern(recognition, cuts):
    """Both strands share left-to-right boundaries; N denotes flanking bases."""
    if not cuts:
        return None
    complement = str.maketrans("ACGTRYSWKMBDHVN", "TGCAYRSWMKVHDBN")
    left = min([0] + [position for pair in cuts for position in pair])
    right = max([len(recognition)] + [position for pair in cuts for position in pair])
    top = "N" * -left + recognition + "N" * (right - len(recognition))
    bottom = top.translate(complement)
    all_boundaries = set(position - left for pair in cuts for position in pair)
    def marked(sequence, positions):
        boundaries = set(position - left for position in positions)
        return "".join(("|" if i in boundaries else " " if i in all_boundaries else "") + base for i, base in enumerate(sequence)) + ("|" if len(sequence) in boundaries else " " if len(sequence) in all_boundaries else "")
    return {"top": "5′ " + marked(top, [pair[0] for pair in cuts]) + " 3′",
            "bottom": "3′ " + marked(bottom, [pair[1] for pair in cuts]) + " 5′",
            "flank_left": -left, "flank_right": right - len(recognition)}


@lru_cache(maxsize=1)
def catalog():
    data = json.loads(Path(__file__).with_name("restriction_catalog.json").read_text(encoding="utf-8"))
    rows = data["enzymes"]
    groups = {}
    for row in rows:
        groups.setdefault(row["recognition"], []).append(row)
    for row in rows:
        others = groups[row["recognition"]]
        # Unknown cut geometry cannot establish equivalence.
        row["same_cut_enzymes"] = [other["name"] for other in others if other["name"] != row["name"] and row["cuts"] and other["cuts"] == row["cuts"]]
        row["different_cut_enzymes"] = [other["name"] for other in others if other["cuts"] and row["cuts"] and other["cuts"] != row["cuts"]]
        row["unknown_cut_enzymes"] = [other["name"] for other in others if other["name"] != row["name"] and not other["cuts"]]
        row["pattern"] = cleavage_pattern(row["recognition"], row["cuts"])
        row["prediction_supported"] = len(row["cuts"]) == 1 and row["name"] not in SPECIAL_SUBSTRATES
        row["commercial_in_snapshot"] = bool(row["suppliers"])
        row["reference_url"] = "https://identifiers.org/rebase:" + str(row["rebase_id"])
    return data


@lru_cache(maxsize=1)
def catalog_index():
    index = {}
    for row in catalog()["enzymes"]:
        for name in [row["name"]] + row.get("product_names", []):
            index[normalize_enzyme_name(name)] = row
    return index


def enzyme_info(name):
    return catalog_index().get(normalize_enzyme_name(name))
