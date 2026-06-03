# Seam — Phase 3 Harvest 計畫

> **找到好的地之後，把礦石搬回來、化驗、寫成報告，交給 Vein 吸收。**
> harvest = clone 高分 repo → 靜態化驗 strengths → 出報告 → pipe 給 Vein。

Phase 0–2 見 [`plan.md`](plan.md)。本文件談 **Phase 3：harvest** 的 why 與 phase 切分。
**程式架構（how）+ Seam↔Vein 介面契約 + cowork schedule 編排** 見 [`harvest_architecture.md`](harvest_architecture.md)。

> ⚠️ 讀過 Vein code 後的校正（2026-06-02）：Vein 的 `vein fetch` **本來就會 clone depth-1 + ollama 抽 narrative**，
> 還有 `study` / `watchlist` / `night-harvest`。Vein 缺的是 **discovery** 與 **程式碼層強項分類**。
> 所以 Seam 的 harvest = discovery + 永久 2T archive + 強項評分；narrative 抽取與「規劃新產品」留給 Vein。
> 細節與分工表見 architecture §1–§3。**`vein_seeds` 已從 Seam 移除**（那是 Vein 的合成職責）。

---

## 0. 決策前提（已拍板）

| 項目 | 決定 | Decision |
|------|------|----------|
| 定位 | Seam 新增 Phase 3，沿用 search→score→pick | D-006 |
| Clone 策略 | `git clone --depth 1`（shallow snapshot，無 history） | D-007 |
| 量 / 保留 | 每晚 3–5 精選，clone 永久保留，靠 `seam harvest gc` 手動清 | D-008 |
| 化驗方式 | 靜態 signal + ollama 判讀，heuristic fallback | D-009 |
| Vein 整合 | CLI pipe（延續 D-002），輸出 STRENGTH 報告路徑 + jsonl | D-010 |

詳細 trade-off 見 [`decisions.md`](decisions.md) D-006~D-010。

---

## 1. harvest 在 pipeline 的位置

```
seam run                         （既有）
  search → score → pick  ─────────────────┐  top-N Pick
                                           ↓
seam harvest            （新增 Phase 3）
  1. take Picks（或 harvest 專用 query set）
  2. cloner    →  git clone --depth 1 到 2T
  3. signals   →  靜態掃描 repo（tests/CI/linter/license…，零 network）
  4. analyzer  →  ollama 判讀 → StrengthReport（tags + 5 維分數 + evidence）
  5. report    →  寫 STRENGTH.md + 追加 _index.jsonl
  6. --pipe    →  輸出報告路徑 → vein fetch
```

harvest **不重做 discovery**：吃 `pick` 的結果，或讀 `picks.jsonl` 最近未 harvest 的項目。
保持 thin：clone/化驗是 Seam 的事，吸收 strengths、規劃新產品是 **Vein** 的事，兩者只用 pipe 接。

---

## 2. 落地目錄 layout（2T）

根目錄：`/Volumes/2T_260130/py_in_2T/github_open_project/`

```
github_open_project/
  _index.jsonl                 ← 全域索引（append-only，每 repo 一行）
  rust/
    astral-sh__ruff/
      .seam-meta.json          ← clone metadata（commit, date, size, source candidate）
      STRENGTH.md              ← 化驗報告（人類可讀）
      <repo 原始檔案…>          ← depth-1 snapshot
  python/
    simonw__llm/
      ...
  swift/
  go/
  _unknown/                    ← 無法判定主語言時
```

規則：
- 子資料夾 = repo **主語言**（lowercase）。語言對照表在 `profile.yaml` 的 `harvest.lang_map`。
- repo 目錄名 = `owner__repo`（`/` 換 `__`，避免巢狀）。
- `_index.jsonl` 是 source of truth，可 grep / jq；遺失可由各 `.seam-meta.json` 重建。

---

## 3. 資料模型（新增）

`core/models.py` 追加：

