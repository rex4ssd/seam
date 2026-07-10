#!/usr/bin/env python3
"""
seam_harvest_entry.py — one-shot harvest orchestration.

Triggered by cowork schedule (not cron, not always-on daemon).
Runs one full cycle: picks → clone → signals → analyze → report → vein handoff.

Usage:
  python seam_harvest_entry.py               # normal run
  python seam_harvest_entry.py --self-check  # retry last 24h failures
  python seam_harvest_entry.py --dry-run     # show plan, no disk writes

Override settings (all optional, profile.yaml values used as default):
  --picks N          override picks_per_day
  --max-repos N      override harvest.max_repos_per_night
  --model NAME       override model.score_model  (e.g. llama3.2)
  --size-cap MB      override harvest.clone.size_cap_mb

Design: harvest_architecture.md §7
Reliability pattern: runtime/history.py (catch-up + in-progress skip + self-check)
                     runtime/lock.py    (prevent double-trigger from cowork schedule)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# ── logging: file (~/.seam-harvest.log) + stderr ──────────────────────────────
_LOG_PATH = Path.home() / ".seam-harvest.log"
_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(_LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger("seam.harvest")

from seam.core.config import load_config, find_profile
from seam.core.store import harvest_index_path, load_picks
from seam.commands.run import run_pipeline
from seam.harvest import cloner, analyzer, report, veinout
from seam.harvest import signals as sig_mod
from seam.harvest.layout import assert_target_dir
from seam.runtime.lock import single_instance, LockError
from seam.runtime.history import (
    default_history_path,
    load_done_keys,
    mark,
    now_slot,
    retry_failures_24h,
    should_skip,
)

_HIST = default_history_path()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="seam_harvest_entry", add_help=False)
    p.add_argument("--dry-run",    action="store_true")
    p.add_argument("--self-check", action="store_true")
    p.add_argument("--from-picks", action="store_true")
    p.add_argument("--yes",        action="store_true",
                   help="auto-confirm overwrite of updated repos (no prompt)")
    # setting overrides
    p.add_argument("--picks",     type=int,   default=None, metavar="N",
                   help="override picks_per_day (default: profile.yaml value)")
    p.add_argument("--max-repos", type=int,   default=None, metavar="N",
                   help="override harvest.max_repos_per_night")
    p.add_argument("--model",     type=str,   default=None, metavar="NAME",
                   help="override model.score_model (e.g. llama3.2)")
    p.add_argument("--size-cap",  type=int,   default=None, metavar="MB",
                   help="override harvest.clone.size_cap_mb")
    return p.parse_args(argv)


def _apply_overrides(cfg, args: argparse.Namespace) -> None:
    """Patch cfg._data in-place with any CLI overrides."""
    if args.picks is not None:
        cfg._data["picks_per_day"] = args.picks
        log.info("override picks_per_day=%d", args.picks)
    if args.max_repos is not None:
        cfg._data.setdefault("harvest", {})["max_repos_per_night"] = args.max_repos
        log.info("override max_repos_per_night=%d", args.max_repos)
    if args.model is not None:
        cfg._data.setdefault("model", {})["score_model"] = args.model
        log.info("override score_model=%s", args.model)
    if args.size_cap is not None:
        cfg._data.setdefault("harvest", {}).setdefault("clone", {})["size_cap_mb"] = args.size_cap
        log.info("override size_cap_mb=%d", args.size_cap)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    args       = _parse_args(argv)
    dry_run    = args.dry_run
    self_check = args.self_check
    from_picks = args.from_picks
    auto_update = args.yes   # --yes → auto-confirm overwrite; else prompt

    cfg = load_config(find_profile())
    _apply_overrides(cfg, args)
    harvest_cfg = cfg.harvest

    # ── validate target_dir ───────────────────────────────────────────────
    try:
        target_dir = cfg.harvest_target_dir()
        if not dry_run:
            assert_target_dir(target_dir)
    except ValueError as exc:
        log.error("target_dir error: %s", exc)
        return 1

    index_path = harvest_index_path(target_dir)

    # ── clean up stale temp dirs from previous crashed runs ───────────────
    if not dry_run:
        removed = cloner.cleanup_stale_temps(target_dir)
        if removed:
            log.info("cleaned up %d stale temp dir(s)", removed)

    # ── --self-check path ─────────────────────────────────────────────────
    if self_check:
        return _run_self_check(cfg, target_dir, index_path, dry_run)

    # ── normal run (single-instance lock) ─────────────────────────────────
    try:
        with single_instance():
            return _run_once(cfg, harvest_cfg, target_dir, index_path, dry_run,
                             from_picks=from_picks, auto_update=auto_update)
    except LockError as exc:
        log.warning("lock held — skipping: %s", exc)
        return 0   # not an error; another harvest is active


def _run_once(cfg, harvest_cfg, target_dir: Path, index_path: Path,
              dry_run: bool, from_picks: bool = False,
              auto_update: bool = True) -> int:
    """One full harvest cycle."""
    finalized, in_progress = load_done_keys(_HIST)
    sf = now_slot()
    today_str = date.today().isoformat()

    log.info("harvest start  slot=%s  dry_run=%s", sf, dry_run)

    # ── get picks ─────────────────────────────────────────────────────────
    if from_picks:
        # Read today's picks from picks.jsonl — skip GitHub search + scoring
        log.info("reading picks from picks.jsonl (--from-picks)")
        picks = _picks_from_log(cfg)
    else:
        log.info("running search + score pipeline (takes 2-5 min) ...")
        try:
            picks = run_pipeline(cfg, save=not dry_run)
        except Exception as exc:
            log.error("pick pipeline failed: %s", exc)
            return 1

    if not picks:
        log.info("no picks today")
        return 0

    if dry_run:
        log.info("dry-run: would harvest %d repo(s):", len(picks))
        for p in picks:
            log.info("  %s (score=%d)", p.scored.candidate.id, p.scored.score)
        return 0

    reports = []

    for pick in picks:
        cand = pick.scored.candidate
        slug  = cand.id

        # ── clone ──────────────────────────────────────────────────────
        # Mark after we have the commit so history key = (slug, sha, stage).
        try:
            cr = cloner.clone_repo(
                slug, target_dir, harvest_cfg,
                language=cand.language, stars=cand.stars,
                auto_update=auto_update,
            )
            commit = cr.commit or "unknown"
        except Exception as exc:
            mark(_HIST, slug, "failed", "clone", "fail", sf)
            log.error("clone failed %s: %s", slug, exc)
            continue

        if cr.skipped and cr.skipped_reason not in ("exists_same_commit", "dry_run"):
            mark(_HIST, slug, commit, "clone", "skipped", sf)
            log.info("clone skipped %s: %s", slug, cr.skipped_reason)
            continue

        mark(_HIST, slug, commit, "clone", "pass", sf)
        clone_path = Path(cr.clone_path) if cr.clone_path else None
        if not clone_path or not clone_path.exists():
            log.warning("%s: clone_path missing after clone", slug)
            continue

        # ── signals (always safe to re-run) ────────────────────────────
        mark(_HIST, slug, commit, "signals", "running", sf)
        try:
            repo_signals = sig_mod.collect_signals(clone_path)
            mark(_HIST, slug, commit, "signals", "pass", sf)
        except Exception as exc:
            mark(_HIST, slug, commit, "signals", "fail", sf)
            log.error("signals failed %s: %s", slug, exc)
            continue

        # ── analyze (non-idempotent — skip if previous run in-progress) ─
        if should_skip(slug, commit, "analyze", finalized, in_progress):
            log.info("%s: analyze skipped (already done or interrupted)", slug)
            continue

        mark(_HIST, slug, commit, "analyze", "running", sf)
        try:
            rep = analyzer.analyze(repo_signals, clone_path, cand, cfg)
            mark(_HIST, slug, commit, "analyze", "pass", sf)
        except Exception as exc:
            mark(_HIST, slug, commit, "analyze", "fail", sf)
            log.error("analyze failed %s: %s", slug, exc)
            continue

        # ── report ─────────────────────────────────────────────────────
        mark(_HIST, slug, commit, "report", "running", sf)
        try:
            report.write_report(rep, clone_path, index_path)
            # CSV log — one row per repo
            csv_path = target_dir / "harvest_log.csv"
            report.append_csv_log(rep, csv_path)
            mark(_HIST, slug, commit, "report", "pass", sf)
            reports.append(rep)
            tag_str = ", ".join(rep.strength_tags) or "none"
            log.info("%s ★%d  tags: %s", slug, rep.stars, tag_str)
        except Exception as exc:
            mark(_HIST, slug, commit, "report", "fail", sf)
            log.error("report failed %s: %s", slug, exc)

    # ── vein handoff ──────────────────────────────────────────────────────
    if reports:
        mark(_HIST, "batch", today_str, "veinout", "running", sf)
        try:
            vein_dir = Path.home() / ".vein"
            veinout.write_vein_watchlist(reports, vein_dir, today_str)
            mark(_HIST, "batch", today_str, "veinout", "pass", sf)
            log.info(
                "vein watchlist: seam-%s (%d repos)  → %s",
                today_str, len(reports), vein_dir / "watchlist.yaml",
            )
        except Exception as exc:
            mark(_HIST, "batch", today_str, "veinout", "fail", sf)
            log.error("veinout failed: %s", exc)

    log.info(
        "harvest done  analyzed=%d/%d",
        len(reports), len(picks),
    )
    return 0


def _picks_from_log(cfg) -> list:
    """Read today's (or recent) picks from picks.jsonl, reconstruct Pick objects."""
    from seam.core.models import Candidate, ScoredCandidate, Pick
    rows = load_picks(days=1)   # today only
    if not rows:
        rows = load_picks(days=3)  # fallback: last 3 days

    picks = []
    for row in rows[:cfg.picks_per_day]:
        cand = Candidate(
            source=row.get("source", "github"),
            id=row["id"],
            title=row.get("title", row["id"]),
            description="",
            stars=row.get("stars", 0),
            url=row.get("url", f"https://github.com/{row['id']}"),
            pushed_at="",
            language=row.get("language", ""),
            topics=[],
        )
        scored = ScoredCandidate(
            candidate=cand,
            score=row.get("score", 0),
            reason=row.get("reason", ""),
            dimensions=row.get("dimensions", {}),
        )
        picks.append(Pick(rank=row.get("rank", 1), scored=scored, date=row.get("date", "")))

    log.info("loaded %d pick(s) from picks.jsonl", len(picks))
    return picks


