# Adapting Reference Genomes

This public copy does not bundle private genomes, BLAST indexes, Primer3
binaries, API keys, or machine-local paths.

## Where To Add A Public Preset

Edit:

`frontend/workbench/src/config/referencePresets.ts`

Add one entry to `REFERENCE_PRESETS` when you want a public example genome to
appear in DB Manager or optional target presets.

Keep private lab genomes out of the repository. Users should provide their own
FASTA/GFF files and BLAST indexes locally.

## Required Local Setup

1. Put the reference FASTA/GFF outside this repository.
2. Build the BLAST index with `makeblastdb`.
3. Start the BioAPI backend.
4. Open DB Manager and register the `makeblastdb` prefix.
5. Use that DB from BLAST, BLAST-OR, PrimerBLAST, CDS/exon amplification, or
   primer reverse lookup.

## Public Defaults

The only built-in public example preset is Arabidopsis thaliana.

Removed from this public copy:

- bundled private genomes or local data paths
- non-default organism presets
- lab- or project-specific plant presets
- accelerator-specific BLAST UI
- external reference BLAST UI
- API-key based workflows

## Notes For AI Agents

When adapting this project, prefer adding or changing reference presets in
`referencePresets.ts` instead of scattering species names through components.
Do not hard-code user home directories, drive letters, API keys, or lab-specific
genome identifiers in source files.
