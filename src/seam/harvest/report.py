"""
harvest/report.py — write STRENGTH.md + append to _index.jsonl.

Public API:
  write_report(rep, repo_dir, index_path) -> Path
    Writes STRENGTH.md into repo_dir.
    Atomically appends one line to index_path, skipped if (candidate_id, commit)
    already exists (idempotent — I-004 / P-S02 pattern).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..core.models import StrengthReport
from .layout import strength_report_path

# ── dimension display order ────────────────────────────────────────────────────

_DIMS = [
    ("tech_strength",  "tech_strong"),
    ("coding_style",   "style_strong"),
    ("stability",      "product_stable"),
    ("validation",     "great_validation"),
    ("onboarding",     "easy_onboarding"),
]


# ── public API ─────────────────────────────────────────────────────────────────

def write_report(rep: StrengthReport, repo_dir: Path, index_path: Path) -> Path:
    """
    Write STRENGTH.md into repo_dir.
    Atomic-append one line to index_path; skip if (candidate_id, commit) already there.
    Returns the path to STRENGTH.md.
    """
    md_path = strength_report_path(repo_dir)
    md_path.write_text(_render_markdown(rep), encoding="utf-8")

    _append_index(rep, index_path)

    return md_path


# ── markdown renderer ──────────────────────────────────────────────────────────

def _render_markdown(rep: StrengthReport) -> str:
    lines: list[str] = []

    # ── header ────────────────────────────────────────────────────────────
    lines.append(f"# STRENGTH REPORT: {rep.candidate_id}")
    lines.append("")
    lines.append(
        f"> Analyzed: {rep.analyzed_at}  "
        f"| Engine: {rep.engine}  "
        f"| Profile: `{rep.profile_version}`"
    )
    lines.append("")

    # ── summary ───────────────────────────────────────────────────────────
    lines.append("## Summary")
    lines.append("")
    lines.append(rep.summary or "_(no summary)_")
    lines.append("")

    # ── scores table ──────────────────────────────────────────────────────
    lines.append("## Scores")
    lines.append("")
    lines.append("| Dimension | Score | Tag |")
    lines.append("|---|---|---|")
    for dim, tag in _DIMS:
        score = rep.dimensions.get(dim, 0)
        tag_cell = f"✓ `{tag}`" if tag in rep.strength_tags else "—"
        lines.append(f"| {dim} | {score} | {tag_cell} |")
    lines.append("")

    if rep.strength_tags:
        tag_badges = " ".join(f"`{t}`" for t in rep.strength_tags)
        lines.append(f"**Tags:** {tag_badges}")
    else:
        lines.append("**Tags:** _(none above threshold)_")
    lines.append("")

    # ── evidence ──────────────────────────────────────────────────────────
    lines.append("## Evidence")
    lines.append("")
    for _dim, tag in _DIMS:
        items = rep.evidence.get(tag, [])
        if not items:
            continue
        lines.append(f"### {tag}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    # ── metadata ──────────────────────────────────────────────────────────
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- **Repo:** {rep.candidate_id}")
    lines.append(f"- **Stars:** {rep.stars:,}")
    lines.append(f"- **Primary language:** {rep.language}")
    if rep.languages:
        lang_str = ", ".join(
            f"{ext}:{pct}%" for ext, pct in list(rep.languages.items())[:5]
        )
        lines.append(f"- **Language breakdown:** {lang_str}")
    lines.append(f"- **SLOC (est.):** {rep.sloc:,}")
    lines.append(f"- **Clone path:** `{rep.clone_path}`")
    lines.append(f"- **Commit:** `{rep.commit}`")
    lines.append("")

    return "\n".join(lines)


# ── _index.jsonl append ────────────────────────────────────────────────────────

def _append_index(rep: StrengthReport, index_path: Path) -> None:
    """
    Append one JSON line to index_path.
    Idempotency key = (candidate_id, commit): skip if already present. (I-004)
    Uses per-line fsync to survive crash mid-batch. (P-S02 pattern)
    """
    index_path.parent.mkdir(parents=True, exist_ok=True)
    key = (rep.candidate_id, rep.commit)

    # idempotency check
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if (row.get("candidate_id", ""), row.get("commit", "")) == key:
                    return  # already indexed — skip

    entry = {
        "candidate_id": rep.candidate_id,
        "commit":       rep.commit,
        "language":     rep.language,
        "stars":        rep.stars,
        "sloc":         rep.sloc,
        "strength_tags": rep.strength_tags,
        "dimensions":   rep.dimensions,
        "analyzed_at":  rep.analyzed_at,
        "clone_path":   rep.clone_path,
        "profile_version": rep.profile_version,
        "engine":       rep.engine,
    }
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