```python
@dataclass
class StrengthReport:
    candidate_id: str            # "owner/repo"
    language: str                # 主語言
    languages: dict[str, float]  # {"Rust": 82.0, "Python": 12.0}  by file ext
    clone_path: str              # 2T 上的絕對路徑
    commit: str                  # clone 當下的 HEAD sha
    sloc: int                    # 估算行數
    stars: int
    strength_tags: list[str]     # 來自固定 taxonomy（§4）
    dimensions: dict[str, int]   # 5 維各 0–100（§4）
    summary: str                 # ollama 2–3 句總結
    evidence: dict[str, list[str]]   # tag → 證據檔案/訊號
    analyzed_at: str             # ISO datetime
    profile_version: str         # 化驗當下 profile 的 hash（debug 用，承 P-S07）
```

`Candidate`（既有）會被 cloner 消費；harvest 不改 `Candidate`/`Pick`。

---

## 4. Strength taxonomy（固定標籤，對應你的需求）

5 個維度，各 0–100；分數過門檻才掛對應 tag。

| 維度 (dimension) | tag | 主要靜態訊號 | 你的原始需求 |
|---|---|---|---|
| `tech_strength` | `tech_strong` | 新/少見依賴、unsafe/FFI、演算法、星數、近期活躍 | 技術強、新技術 |
| `coding_style` | `style_strong` | rustfmt/ruff/.editorconfig/pre-commit、module 結構、type hints | coding style 強 |
| `stability` | `product_stable` | semver tag、CHANGELOG、LICENSE、release 數、issue 處理 | 產品穩定 |
| `validation` | `great_validation` | `tests/`、CI workflow、coverage、fuzz/property test、bench | 很好的驗證系統 |
| `onboarding` | `easy_onboarding` | README 長度/品質、quickstart、examples/、docs/ | 容易上手 |

- 預設掛 tag 門檻 `tag_threshold: 70`（profile 可調）。
- 每個掛上的 tag 都要在 `evidence` 留證據檔案路徑，報告才可信、可追。
- 強項只做「評分 + 分類」，**不做產品 ideation**——「借鏡哪個 pattern / 衍生什麼新產品」是 Vein 的合成職責（`vein study compare` / morning brief）。Seam 把分數與 tags 隨 slug 餵過去即可。

---

## 5. profile.yaml 新增區段

```yaml
harvest:
  target_dir: /Volumes/2T_260130/py_in_2T/github_open_project
  max_repos_per_night: 5        # 少量精選
  retention: permanent          # 永久保留；只有 `seam harvest gc` 會刪
  clone:
    depth: 1                    # shallow（D-007）
    single_branch: true
    lfs: skip                   # GIT_LFS_SKIP_SMUDGE=1（P-H04）
    size_cap_mb: 500            # 估計超過就跳過、記 skip 原因（P-H01）
    timeout_sec: 300            # 單 repo clone 上限（P-H02）
    min_free_gb: 20             # 2T 剩餘空間低於此值就停止 harvest（P-H01）
  analyze:
    tag_threshold: 70
    readme_truncate: 1500       # 承 P-S05
    sample_files: 8             # 餵 ollama 的代表性檔案數上限
    ollama_timeout_sec: 180
  lang_map:                     # GitHub language → 落地資料夾名
    Rust: rust
    Python: python
    Swift: swift
    Go: go
    TypeScript: typescript
    "C++": cpp
    default: _unknown
```

`version:` 仍沿用既有頂層欄位（P-V02）。`target_dir` 不存在時 harvest 報明確錯誤、不靜默建在 home。

---

## 6. CLI 設計

