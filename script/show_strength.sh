#!/bin/bash
# show_strength.sh — 印每個 repo 的 STRENGTH.md summary
# 使用：./script/show_strength.sh [目標路徑]

TARGET="${1:-/Volumes/2T_260130/py_in_2T/github_open_project}"

if [ ! -d "$TARGET" ]; then
    echo "❌ target dir not found: $TARGET"
    exit 1
fi

COUNT=0
for md in "$TARGET"/*/*/STRENGTH.md; do
    [ -f "$md" ] || continue
    COUNT=$((COUNT + 1))

    # 抓 repo 名（倒數第二個目錄，__ → /）
    repo_dir=$(basename "$(dirname "$md")")
    repo=$(echo "$repo_dir" | sed 's/__/\//')

    echo "══════════════════════════════════════"
    echo "  $repo"
    echo "══════════════════════════════════════"

    # 印 Summary 區段 + Tags 行
    python3 - "$md" <<'PYEOF'
import sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    lines = f.readlines()

in_summary = False
for line in lines:
    stripped = line.rstrip()
    if stripped == "## Summary":
        in_summary = True
        continue
    if in_summary and stripped.startswith("## "):
        in_summary = False
    if in_summary and stripped:
        print(" ", stripped)
    if stripped.startswith("**Tags:**"):
        print(" ", stripped)
        break
PYEOF
    echo ""
done

if [ "$COUNT" -eq 0 ]; then
    echo "（尚無 STRENGTH.md — 請先跑 seam_harvest_entry.py）"
fi