def _run_self_check(cfg, target_dir: Path, index_path: Path, dry_run: bool) -> int:
    """Retry failed stages from the last 24 hours."""
    log.info("self-check: scanning last 24h failures")
    sf = now_slot()

    recent_picks = load_picks(days=7)
    active_repos = {p["id"] for p in recent_picks}

    retry_list = retry_failures_24h(_HIST, active_repos)
    if not retry_list:
        log.info("self-check: nothing to retry")
        return 0

    log.info("self-check: %d item(s) to retry", len(retry_list))

    # Group by repo so we run the full pipeline once per repo
    retry_repos = {repo for repo, _commit, _stage in retry_list}

    for slug in retry_repos:
        log.info("  retry: %s", slug)
        if dry_run:
            continue

        # Re-run pick for this repo (skip search — use existing picks)
        pick = next(
            (p for p in recent_picks if p.get("id") == slug),
            None,
        )
        if not pick:
            log.warning("  %s: no pick record found — skipping", slug)
            continue

        from seam.core.models import Candidate, ScoredCandidate, Pick
        cand = Candidate(
            source=pick.get("source", "github"),
            id=slug,
            title=pick.get("title", slug),
            description="",
            stars=pick.get("stars", 0),
            url=pick.get("url", f"https://github.com/{slug}"),
            pushed_at="",
            language=pick.get("language", ""),
            topics=[],
        )

        # Run just the failed stages for this repo
        for _repo, commit, stage in retry_list:
            if _repo != slug:
                continue
            retry_commit = f"retry:{commit}"
            mark(_HIST, slug, retry_commit, stage, "running", sf)
            try:
                _retry_stage(stage, slug, commit, cand, cfg, target_dir, index_path)
                mark(_HIST, slug, retry_commit, stage, "pass", sf)
                log.info("  %s %s: retry passed", slug, stage)
            except Exception as exc:
                mark(_HIST, slug, retry_commit, stage, "fail", sf)
                log.error("  %s %s: retry failed: %s", slug, stage, exc)

    return 0


