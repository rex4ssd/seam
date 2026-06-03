# Seam — Phase 3 Harvest 程式架構（需求規格）

> 給實作者（人或 cowork）照著建。先讀 [`harvest_plan.md`](harvest_plan.md) 知道 why，本檔講 how。
> **不需要 cron。** orchestration 是一支「一次性」python entry，由 **cowork schedule** 觸發；
> 可靠性（catch-up / self-check / 冪等）參考 `/Users/lion/Documents/py/schedule_entry.py` 的做法，
> 但 **不抄整個常駐 scheduler loop**（觸發交給 cowork schedule）。

---

## 1. Vein 實況（讀 code 後校正，取代舊假設）

| 面向 | Vein 真實行為 |
|---|---|
| ingest 單位 | **`owner/repo` slug 或 GitHub URL**（`vein fetch` 的 `_normalise_github`）。**不吃本地路徑。** |
| fetch 做什麼 | clone `--depth 1` 到 **temp** → 只讀 markdown（README / ARCHITECTURE / DESIGN / docs/**.md）→ ollama 抽 `reference/decision/lore/pitfall` 進 `.vein/` → **刪掉 clone**。 |
| 不做什麼 | 不留 repo、**不看 code / tests / CI**、不做語言分類、不打分數。 |
| 批次 | `vein study fetch <collection> repo...` → tag `study:<name>`；`vein study compare` 做跨 repo AI 比較。 |
| 自動化 | `.vein/watchlist.yaml`（collection→repos）+ `vein night-harvest`（watchlist→debrief→morning brief）。**watchlist 是手打的，無 discovery。** |
| tag 慣例 | `source:github/<slug>`、`study:<name>`、`project:xxx`、任意 `--tag`（可重複）。 |
| Entry schema | `Entry.make(type, title, body, tags, source, source_url, source_title, volatility)`；type ∈ decision/lore/pitfall/reference；存 YAML frontmatter + md。 |

**結論：Vein 已經會 clone+抽 narrative，缺的是「要 fetch 哪些 repo」與「程式碼層級的強項分類」。那兩件正是 Seam 的事。**

---

## 2. 分工（Seam vs Vein）— 不重疊

| 能力 | 誰做 | 說明 |
|---|---|---|
| discovery（search→score→pick） | **Seam** | Vein 完全沒有。把整個 GitHub 收斂成精選 slug。 |
| 永久 code archive 到 2T + 語言分類 | **Seam** | Vein 用完即刪 clone。Seam 留檔給人反覆研讀。 |
| 程式碼層強項評分（5 維 + tags） | **Seam** | 掃 tree（tests/CI/linter/manifest），Vein 只讀 markdown 做不到。 |
| narrative 抽取（reference/decision/lore/pitfall） | **Vein** | `vein fetch` 既有能力，不重做。 |
| 跨 repo 比較、吸收強項、**規劃新產品** | **Vein** | `study compare` / debrief / morning brief 的合成職責。Seam 不做 ideation（守 thin）。 |

> 修正舊規劃：`vein_seeds`（未來產品點子）**從 Seam 移除**，那是 Vein 的合成職責。Seam 只餵「對的 repo + 分類」。

---

## 3. Seam → Vein 介面契約（核心）

**最小契約（Phase 3，Vein 不需改任何 code）：** Seam 輸出 **slug + 分類 tags**，Vein 用既有 `vein fetch` 吃。

### 3.1 Tag namespace（Seam 產、Vein 存）
```
lang:rust            # 主語言
strength:tech_strong # 過門檻的強項 tag（可多個）
strength:great_validation
seam-score:88        # pick 階段總分
seam-pick:2026-06-02 # 哪一晚被選中
```

### 3.2 三種 handoff（擇一或併用）

**A. slug pipe（最 thin，推薦預設）**
```bash
seam harvest --pipe-vein            # 每行輸出：owner/repo + 該 repo 的 --tag 參數
# 範例輸出一行：astral-sh/ruff --tag lang:rust --tag strength:tech_strong --tag seam-score:88
seam harvest --pipe-vein | while read line; do vein fetch $line; done
```

**B. 直接灌 Vein watchlist（重用 night-harvest）**
- Seam 寫一個當日 collection 進 `.vein/watchlist.yaml`：`seam-YYYY-MM-DD: { repos: [...], compare: true }`
- 之後 `vein night-harvest` 自動 fetch+compare。Seam 不 import vein，只寫它的 yaml（檔案契約，非 code 契約）。

**C. 強項以 tag 隨行（A/B 都加上）** — 讓 `vein recall strength:tech_strong lang:rust` 可查。

### 3.3 雙重 clone 問題（必須讓 Rex 知道的 trade-off）
Seam clone 到 2T（永久），Vein `fetch` 又 clone 到 temp → **同一 repo 下載兩次**。兩邊都 depth-1，浪費不大但真實。

- **Phase 3 採法：** 接受雙 clone（保持零耦合，Vein 不改）。
- **建議的 Vein 端 enhancement（選配，記成 Vein 決策、非 Seam 阻塞）：** 給 `vein fetch` 加 `--from-path <dir>`，直接從 Seam 在 2T 的既有 clone 抽 markdown，免重複下載。**這要動 Vein，不在 Phase 3 範圍**，由 Rex 決定要不要做。

---

## 4. 模組樹（src/seam/harvest/）

```
src/seam/harvest/
  __init__.py
  cloner.py        # shallow clone 到 2T + 語言分類 + .seam-meta.json
  signals.py       # 純靜態掃描（tests/CI/linter/manifest/SLOC），零 network
  analyzer.py      # signals + 取樣檔 → ollama → StrengthReport；heuristic fallback
  report.py        # 寫 STRENGTH.md + 追加 _index.jsonl + 產 vein handoff
  veinout.py       # 產 slug+tags（pipe）或寫 .vein/watchlist.yaml（檔案契約）
src/seam/commands/
  harvest.py       # `seam harvest` CLI（--dry-run/--pipe-vein/--repo/--from picks）
src/seam/runtime/
  history.py       # harvest_history.csv 讀寫 + 冪等判斷（參考 schedule_entry）
  lock.py          # .seam/harvest.lock（pid），防 cowork schedule 重觸
seam_harvest_entry.py   # 一次性 orchestration entry（cowork schedule 觸發）
```

`core/models.py` 追加 `StrengthReport`（見 harvest_plan §3）。

---

## 5. 資料契約

### 5.1 `harvest_history.csv`（参 schedule_history.csv，Seam 本地、append-only）
```
timestamp,repo,commit,stage,result,scheduled_for
2026-06-02 02:14:03,astral-sh/ruff,a1b2c3d,clone,running,2026-06-02 02:00
2026-06-02 02:14:09,astral-sh/ruff,a1b2c3d,clone,pass,2026-06-02 02:00
2026-06-02 02:15:55,astral-sh/ruff,a1b2c3d,analyze,pass,2026-06-02 02:00
```
- `stage` ∈ clone / signals / analyze / report / veinout
- `result` ∈ running / pass / fail / skipped / killed
- 冪等鍵 = `(repo, commit, stage)`。寫 `running` 後立即 fsync（承 P-S02）。

### 5.2 `_index.jsonl`（2T 上，每 repo 一行；見 harvest_plan §2）
冪等鍵 `(repo, commit)`：commit 沒變就跳過整個 repo。

---

## 6. 關鍵函式簽名（給實作者）

```python
# harvest/cloner.py
def clone_repo(slug: str, target_root: Path, cfg: HarvestCfg) -> CloneResult:
    """git clone --depth 1 --single-branch；GIT_LFS_SKIP_SMUDGE=1；
    clone 前 free-space + size_cap 檢查（P-H01）；timeout（P-H02）；
    已存在且 commit 同 → skip；不同 → fetch+reset（P-H03）。
    依語言歸檔到 target_root/<lang>/<owner>__<repo>/。回傳含 commit/path/skipped_reason。"""

# harvest/signals.py
def collect_signals(repo_dir: Path) -> RepoSignals:
    """純檔案掃描：has_tests, ci_files, linter_cfgs, license, changelog,
    semver_tags(從 .seam-meta 或 GH API), dep_manifest, lang_breakdown, sloc,
    readme_len。零 network、零執行（P-H07）。"""

# harvest/analyzer.py
def analyze(signals: RepoSignals, repo_dir: Path, cand: Candidate,
            cfg: HarvestCfg) -> StrengthReport:
    """組 prompt(signals + 取樣≤sample_files 檔 + 截斷 README) → ollama →
    5 維分數 + 過 tag_threshold 的 strength tags + summary + evidence。
    ollama 不可用 → heuristic_score(signals)（純 signal，承 I-003、P-V01）。"""

def heuristic_score(signals: RepoSignals) -> StrengthReport: ...

# harvest/report.py
def write_report(rep: StrengthReport, repo_dir: Path, index_path: Path) -> Path:
    """寫 repo_dir/STRENGTH.md；atomic append _index.jsonl（去重 by (repo,commit)）。"""

# harvest/veinout.py
def to_vein_lines(reps: list[StrengthReport]) -> list[str]:
    """A 法：每行 'owner/repo --tag lang:x --tag strength:y --tag seam-score:n'。"""
def write_vein_watchlist(reps: list[StrengthReport], vein_dir: Path, day: str) -> None:
    """B 法：寫 collection seam-<day> 進 .vein/watchlist.yaml（純檔案，不 import vein）。"""

# runtime/history.py（參考 schedule_entry.py）
def load_done_keys(hist: Path) -> tuple[set, set]:
    """回傳 (finalized, in_progress)。finalized=(repo,commit,stage) 有 pass/fail/killed；
    in_progress=只有 running 沒終局（上次中斷）。"""
def mark(hist: Path, repo: str, commit: str, stage: str, result: str, sf: str) -> None:
    """append + flush + fsync。"""
def should_skip(repo: str, commit: str, stage: str, finalized: set, in_progress: set) -> bool:
    """finalized→skip；in_progress 且 stage 是非冪等(analyze/veinout)→保守 skip（避免重燒 ollama）。"""
```

---

## 7. Orchestration：一次性 entry（cowork schedule 觸發，無 cron / 無常駐 loop）

`seam_harvest_entry.py` — 跑一輪就結束，回傳 exit code。cowork schedule 負責每晚叫它。
可靠性語意照搬 `schedule_entry.py` 的 **catch-up + self-check + in-progress 保守跳過**，但去掉 `while True: schedule.run_pending()` 那層。

```python
#!/usr/bin/env python3
"""seam_harvest_entry.py — 一次性 harvest 編排，給 cowork schedule 觸發。
用法：
  python seam_harvest_entry.py                # 正常跑一輪（含 catch-up）
  python seam_harvest_entry.py --self-check   # 只重試前 24h fail（参 schedule_entry）
  python seam_harvest_entry.py --dry-run
"""
import sys
from seam.runtime.lock import single_instance        # .seam/harvest.lock，防重觸（P-H06）
from seam.runtime.history import (load_done_keys, mark, should_skip, retry_failures_24h)
from seam.commands.run import run_pick                # 既有 search→score→pick
from seam.harvest import cloner, signals, analyzer, report, veinout

def main(argv) -> int:
    if "--self-check" in argv:                        # 参 run_self_check()
        return retry_failures_24h(stages=(clone_one,))  # 逐筆重試、寫回 history
    with single_instance():                           # stale lock(pid 不存在)自動清
        finalized, in_progress = load_done_keys(HIST)
        sf = now_slot()                               # "YYYY-MM-DD HH:MM" 當作 scheduled_for
        picks = run_pick(cfg)                          # 少量精選（max_repos_per_night）
        reports = []
        for p in picks:
            slug = p.scored.candidate.id
            try:
                cr = cloner.clone_repo(slug, cfg.target_dir, cfg)   # mark clone running→pass
                if should_skip(slug, cr.commit, "analyze", finalized, in_progress):
                    continue                            # 上次跑過/中斷未完 → 冪等跳過
                sig = signals.collect_signals(cr.path)
                rep = analyzer.analyze(sig, cr.path, p.scored.candidate, cfg)
                report.write_report(rep, cr.path, cfg.index_path)
                reports.append(rep)
            except Exception as e:
                mark(HIST, slug, "?", "clone", "fail", sf); log(e); continue  # 不中斷整批（P-H02）
        veinout.write_vein_watchlist(reports, cfg.vein_dir, today())   # B 法
        # 或印 veinout.to_vein_lines(reports) 給 schedule 的下一步 pipe（A 法）
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

### cowork schedule 怎麼接（兩步鏈）
1. **每晚一次：** `python seam_harvest_entry.py`（clone+化驗+寫 watchlist / 或輸出 slug）。
2. **緊接著：** `vein night-harvest`（吃 watchlist 抽 narrative + morning brief）。
   或 A 法：`python seam_harvest_entry.py --pipe-vein | while read l; do vein fetch $l; done`
3. **每日 00:00（選配）：** `python seam_harvest_entry.py --self-check` 重試昨夜 fail。

> cowork schedule = 觸發器，取代 cron。Seam 這支 entry 只管「跑一輪 + 冪等 + 自我重試」，不自己排程。

---

## 8. 驗證計畫（python validation，参 schedule_entry.py）

| 測試 | 對應 schedule_entry 概念 | 斷言 |
|---|---|---|
| 冪等：重跑同晚 | catch-up `finalized` | 同 `(repo,commit)` 不重 clone、`_index.jsonl` 不重複行 |
| 中斷續跑 | `in_progress` 保守跳過 | clone 完 analyze 前殺掉 → 重跑跳過 analyze、不重燒 ollama |
| self-check 重試 | `run_self_check()` | 注入一筆 24h 內 fail → `--self-check` 重試一次、寫回 `retry:` 前綴 |
| 失效過濾 | `_load_scheduled_commands()` | 已不在 picks 的舊 fail 不重試 |
| lock 防重觸 | （cowork schedule 可能雙觸） | 第二個 instance 偵測到 live pid 直接退出 |
| 磁碟/timeout 守門 | — | mock 空間不足→skip 不 crash；clone timeout→記錄續跑 |
| handoff 契約 | — | `to_vein_lines` 格式可被 `vein fetch` 解析；watchlist yaml 結構符合 vein `_load_watchlist` |

`history.py` 的 catch-up/self-check 是 Seam 本地最小重寫（zero deps），**不 import** `/Users/lion/Documents/py/task_runner.py`（跨專案耦合）— 只借語意。

---

## 9. 對 Phase 計畫的影響（覆寫 harvest_plan §7 的 3.5）

- Phase 3.4 的 Vein pipe：改成 §3 的 slug+tags 契約（A）或 watchlist（B），**移除 vein_seeds**。
- Phase 3.5「Overnight orchestration」：**cron → `seam_harvest_entry.py` + cowork schedule**；validation 改用 §8（参 schedule_entry）。
- pitfalls P-H06：cron log → 改為 entry 自身 logging + lock + history（見 §7）。
