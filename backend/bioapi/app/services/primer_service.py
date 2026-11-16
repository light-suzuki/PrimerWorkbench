"""Compatibility adapter; PrimerBLAST OSS owns all Primer3 design execution."""
from primerblast_oss.workbench_design import (
    Primer3NotFoundError, Primer3ExecutionError, PrimerCandidate,
    design_primers, get_primer3_version,
)