def _retry_stage(
    stage: str,
    slug: str,
    commit: str,
    cand,
    cfg,
    target_dir: Path,
    index_path: Path,
) -> None:
    """Attempt to re-run a single failed stage."""
    harvest_cfg = cfg.harvest

    if stage == "clone":
        cr = cloner.clone_repo(slug, target_dir, harvest_cfg,
                               language=cand.language, stars=cand.stars)
        if cr.skipped and cr.skipped_reason not in ("exists_same_commit", "dry_run"):
            raise RuntimeError(f"clone skipped: {cr.skipped_reason}")
        return

    # For post-clone stages, locate the existing clone
    lang_folder = harvest_cfg.map_language(cand.language) if cand.language else "_unknown"
    from seam.harvest.layout import repo_dir
    clone_path = repo_dir(target_dir, lang_folder, slug)
    if not clone_path.exists():
        raise RuntimeError(f"clone_path not found: {clone_path}")

    if stage == "signals":
        sig_mod.collect_signals(clone_path)
        return

    if stage == "analyze":
        repo_signals = sig_mod.collect_signals(clone_path)
        rep = analyzer.analyze(repo_signals, clone_path, cand, cfg)
        report.write_report(rep, clone_path, index_path)
        return

    if stage == "report":
        repo_signals = sig_mod.collect_signals(clone_path)
        rep = analyzer.analyze(repo_signals, clone_path, cand, cfg)
        report.write_report(rep, clone_path, index_path)
        return

    raise ValueError(f"unknown stage: {stage}")


if __name__ == "__main__":
    sys.exit(main())
