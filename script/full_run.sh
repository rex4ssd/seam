#!/bin/bash
# full_run.sh — Phase 1 (search+score+pick) + Phase 2 (clone+analyze+report)
# 使用：./script/full_run.sh [--dry-run]
set -e

SEAM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SEAM_DIR"
source venv/bin/activate 2>/dev/null || true

echo "══ Phase 1: search + score + pick ══"
seam run --verbose

echo ""
echo "══ Phase 2: clone + analyze + report ══"
python seam_harvest_entry.py "$@"
