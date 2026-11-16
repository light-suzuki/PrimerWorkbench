# Research workflow workbench

Sequence Workbench is the upper application for breeding and gene research.
The Workflow entry can send the target sequence directly to PCR design/screening.
FASTA names and separate records are preserved. Sequence handoffs specify their
destination so previously opened hidden tabs do not consume another tool's input.
It presents research goals, sequence/gene context, experiment inputs, visualization
and saved results. It combines existing open-source engines rather than rebuilding
each engine in React. New tabs require a distinct research goal, not a new library.

PrimerBLAST OSS is an embedded Python engine, also usable by its standalone CLI.
Workbench imports the package directly; it does not require a second web server,
iframe or browser automation. There is no reverse dependency on Workbench.

```text
Workbench GUI / agent CLI
        -> Workbench application and jobs
        -> PrimerBLAST OSS -> Primer3 / BLAST+ -> user-managed local data
        -> other sequence / annotation / restriction services
```

All existing Primer3 design consumers (primers, exon/sequencing primers and CAPS)
use the engine's compatibility adapter with the previous input settings and
1-based API output coordinates. This avoids silently changing established assay
conditions. Workbench no longer owns a copy of the Primer3 subprocess/parser.

The PrimerBLAST tab runs the engine's full design/specificity workflow in an
existing Workbench background job. Its screen only displays engine results;
it no longer classifies primer BLAST hits with a separate frontend algorithm.
`primerblast.design` exposes the same workflow through the local agent CLI.
Search completeness and Wet-unverified evidence remain visible. Expected-size
products are classified by size, not proof of genomic target identity.

Cancellation is cooperative at the design/search boundary. It does not immediately
terminate an active native subprocess. Existing reference registration, general
BLAST, CAPS and sequence analysis remain Workbench responsibilities. CAPS-specific
comparison logic and older primer-lookup views have not all been migrated.

Install backend dependencies from `backend/bioapi/requirements.txt`; the engine is
pinned to a public Git commit. Do not copy engine source into Workbench or use
private absolute paths as package dependencies. Keep genome/annotation/DB data
outside Git. Ordinary design and screening do not create or modify databases.
