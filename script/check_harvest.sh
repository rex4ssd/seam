#!/bin/bash
# check_harvest.sh — 列出 2T 上已 harvest 的 repo
# 使用：./script/check_harvest.sh [目標路徑]

TARGET="${1:-/Volumes/2T_260130/py_in_2T/github_open_project}"
INDEX="$TARGET/_index.jsonl"

if [ ! -f "$INDEX" ]; then
    echo "❌ index not found: $INDEX"
    echo "   尚未 harvest，或 2T 未掛載"
    exit 1
fi

echo "📦 Harvested repos in $TARGET"
echo "─────────────────────────────────────────────────────"

# 列出 index，顯示 id / tags / analyzed_at
python3 - <<'PYEOF'
import json, sys, os

index = os.environ.get("INDEX", "/Volumes/2T_260130/py_in_2T/github_open_project/_index.jsonl")
rows = []
with open(index) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except:
            pass

rows.sort(key=lambda r: r.get("analyzed_at", ""), reverse=True)

for r in rows:
    slug     = r.get("candidate_id", "?")
    tags     = " ".join(r.get("strength_tags", [])) or "(none)"
    lang     = r.get("language", "?")
    stars    = r.get("stars", 0)
    sloc     = r.get("sloc", 0)
    date_str = r.get("analyzed_at", "")[:10]
    engine   = r.get("engine", "?")
    print(f"  {date_str}  {slug:<35} ★{stars:<6} [{lang:<4}] {tags}")

print(f"\n  total: {len(rows)} repo(s)")
PYEOF
