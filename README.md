# Primer Workbench

[![CI](https://github.com/light-suzuki/PrimerWorkbench/actions/workflows/ci.yml/badge.svg)](https://github.com/light-suzuki/PrimerWorkbench/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/light-suzuki/=semver)](https://github.com/light-suzuki/PrimerWorkbench/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)


Primer3設計、ローカル特異性確認、既存プライマー逆引きに絞ったWindows GUI。 | A focused Windows GUI for Primer3 design, local specificity checks, and existing-primer lookup.

This is a source-level focused distribution of
[Gene Research / Sequence Workbench](https://github.com/light-suzuki/Gene-research).
Unrelated top-level UI panels are removed; this is not the full application with
hidden tabs.

## Features / 機能

- Design primer pairs with WSL Primer3
- Check candidates against user-supplied local BLAST databases
- Locate existing primer pairs and predict amplicons

## Requirements / 必要条件

- Windows 10 or 11
- WSL2 Ubuntu
- Node.js 20+ on Windows
- WSL packages: Python 3, `python3-venv`, Primer3 and NCBI BLAST+, and `netcat-openbsd`
- Your own genome/BLAST database where the workflow requires one

Windows版Primer3は使用しません。Primer3を使う機能はWSL版だけをサポートします。

## Install and start / 導入と起動

```powershell
wsl --install -d Ubuntu
wsl -d Ubuntu -- bash -lc "sudo apt update && sudo apt install -y python3 python3-venv ncbi-blast+ primer3 netcat-openbsd"
git clone https://github.com/light-suzuki/PrimerWorkbench.git
cd PrimerWorkbench
.\start_windows.bat
```

The launcher installs exact frontend dependencies with `npm ci`, creates the
backend virtual environment at `~/.sequence_workbench/venvs/bioapi` in WSL,
starts FastAPI on port 8000 and the UI on port 5173, then opens the browser.

初回は依存関係を導入します。backendはWSL、frontendとブラウザはWindowsで動きます。

Stop:

```powershell
.\stop_windows.ps1
```

## Register your own BLAST database / 手持ちDBの登録

Store data outside the cloned repository:

```bash
mkdir -p ~/sequence-workbench-data/blastdb
makeblastdb -in ~/sequence-workbench-data/my_genome.fa -dbtype nucl \
  -out ~/sequence-workbench-data/blastdb/my_genome
```

Register `/home/<your-wsl-user>/sequence-workbench-data/blastdb/my_genome`.
Use the `-out` prefix, not an individual index file. Data and indexes are not
included in this repository.

## Optional paths / 任意設定

```powershell
Copy-Item backend\bioapi\.env.wsl.example backend\bioapi\.env.wsl
```

Use Linux paths in `.env.wsl`. Windows paths such as `C:\data` are rejected.

## Verify / 検証

```powershell
npm --prefix frontend\workbench ci
npm --prefix frontend\workbench run typecheck
npm --prefix frontend\workbench run build
npm --prefix frontend\workbench audit --audit-level=moderate
.\start_windows.bat
Invoke-RestMethod http://127.0.0.1:8000/health
.\stop_windows.ps1
```

## Troubleshooting / トラブルシュート

- Startup logs: `.runtime\backend.error.log`, `proxy.error.log`, and `frontend.error.log`
- Primer3: run `wsl which primer3_core`
- BLAST+: run `wsl which blastn`
- Missing DB: register the WSL `makeblastdb -out` prefix
- Port conflict: stop the exact process using 5173 or 8000
- Do not install or configure Windows-native Primer3

## Clean removal / 完全削除

Run `stop_windows.ps1`, then delete the clone. To also remove the shared test
environment and example data:

```powershell
wsl -d Ubuntu -- bash -lc "rm -rf ~/.sequence_workbench/venvs/bioapi ~/sequence-workbench-data"
```

No user genome, accession, sequence, private path, credential, or result is
bundled. The app does not delete user data automatically.

## License and contributors

MIT License. See [CONTRIBUTORS.md](CONTRIBUTORS.md) for project and AI-assistance
credits.
