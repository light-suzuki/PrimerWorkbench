# Primerblast-oss features in the existing workbench

The Sequence and CAPS panels reuse selected public components and catalog data
from [primerblast-oss](https://github.com/light-suzuki/primerblast-oss), revision
`8f0c19e393ca179eb083ec8278f4031772c90926`. No additional navigation tabs are needed.

- Sequence: search 1,088 restriction enzyme names, verified product aliases and
  recognition sequences; expand the actual two-strand cleavage window.
- CAPS: expand each marker to inspect both oriented PCR products, primer binding
  ranges and cleavage windows; compare AA/AB/BB schematic lanes and PCR product
  sizes per searched database.
- Local GFF3 lookup: preserve dotted gene IDs, normalize full-width characters
  and case, resolve annotation aliases, and reject ambiguous matches. When local
  annotation is configured, a missing gene does not trigger an external lookup.

Cut positions retain Biopython's 1-based first-base-after-cut convention. Added
top/bottom boundaries count bases before the cleavage boundary, starting at zero.
The catalog is Biopython 1.87 / REBASE 404 (2024), not a complete current product
inventory. Special substrates and multiple-cut models remain browsable but are
excluded from this automatic CAPS prediction. Conditions and methylation
sensitivity must be checked against the selected supplier's documentation.

Gel lanes are illustrative in-silico fragment positions, not measured migration
or evidence of amplification. Wet validation is still required. PCR product
sizes include all returned amplicons and do not independently classify off-targets.
Existing export and local persistence remain available; legacy records without
the new fields continue to render. The added product sequences and cut windows
are included in the API response, not automatically uploaded anywhere.

Only public catalog data and synthetic test fixtures are checked in. User genomes,
database paths and private annotations remain local. Attribution is recorded in
`THIRD_PARTY_PRIMERBLAST_OSS_LICENSE.txt` and
`THIRD_PARTY_BIOPYTHON_LICENSE.rst`.
