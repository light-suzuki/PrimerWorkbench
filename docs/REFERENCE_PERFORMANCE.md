# Read-only reference access

Local GFF gene structure lookup uses a disposable SQLite query index, shared in
implementation with primerblast-oss. It streams the annotation once on first use,
then reads only the requested gene and its descendants. Width/case normalization,
alias ambiguity checks and transcript hierarchy are preserved. The initial build
runs outside the async API event loop so other requests can continue.

Sources are not rewritten. Cache files live in the user's
`.cache/sequence-reference-queries` folder, or a separate directory set with
`SEQWB_REFERENCE_CACHE_DIR`; they are not created next to reference databases.
These files contain annotation data and must remain private. Path, size and
modification/change timestamps invalidate the cache when the source changes.
Only completed builds are published atomically. Old cache files can be removed
while the app is stopped and will be rebuilt on demand.

Gene sequence extraction uses an existing `.fai` for direct range access when
available. Without one (including gzip), it streams only as far as the requested
range and retains the requested bases rather than an entire chromosome. No `.fai`
is generated. BLAST's FASTA fallback also uses existing indexes; its scan-layout
cache now invalidates when the source changes. At most eight small `.fai` metadata
dictionaries are reused. BLAST databases, search parameters and scoring remain
unchanged.

Run `python scripts/benchmark_reference_access.py` for the bounded synthetic
5,000-gene / 15,000-feature comparison. It reports whole-file parsing, first index
build and warm query timings and Python allocation peaks, verifies identical exon
coordinates, and checks that the source hash is unchanged. Machine-specific
synthetic results do not establish real-genome BLAST speed or Wet validity. The
first index build can be slower and consumes additional cache disk space.
