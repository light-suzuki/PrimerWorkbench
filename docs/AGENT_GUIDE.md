# Agent Guide

This file is for AI coding agents adapting Sequence Workbench for another user
or another reference genome.

## Goal

Keep this repository as a generic local workbench. The app should let users add
their own genomes, BLAST databases, and Primer3/BLAST+ installations without
committing those data or binaries to Git.

## Main Extension Point

Reference presets belong here:

```text
frontend/workbench/src/config/referencePresets.ts
```

Add public example references there only. Do not scatter organism names through
React components.

## User Data Boundary

Never commit:

- FASTA, GFF, GTF, BED, VCF, or BLAST index files from a user's private dataset
- API keys, `.env` files, or credential snippets
- absolute home-directory paths
- drive-specific data paths
- generated `node_modules`, `dist`, `.venv`, logs, or cache folders

The user should register local BLAST DB prefixes through DB Manager or local
environment variables.

## Expected Local Tools

The app can use these tools when the user installs them:

- Node.js for the frontend
- Python for the FastAPI backend
- BLAST+ for local sequence search
- Primer3 for primer design
- WSL only when the user's local toolchain requires it

Do not assume these binaries are bundled.

## Safe Adaptation Steps

1. Update `frontend/workbench/src/config/referencePresets.ts` for public
   presets.
2. Update docs if setup paths or commands change.
3. Keep UI labels generic unless a preset is intentionally public.
4. Keep BioAPI bound to `127.0.0.1` by default.
5. Preserve user-supplied database registration flow.
6. Run focused validation before handing off.

## Validation Commands

From the repository root:

```powershell
npm --prefix frontend\workbench run typecheck
npm --prefix frontend\workbench run build
python -m py_compile backend\bioapi\app\main.py
```

If a backend is running:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/tools/status
Invoke-RestMethod http://127.0.0.1:8000/blast/local_dbs
```

For UI work, open the frontend in a real browser and check all visible tabs.

## Terms To Avoid In Public UI

Avoid project-specific labels, private organism names, private accession
systems, lab data nicknames, and hardware-specific acceleration labels unless
the current task explicitly asks for them.

Use generic labels such as:

- user reference
- local BLAST DB
- makeblastdb prefix
- public preset
- custom reference
- external reference browser
