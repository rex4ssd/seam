#!/usr/bin/env python3
"""
index_query.py — 查詢 _index.jsonl

使用：
  python script/index_query.py                    # 列全部
  python script/index_query.py --tag tech_strong  # 有這個 tag 的
  python script/index_query.py --lang rust        # 主語言是 rust 的
  python script/index_query.py --min-stars 5000   # 星數 >= N
  python script/index_query.py --sort stars       # 依星數排序
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


DEFAULT_INDEX = Path("/Volumes/2T_260130/py_in_2T/github_open_project/_index.jsonl")


def main():
    parser = argparse.ArgumentParser(description="Query _index.jsonl")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="Path to _index.jsonl")
    parser.add_argument("--tag",   default=None,  help="Filter by strength tag (e.g. tech_strong)")
    parser.add_argument("--lang",  default=None,  help="Filter by primary language (e.g. rust)")
    parser.add_argument("--min-stars", type=int, default=0, help="Minimum stars")
    parser.add_argument("--sort",  default="date", choices=["date", "stars", "sloc"], help="Sort by")
    parser.add_argument("--json",  action="store_true", help="Output raw JSON lines")
    args = parser.parse_args()

    index_path = Path(args.index)
    if not index_path.exists():
        print(f"❌ index not found: {index_path}", file=sys.stderr)
        print("   尚未 harvest，或 2T 未掛載", file=sys.stderr)
        sys.exit(1)

    rows = []
    with open(index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    # filter
    if args.tag:
        rows = [r for r in rows if args.tag in r.get("strength_tags", [])]
    if args.lang:
        rows = [r for r in rows if r.get("language", "").lower() == args.lang.lower()]
    if args.min_stars:
        rows = [r for r in rows if r.get("stars", 0) >= args.min_stars]

    # sort
    sort_key = {
        "date":  lambda r: r.get("analyzed_at", ""),
        "stars": lambda r: r.get("stars", 0),
        "sloc":  lambda r: r.get("sloc", 0),
    }[args.sort]
    rows.sort(key=sort_key, reverse=True)

    if args.json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
        return

    # pretty print
    print(f"{'date':<12} {'repo':<38} {'stars':<7} {'lang':<6} {'tags'}")
    print("─" * 90)
    for r in rows:
        slug     = r.get("candidate_id", "?")
        tags     = " ".join(r.get("strength_tags", [])) or "(none)"
        lang     = r.get("language", "?")
        stars    = r.get("stars", 0)
        date_str = r.get("analyzed_at", "")[:10]
        print(f"{date_str:<12} {slug:<38} {stars:<7} {lang:<6} {tags}")

    print(f"\n{len(rows)} result(s)")


if __name__ == "__main__":
    main()