```bash
# 接在既有 pipeline 後
seam run --harvest                 # search→score→pick→clone→analyze→report
seam harvest                       # 對「最近未 harvest 的 picks」做 clone+化驗
seam harvest --from picks.jsonl --since 2026-06-01
seam harvest --dry-run             # 只印「會 clone 哪些、落地哪裡」，不動磁碟（P-L02）
seam harvest --repo owner/repo     # 手動指定單一 repo（測試用）

# 輸出給 Vein（延續 D-002 pipe；契約見 architecture §3）
seam harvest --pipe-vein           # 每行：'owner/repo --tag lang:x --tag strength:y --tag seam-score:n'
seam harvest --pipe-vein | while read l; do vein fetch $l; done
seam harvest --to-watchlist        # 寫當日 collection 進 .vein/watchlist.yaml，給 vein night-harvest 吃
seam harvest --json                # 輸出 StrengthReport jsonl（Seam 端 archive 用）

# 檢視 / 維護
seam harvest-log                   # 列最近化驗的 repo + tags + 分數
seam harvest gc --before 2026-01-01 --dry-run   # 清舊 clone（必帶 --dry-run/--yes，P-L02）
```

---

## 7. 分階段計畫（Phase 3.0 → 3.5）

每個 phase 都有三軌：**(C) coding ／ (V) validation coding ／ (D) write doc**。
任一 phase 三軌未綠不進下一 phase。

### Phase 3.0 — Foundation / scaffolding
- **(C)** 新增 `harvest/` 套件骨架；`models.py` 加 `StrengthReport`；`config.py` 讀 `harvest:` 區段（缺欄位給預設）；`store.py` 加 `_index.jsonl` atomic append（承 P-S02 pattern）；目錄 layout 建立 helper。
- **(V)** unit test：config 解析（含缺欄位 default）、`owner/repo`→落地路徑映射、index append fsync 原子性、`target_dir` 不存在時報錯。
- **(D)** 本檔 §2/§3/§5 定稿；`decisions.md` 寫 D-006~D-010；profile schema 範例。

### Phase 3.1 — Clone engine
- **(C)** `harvest/cloner.py`：`git clone --depth 1 --single-branch`、`GIT_LFS_SKIP_SMUDGE=1`、clone 前 free-space precheck + size 估算、subprocess timeout、已存在則 `git fetch --depth 1` 更新而非重 clone、寫 `.seam-meta.json`、依語言歸檔。`seam harvest --dry-run` 可跑。
- **(V)** integration test：clone 一個已知小 repo 到 temp（非 2T）；mock 磁碟不足 → 應跳過不 crash；重複 clone → 應 skip/update；timeout → 應記錄並續跑下一個。
- **(D)** clone 策略說明；pitfalls P-H01~P-H04。

### Phase 3.2 — Static signal collector
- **(C)** `harvest/signals.py`：純檔案掃描（零 network）抽 tests/、CI workflow、linter/formatter config、LICENSE/CHANGELOG、semver tag、dep manifest、副檔名語言分布、SLOC（有 `tokei` 用之，否則 `wc` 估算）、README 長度。
- **(V)** golden-file test：放 2–3 個 fixture repo 結構，斷言每個 signal 抽取正確；空 repo / 無 README / monorepo 邊界。
- **(D)** signal → 維度對照表（§4 的詳版）；「哪個檔案證明哪個 strength」。

### Phase 3.3 — Strength analyzer（ollama）
- **(C)** `harvest/analyzer.py`：用 signals + 取樣檔案（≤`sample_files`）+ 截斷 README 組 prompt → ollama → 解析成 5 維分數 + tags（過 `tag_threshold` 才掛）+ summary + evidence。heuristic fallback：純靠 signals 給分（承 I-003），ollama 不可用時印警告（P-V01）。
- **(V)** test prompt builder；mock ollama JSON 測 parser（含壞 JSON 容錯）；heuristic fallback 跑得完；對 3 個已知 repo 做 sanity eval（ruff→tech/style 高、教學型 repo→onboarding 高）。
- **(D)** 5 維定義、prompt template、evidence 規格；P-H05。

