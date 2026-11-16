# CLI-first workbench

```text
Local agent / shell -> app.cli -----------+
                                         +-> app.application -> services -> local tools/data
Browser GUI -> HTTP / job adapters -------+
```

`app/application.py` is the shared experiment boundary. Transport adapters handle
JSON, HTTP status and job lifecycle; services own the scientific engines. New
experiment behavior should be placed in application/services and exposed through
both adapters rather than duplicated in React or HTTP handlers.

From `backend/bioapi`, using the installed backend environment:

```sh
python -m app.cli schema
python -m app.cli run --input request.json
```

`--input -` reads stdin. A synthetic request:

```json
{"request_id":"example-1","operation":"sequence.restriction","params":{"sequence":"GGGAATTCCC","enzymes":["EcoRI-HF"]}}
```

The schema command lists typed JSON schemas for:

- `primerblast.design` (embedded PrimerBLAST OSS design and specificity)

- `sequence.basic`, `sequence.orfs`, `sequence.restriction`
- `primers.design`, `caps.design`
- `sequence.fetch`, `gene.structure`, `blast.local`, `enzymes.catalog`

These use the same core functions as their GUI/API counterparts. `gene.structure`
takes explicit local `gene_id`, `gff_path` and `fasta_path`. `blast.local` rejects
external backends; agent operations do not upload sequences or start a server.
Unknown operation/input fields are rejected, and database creation is not part of
this CLI contract. Existing remote GUI operations and specialized batch adapters
are retained for compatibility and are not advertised as local CLI capabilities.

Stdout is a single UTF-8 JSON envelope with `schema_version`, `ok`, `operation`
and `result`, or an error with a stable category and message. Optional `request_id`
is echoed. Diagnostics go to stderr. Exit codes: 0 completed, 2 invalid input,
1 execution failure. `ok` describes execution, not scientific or Wet validation;
existing metadata, coordinates and incomplete-search evidence must be interpreted
separately.

The GUI keeps its existing routes, saved results and tabs. Local paths, reference
catalogs and annotation caches stay private. No model service or credentials are
needed; the caller's agent can use these commands as local tools. Larger internal
BLAST engine/batch modules remain behind their service boundary and can be split
later without changing the CLI contract.
