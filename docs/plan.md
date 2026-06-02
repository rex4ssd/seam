# Seam — Project Plan & Goals

> **地質考查。找到好的地，再來挖。**
> Prospect the surface. Find the seam. Let Vein mine it.

---

## 1. What Is Seam

Seam is an **overnight intelligence prospector** for solo developers.

It searches external sources (GitHub, YouTube, HN, …), uses a local AI to score
candidates against your personal profile and current projects, then delivers a
curated short-list of "worth studying" picks each morning.

Seam does **not** store knowledge — that is Vein's job.
Seam finds what is worth storing.

```
External world          Seam                    Vein
─────────────   →   prospect + score    →   fetch + store + recall
GitHub / YT             (overnight)           (your lore archive)
```

**Family positioning:**

| Tool    | Analogy        | Role |
|---------|----------------|------|
| Lode    | Mining drill   | Finds code in your local repo |
| Vein    | The mine shaft | Stores and retrieves project lore |
| **Seam**| Geological survey | Finds external intelligence worth mining |

---

## 2. Vision

Every morning, before you open your laptop, Seam has already:

1. Searched GitHub for projects relevant to your work and skills
2. Browsed YouTube for channels publishing developer insight
3. Scored every candidate against your profile with local AI
4. Picked the top 3 for the day
5. Handed them to Vein for deep extraction

You wake up, run `vein morning`, and see:
```
── Today's Seam Picks (2026-06-02) ─────────────────────
 1. [github] simonw/files-to-prompt  ★ 94/100
    Why: Python CLI, solo-built, directly applicable to vein pipe
 2. [github] astral-sh/ruff          ★ 78/100
    Why: Rust extension pattern reusable in Lode
 3. [youtube] Fireship — "Tauri 2 deep dive"  ★ 71/100
    Why: covers wry/tao APIs you need for Lode web clipper
─────────────────────────────────────────────────────────
Run: seam fetch-today | vein study fetch daily-2026-06-02
```

---

## 3. Goals

### Phase 0 — GitHub only, CLI, offline-first (target: 2 weeks)

**Must have:**
- [ ] `seam search` — query GitHub Search API, return ranked repo list
- [ ] `seam score` — ollama scores each repo against profile, returns JSON
- [ ] `seam pick` — pick top-N from scored list (default 3)
- [ ] `seam run` — full pipeline (search → score → pick → print)
- [ ] `seam run --pipe` — output `owner/repo` lines, pipeable to `vein fetch`
- [ ] `.seam/profile.yaml` — user profile + criteria config
- [ ] `GITHUB_TOKEN` support (env var + config)
- [ ] Graceful degrade: no token → public API (60 req/hr, still workable)
- [ ] Graceful degrade: no ollama → score by star count + keyword match only

**Phase 0 success criteria:**
1. `seam run` runs unattended for 30+ min without crashing
2. Picks are consistently relevant to Lode / vein / Python tools
3. `seam run --pipe | xargs -I{} vein fetch {}` works end-to-end

### Phase 1 — YouTube source (target: +2 weeks after Phase 0)

- [ ] `seam search --source youtube --channel @fireship`
- [ ] YouTube Data API v3 integration
- [ ] Score video titles + descriptions against profile
- [ ] Pick top-N videos per channel per day

### Phase 2 — Multi-source aggregation (future)

