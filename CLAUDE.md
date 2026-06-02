# Seam — Project Context for Claude

> **地質考查。找到好的地，再來挖。**
> Overnight intelligence prospector. Finds what's worth mining. Feeds Vein.

---

## 1. What Seam Does (one paragraph)

Seam runs overnight, searches external sources (GitHub Phase 0, YouTube Phase 1),
uses local ollama to score candidates against Rex's developer profile, picks the
top 3 per day, and outputs them for `vein fetch` to extract and store insights.
It is a thin, composable CLI tool — not a daemon, not a web service, not a feed reader.

Full plan: [`docs/plan.md`](docs/plan.md)

---

## 2. Family Context

| Tool  | Role |
|-------|------|
| Lode  | Desktop app — finds code in local repo |
| Vein  | CLI — stores project decision lore |
| **Seam** | CLI — prospects external intelligence for Vein |

Seam **discovers**. Vein **stores**. Never merge.

---

## 3. Phase 0 Scope (current)

Building in this order:
1. `seam init` — create `.seam/profile.yaml` scaffold
2. `seam search` — query GitHub Search API, return candidate JSON
3. `seam score` — ollama scores candidates against profile
4. `seam pick` — select top-N
5. `seam run` — full pipeline
6. `seam run --pipe` — pipe-friendly output for `vein fetch`

See [`docs/plan.md §3`](docs/plan.md) for success criteria.

---

## 4. Key Files (once built)

```
src/seam/cli.py              CLI entry point
src/seam/commands/run.py     full pipeline
src/seam/sources/github.py   GitHub Search API adapter
src/seam/core/models.py      Candidate / ScoredCandidate / Pick
src/seam/core/config.py      .seam/profile.yaml loader
src/seam/core/store.py       .seam/picks.jsonl log
.seam/profile.yaml           user profile + criteria
.seam/picks.jsonl            append-only pick history
```

---

## 5. Working Principles (for Claude)

**Same rules as Vein:**
- 繁體中文, 技術詞英文
- user 說 "ca" → `git add -A && git commit -F -`，不問
- terse > verbose，不寫 postamble
- 決策有 trade-off → 寫進 `docs/decisions.md`

**Seam-specific:**
- Keep it thin. If a feature belongs in Vein, it goes in Vein.
- Integration with Vein = CLI pipe. No Python import of vein internals.
- `.seam/picks.jsonl` is append-only. Never delete history, only `seam gc --before DATE`.
- Score model default: `deepseek-r1:14b` (reasoning needed for profile matching).
- Heuristic fallback must always work without ollama.
- `GITHUB_TOKEN` → env var only, never in profile.yaml.
- All destructive ops → `--dry-run` + `--yes` flags (Lode SOP).
- Phase 0: 6 commands only. No scope creep (Vein D-029 lesson).

**Key pre-loaded pitfalls (read `docs/pitfalls.md` before touching these areas):**
- GitHub API 429 → retry with `Retry-After` header (P-S01)
- picks.jsonl write → atomic append + fsync (P-S02)
- Duplicate candidates from multiple queries → dedup before scoring (P-S03)
- ollama timeout on long README → truncate to 1500 chars (P-S05)
- Cron job logging → always `>> ~/.seam-harvest.log 2>&1` (P-S06)

---

## 6. User Context

**R (Rex)** — same as Vein. Python 5+yr, Mac M1 32GB, US keyboard.
Lode author (Tauri 2 + Rust + React). Runs ollama locally.
Seam's profile.yaml describes Rex's current focus and solo-dev constraints.

---

## 7. Decisions Log

[`docs/decisions.md`](docs/decisions.md) — D-001 to D-005 seeded in plan.md.
