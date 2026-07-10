#!/bin/bash
# harvest_only.sh — 只跑 Phase 2（picks.jsonl 已存在時使用）
# 使用：./script/harvest_only.sh [--dry-run] [--self-check]
set -e

SEAM_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SEAM_DIR"
source venv/bin/activate 2>/dev/null || true

echo "══ Harvest: clone + analyze + report ══"
python seam_harvest_entry.py "$@"