### Phase 3.4 — Report output + Vein pipe
- **(C)** 產 `STRENGTH.md`（人類可讀：tags、分數表、evidence）；追加 `_index.jsonl`；`seam harvest --pipe-vein`（slug+tags）、`--to-watchlist`、`--json`、`seam harvest-log`。契約見 architecture §3。
- **(V)** end-to-end：對 1–2 repo 跑完整 harvest，驗 STRENGTH.md + index；`--pipe-vein` 輸出可被 `vein fetch` 解析、`--to-watchlist` yaml 符合 vein `_load_watchlist`；報告冪等（重跑同 repo 不重複 index 行，I-004）。
- **(D)** STRENGTH.md 格式規格；Seam↔Vein 契約（architecture §3）。

### Phase 3.5 — Orchestration（Phase n，**無 cron**）
- **(C)** `seam_harvest_entry.py`：一次性 entry（跑一輪即結束），由 **cowork schedule** 觸發，非 cron、非常駐 loop。內含 `runtime/lock.py`（防重觸）+ `runtime/history.py`（catch-up / self-check / 冪等，**語意參考 `schedule_entry.py`**）。架構與 skeleton 見 architecture §4–§7。
- **(V)** python validation（参 `schedule_entry.py`）：冪等重跑、中斷續跑（`in_progress` 保守跳過、不重燒 ollama）、`--self-check` 重試昨夜 fail、失效過濾、lock 防重觸、磁碟/timeout 守門。清單見 architecture §8。
- **(D)** architecture §7 的 cowork schedule 兩步鏈接法、runbook；P-H06 改為 entry 自身 logging + lock + history。

> **Phase 4（future，非本次範圍）**：「吸收 strengths → 規劃新產品」全程是 **Vein** 的職責（`vein study compare` / debrief / morning brief）。Seam 只餵「對的 repo + 分類 tags」，不做 ideation（守 thin 原則）。

---

## 8. 驗證總計畫（cross-phase）

| 層級 | 方法 | 對應 phase |
|---|---|---|
| Unit | config/path/index/parser，pytest | 3.0, 3.2, 3.3 |
| Integration | 真 clone 小 repo 到 temp、mock 磁碟/timeout | 3.1, 3.4 |
| Eval | 3 個已知 repo 人工核對 tags 是否合理 | 3.3 |
| E2E | `seam run --harvest` → STRENGTH.md → pipe → mock vein | 3.4, 3.5 |
| Soak | 30 min 無人值守 + 中斷續跑 | 3.5 |
| Safety | `--dry-run`/`--yes`、free-space guard、不重 publish secrets | 全程 |

驗收門檻（承 plan.md §3 風格）：
1. `seam run --harvest` 連跑 30 min 不 crash，磁碟不足會優雅停止。
2. 每個 STRENGTH.md 的 tag 都有 evidence，且人工抽查合理。
3. `seam harvest --pipe | xargs -I{} vein fetch {}` 端到端通。
4. 重跑同一天不重複 clone、不重複寫 index（冪等）。

---

## 9. Non-Goals（Phase 3）

- **不做產品規劃 / ideation**：吸收強項與規劃新產品是 Vein 的職責（D-013）。
- **不留 git history**：depth-1 snapshot；要 history 請自己 `git clone` 該 repo。
- **不自動清磁碟**：永久保留，只有手動 `seam harvest gc`。
- **不 build / 不執行**：純靜態化驗，絕不執行 clone 下來的程式碼（安全）。
- **不重新發布**：clone 與報告只存本地 2T，不上傳、不對外。

---

## 10. 決策摘要（詳見 decisions.md）

- **D-006** harvest 放 Seam Phase 3，不開新 repo。
- **D-007** shallow `--depth 1`。
- **D-008** 少量精選 + 永久保留。
- **D-009** 靜態 signal + ollama，heuristic fallback。
- **D-010** Vein 整合走 CLI pipe，輸出 STRENGTH 報告。
