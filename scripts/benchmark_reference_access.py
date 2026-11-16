"""Bounded synthetic benchmark. Never opens or modifies a user's reference DB."""
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "bioapi"))
from app.services.annotation_index import gene_annotation
from app.services.gff3 import parse_gff3


def measure(operation):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    result = operation()
    seconds = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {"seconds": round(seconds, 6), "peak_python_bytes": peak}


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "synthetic.gff3"
        with source.open("w") as fh:
            for i in range(5000):
                start = 100 * i + 1
                fh.write(f"chr1\ttest\tgene\t{start}\t{start+49}\t.\t+\t.\tID=g{i}\n")
                fh.write(f"chr1\ttest\tmRNA\t{start}\t{start+49}\t.\t+\t.\tID=t{i};Parent=g{i}\n")
                fh.write(f"chr1\ttest\texon\t{start}\t{start+19}\t.\t+\t.\tParent=t{i}\n")
        previous = os.environ.get("SEQWB_REFERENCE_CACHE_DIR")
        os.environ["SEQWB_REFERENCE_CACHE_DIR"] = str(root / "cache")
        try:
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            expected, baseline = measure(lambda: parse_gff3(str(source)).gene_region("g2500", "exon"))
            first, cold = measure(lambda: gene_annotation(source, "g2500").gene_region("g2500", "exon"))
            warm_result, warm = measure(lambda: gene_annotation(source, "g2500").gene_region("g2500", "exon"))
            assert expected == first == warm_result
            unchanged = hashlib.sha256(source.read_bytes()).hexdigest() == before
            assert unchanged
            print(json.dumps({"synthetic_genes": 5000, "synthetic_features": 15000,
                              "full_parse": baseline, "first_indexed_query": cold,
                              "warm_indexed_query": warm, "source_unchanged": unchanged}))
        finally:
            if previous is None:
                os.environ.pop("SEQWB_REFERENCE_CACHE_DIR", None)
            else:
                os.environ["SEQWB_REFERENCE_CACHE_DIR"] = previous


if __name__ == "__main__":
    main()