- [ ] Hacker News (`/topstories`, `/newstories`)
- [ ] RSS feed reader (dev blogs, release notes)
- [ ] Unified scoring across sources (normalised 0-100)
- [ ] `seam watchlist` — persistent source list (mirrors vein's watchlist pattern)

---

## 4. Architecture

### Data flow

```
seam run
  ├── sources/github.py   → list[Candidate]
  ├── sources/youtube.py  → list[Candidate]   (Phase 1)
  ├── score/ollama.py     → list[ScoredCandidate]
  ├── score/heuristic.py  → fallback if no ollama
  ├── pick.py             → list[Pick] (top-N)
  └── output.py           → stdout / pipe / vein
```

### Key types

```python
@dataclass
class Candidate:
    source: str          # "github" | "youtube"
    id: str              # "owner/repo" or "yt:video_id"
    title: str
    description: str     # README snippet / video description
    stars: int           # or view count
    url: str
    metadata: dict       # raw API response

@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: int           # 0–100
    reason: str          # one-line explanation from AI
    dimensions: dict     # {lode_relevance: 82, solo_feasible: 71, ...}

@dataclass
class Pick:
    rank: int
    scored: ScoredCandidate
    date: str
```

### Scoring dimensions

Each candidate is scored on two weighted dimensions:

| Dimension | What it measures | Weight |
|-----------|-----------------|--------|
| `target_relevance` | How relevant to your current projects (Lode, Vein, …) | configurable |
| `solo_feasible` | Can a solo dev with your profile use/learn from this? | configurable |

Final score = weighted average. Both dimensions scored 0–100 by ollama.

---

## 5. Profile & Config (`.seam/profile.yaml`)

```yaml
version: 1

# Who you are — sent to ollama as scoring context
profile: |
  Solo developer, Python 5+ years, Rust beginner, React/TypeScript intermediate.
  Mac Studio M1 32GB. Main projects: Lode (Tauri 2 desktop app, Rust+React),
  Vein (Python CLI, decision lore archive). Available: ~4 weeks per side project.
  Goals: improve Lode features, find reusable patterns, learn Rust idioms.

# What you care about
interests:
  primary:   [tauri, macos-app, rust, file-diff, code-search, desktop-app]
  secondary: [python-cli, developer-tools, ai-tooling, sqlite]
  avoid:     [mobile, kubernetes, enterprise, java]

scoring:
  target_relevance_weight: 0.6
  solo_feasible_weight: 0.4
  min_score: 60        # don't include picks below this threshold

picks_per_day: 3

github:
  token: ""            # or set GITHUB_TOKEN env var
  min_stars: 200
  max_age_days: 90     # repo must have been pushed within N days
  queries:             # rotated daily to avoid repetition
    - "topic:tauri stars:>200"
    - "topic:macos-app language:rust stars:>100"
    - "topic:python-cli stars:>500"
    - "topic:developer-tools language:python stars:>300"
    - "topic:file-diff stars:>100"
    - "topic:ai-tooling language:python stars:>200"
    - "topic:sqlite language:python stars:>200"

model:
  base_url: http://localhost:11434
  score_model: deepseek-r1:14b
  fallback: heuristic   # "heuristic" | "skip"
```

---

## 6. CLI Design

```bash
# Full pipeline
seam run                           # search → score → pick → print
seam run --picks 5                 # override daily limit
seam run --source github           # one source only
seam run --pipe                    # output owner/repo lines only (for piping)

# Individual steps (composable)
seam search                        # run queries, print candidates JSON
seam search | seam score           # score stdin candidates
seam search | seam score | seam pick --top 3   # pick top 3

# Pipe to Vein
seam run --pipe | while read repo; do vein fetch "$repo"; done
seam run --pipe | xargs -I{} vein fetch {}

# Review picks
seam log                           # show past picks (last 7 days)
seam log --date 2026-05-31

# Config
seam init                          # create .seam/ in current dir
seam profile                       # show effective profile
```

---

## 7. Integration with Vein

Seam feeds Vein. Three integration levels:

**Level 1: pipe (Phase 0)**
```bash
# in crontab or night-harvest
seam run --pipe | while read repo; do vein fetch "$repo" --tag daily-pick; done
```

**Level 2: vein night-harvest hook (Phase 0.5)**
```yaml
# .vein/config.yaml
harvest:
  seam: true          # if seam is installed, run it as part of night-harvest
  seam_picks: 3
```

**Level 3: Vein MCP tool (Phase 1)**
Seam exposes an MCP tool `seam_picks_today` → Vein MCP can call it.

---

## 8. Pre-Loaded Experience (from Lode + Vein)

Seam 是第三個專案，前兩個踩過的雷不必再踩。完整版見 [`docs/pitfalls.md`](pitfalls.md) + [`docs/working_style.md`](working_style.md)。

### Architecture decisions carried forward

| 決策 | 來源 | 在 Seam 的實作 |
|------|------|----------------|
| Config `version:` 必填 | Vein I-004 | `profile.yaml` 頂層有 `version: 1` |
| ollama failure 顯式報錯 | Vein I-002 | scoring 失敗印 warning，不 silent fallback |
| model backend 抽象化 | Vein D-015 | `base_url` 從 config 讀，不 hardcode |
| 密鑰走 env var | Vein D-020 | `GITHUB_TOKEN` 只在 `.env`，不進 `profile.yaml` |
| Cache 不進 git | Vein I-001 | `.seam/cache/` 進 `.gitignore` |
| 不做 telemetry | Vein I-006 | 無 phone-home，無 auto-update check |
| 6 commands only（Phase 0）| Vein D-029 | init/search/score/pick/run/log |
| --dry-run + --yes on destructive | Lode SOP | `seam gc` 必有兩個 flag |
| picks 只 append，不修改 | Vein D-005 pattern | picks.jsonl + atomic fsync |

### Seam-specific risks pre-documented

| 風險 | Pitfall ID | 預防 |
|------|-----------|------|
| GitHub 429 rate limit | P-S01 | Retry-After header + 0.5s 節流 |
| picks.jsonl crash 半路損壞 | P-S02 | per-line atomic append + fsync |
| 多 query 重複 repo | P-S03 | dedup by (source, id) 在 scoring 前 |
| 每天推同一批 repo | P-S04 | 14 天 cooldown per repo |
| ollama 跑 8K README 超時 | P-S05 | 截斷到 1500 chars；timeout=120s |
| 夜間 cron 不知道炸了 | P-S06 | log 到 `~/.seam-harvest.log` |

---

## 9. Non-Goals

- **Not a feed reader** — Seam picks signal, not volume. 3/day max.
- **Not a recommendation engine** — no user history, no collaborative filtering.
- **Not a web scraper** — uses official APIs only (GitHub, YouTube Data).
- **Not a Vein replacement** — Seam discovers, Vein stores. Never merge.
- **Not always-on** — batch job, not a daemon. No server process.
- **Not cloud-dependent** — all scoring is local (ollama). API calls are the only network I/O.

---

## 9. Directory Structure

```
seam/
  CLAUDE.md                  ← project context for Claude sessions
  docs/
    plan.md                  ← this file
    decisions.md             ← D-001… decision log
  src/
    seam/
      cli.py                 ← Click entry point
      commands/
        run.py               ← full pipeline
        search.py            ← source queries
        score.py             ← ollama + heuristic scoring
        pick.py              ← top-N selection
        log_cmd.py           ← show past picks
        init.py              ← seam init
      sources/
        github.py            ← GitHub Search API adapter
        youtube.py           ← YouTube Data API (Phase 1)
      core/
        models.py            ← Candidate, ScoredCandidate, Pick
        config.py            ← profile + .seam/ loading
        store.py             ← picks log (JSON Lines in .seam/picks.jsonl)
  tests/
  pyproject.toml
  .seam/                     ← runtime data (gitignored index, picks log)
    profile.yaml
    picks.jsonl              ← append-only log of all picks
```

---

## 10. Decisions Log

`docs/decisions.md` — to be populated as we build.

Seed decisions already made:

**D-001** — Separate project, not inside Vein
Why: Different concern (discovery vs. storage). Vein's Path D positioning
would be diluted. Loose coupling via CLI pipe is sufficient.

**D-002** — CLI + pipe as primary integration, not SDK
Why: Unix philosophy. `seam run --pipe | vein fetch` is more composable
than embedding Vein as a Python dependency. Also survives Vein refactors.

**D-003** — Local AI for scoring, not cloud LLM
Why: Privacy (repo lists may be sensitive), no API cost, runs at 02:00
without supervision. Graceful fallback to heuristic if ollama is down.

**D-004** — Official APIs only (no scraping)
Why: GitHub trending scraping is fragile. GitHub Search API + YouTube
Data API are stable, documented, and free within rate limits.

**D-005** — Picks stored as JSON Lines, not SQLite
Why: append-only log, human-readable, no schema migration, trivially
greppable. SQLite is Vein's concern. Seam stays simple.
