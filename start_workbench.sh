#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/frontend/workbench"
npm install
npm run dev
