# seam 使用手冊

> **地質考查。找到好的地，再來挖。**
> Overnight intelligence prospector. Finds what's worth mining. Feeds Vein.

---

## 最重要的事：兩個階段，分開跑

```
seam run                       ← Phase 1: 搜尋 + 評分 + 選出精選
                                  結果存進 .seam/picks.jsonl
                                  2T 上什麼都沒有

python seam_harvest_entry.py   ← Phase 2: clone + 化驗 + 寫報告
                                  讀 picks.jsonl → git clone → STRENGTH.md
                                  結果落地在 2T
```

**`seam run` 不 clone。** 它只做搜尋和評分，輸出是「誰值得 clone」，不是實際 clone。

---

## 安裝

```bash
cd /Users/lion/Documents/seam
source venv/bin/activate       # 或 python -m venv venv && source venv/bin/activate
pip install -e .
seam --version
```

---

## 全命令一覽

### seam CLI

```
seam init          — 建立 .seam/profile.yaml 範本
seam run           — Phase 1: GitHub 搜尋 → ollama 評分 → 選出精選（存 picks.jsonl）
seam search        — 只做搜尋，印 candidate JSON
seam score         — 從 stdin 讀 candidates，輸出評分
seam pick          — 從 stdin 讀 scored candidates，選出 top-N
seam log           — 查看最近幾天的 picks
seam harvest       — git clone 已 pick 的 repo 到 2T（無 ollama 化驗）
```

### Phase 2 entry script

```bash
python seam_harvest_entry.py              # 完整 harvest（clone + 化驗 + 報告）
python seam_harvest_entry.py --dry-run    # 只看計畫，不動磁碟
python seam_harvest_entry.py --self-check # 重試昨晚失敗的 stage
```

---

## profile.yaml — 告訴 Seam 你想找什麼

```yaml
# .seam/profile.yaml
version: 1

# 你是誰（送給 ollama 當評分 context）
profile: |
  Solo developer, Python 5+ years, Rust beginner, React/TypeScript intermediate.
  Mac Studio M1 32GB. Main projects: Lode (Tauri 2 desktop app, Rust+React),
  Vein (Python CLI, decision lore archive). Available: ~4 weeks per side project.
  Goals: improve Lode features, find reusable patterns, learn Rust idioms.

# 感興趣的主題（影響評分權重）
interests:
  primary:   [tauri, macos-app, rust, file-diff, code-search, desktop-app]
  secondary: [python-cli, developer-tools, ai-tooling, sqlite]
  avoid:     [mobile, kubernetes, enterprise, java]

# 評分參數
scoring:
  target_relevance_weight: 0.6   # 與你專案的相關度
  solo_feasible_weight:    0.4   # solo dev 能學/用的程度
  min_score: 60                  # 低於這個不進 picks

picks_per_day: 3

# GitHub 搜尋
github:
  token: ""          # 已停用：token 只從 env 讀（export GITHUB_TOKEN=ghp_xxx）；此欄位若填值會被警告並忽略
  min_stars: 200     # 硬過濾：少於這個直接丟掉
  max_age_days: 90   # 超過 N 天沒 push 的也丟掉

  queries:           # 每次輪流跑，14 天 cooldown 防重複推薦
    - "topic:tauri stars:>200"
    - "topic:macos-app language:rust stars:>100"
    - "topic:python-cli stars:>500"
    - "topic:developer-tools language:python stars:>300"
    - "topic:file-diff stars:>100"
    - "topic:ai-tooling language:python stars:>200"
    - "topic:sqlite language:python stars:>200"

# ollama 評分模型
model:
  base_url: http://localhost:11434
  score_model: deepseek-r1:14b    # Phase 1 評分（需要 reasoning）
  fallback: heuristic             # ollama 掛掉時改用星數+關鍵字

# Phase 2 harvest 設定
harvest:
  target_dir: /Volumes/2T_260130/py_in_2T/github_open_project
  max_repos_per_night: 3
  clone:
    depth: 1
    size_cap_mb: 500             # 超過就跳過，記 skip 原因
    timeout_sec: 300
    min_free_gb: 20              # 2T 剩餘空間低於此值就停止
  analyze:
    tag_threshold: 70            # 維度分數 ≥ 70 才掛 tag
    ollama_timeout_sec: 180
    sample_files: 8              # 餵 ollama 的代表性源碼檔數上限
```

---

## 換 queries：找不同類型

### macOS app

```yaml
queries:
  - "topic:macos-app language:swift stars:>500"
  - "topic:macos-app language:rust stars:>300"
  - "topic:swiftui stars:>500"
interests:
  primary: [macos-app, swift, swiftui, cocoa, appkit]
  avoid:   [ios, android, windows, linux]
```

### iPhone / iOS app

```yaml
queries:
  - "topic:ios language:swift stars:>1000"
  - "topic:swiftui stars:>500"
  - "topic:uikit stars:>300"
interests:
  primary: [ios, swift, swiftui, uikit, xcode]
```

### Linux driver / kernel

```yaml
queries:
  - "topic:linux-kernel language:c stars:>200"
  - "topic:ebpf stars:>300"
  - "topic:device-driver language:c stars:>100"
interests:
  primary: [linux, kernel, driver, ebpf, c]
  avoid:   [windows, macos, java]
```

### 多加 forks 過濾（GitHub 支援）

```yaml
# query 字串直接加 forks:>N
queries:
  - "topic:rust stars:>500 forks:>100"
```

> GitHub 沒有「下載量」API。替代指標：`stars` = 受歡迎、`forks` = 被複用、
> `pushed_at` = 活躍（`max_age_days` 控制）。

---

## Phase 1：`seam run` 詳解

```bash
seam run                          # 標準（ollama 評分）
seam run --verbose                # 顯示每個 repo 的分數和原因
seam run --engine heuristic       # 跳過 ollama，只用星數+關鍵字（快，離線可用）
seam run --engine ollama          # 強制用 ollama（不 fallback）
seam run --no-save                # 不存 picks.jsonl（測試用）
seam run --no-cooldown            # 忽略 14 天 cooldown（強制重找）
seam run --picks 5                # 今天最多幾個
```

**跑完 `seam run` 後的狀態：**
- `.seam/picks.jsonl` 追加了今天的精選
- 2T 上**什麼都還沒有**

---

## Phase 2：`seam_harvest_entry.py` 詳解

讀 picks.jsonl → git clone → 靜態掃描 → ollama 化驗 → 寫報告

```bash
python seam_harvest_entry.py --dry-run    # 先看計畫
python seam_harvest_entry.py              # 正式跑
python seam_harvest_entry.py --self-check # 重試 24h 內失敗的 stage
```

**跑完後的 2T 結構：**

```
/Volumes/2T_260130/py_in_2T/github_open_project/
  _index.jsonl                  ← 全域索引，append-only，可 grep / jq
  rust/
    rustdesk__rustdesk/         ← owner/repo → owner__repo（/ 換 __）
      STRENGTH.md               ← 化驗報告（人類可讀）
      .seam-meta.json           ← clone metadata（commit, stars, date）
      <repo 原始碼>
  python/
    simonw__llm/
      STRENGTH.md
      ...
  typescript/
    ...
```

**STRENGTH.md 長這樣：**

```markdown
# STRENGTH REPORT: rustdesk/rustdesk
> Analyzed: 2026-06-03T02:15:33 | Engine: ollama | Profile: a1b2c3d4

## Summary
Cross-platform remote desktop in Rust. The Tauri-adjacent IPC architecture and
strong validation make it directly applicable to Lode's event handling pattern.

## Scores
| Dimension     | Score | Tag                  |
|---|---|---|
| tech_strength | 85    | ✓ `tech_strong`      |
| coding_style  | 72    | ✓ `style_strong`     |
| stability     | 88    | ✓ `product_stable`   |
| validation    | 80    | ✓ `great_validation` |
| onboarding    | 55    | —                    |

**Tags:** `tech_strong` `style_strong` `product_stable` `great_validation`

## Evidence
### tech_strong
- src/server/input_service.rs: unsafe FFI for platform-level event injection
...
```

---

## 快速腳本

```bash
# 一鍵跑完整流程（run + harvest）
./script/full_run.sh

# 只跑 harvest（picks 已存在）
./script/harvest_only.sh

# 查看 2T 上已 harvest 的 repo
./script/check_harvest.sh

# 印每個 STRENGTH.md 的 summary
./script/show_strength.sh

# 即時追 harvest log
./script/tail_log.sh

# 查詢 _index.jsonl（接受 tag 過濾）
python script/index_query.py
python script/index_query.py --tag tech_strong
python script/index_query.py --lang rust
```

---

## Vein 整合

Harvest 完成後 `~/.vein/watchlist.yaml` 已自動寫入：

```yaml
seam-2026-06-03:
  repos:
    - rustdesk/rustdesk
    - simonw/llm
  compare: true
```

```bash
# Vein 抓 lore / decision / pitfall（需 vein 在 PATH）
vein night-harvest

# 或只跑今天 seam 選的
vein study watchlist run seam-2026-06-03

# recall
vein recall "rust IPC pattern"
vein recall "strength tech_strong"
```

---

## 查看歷史

```bash
# harvest 歷史（各 repo 各 stage 的成功/失敗）
column -t -s, ~/.seam/harvest_history.csv | head -30

# 全域索引
cat /Volumes/2T_260130/py_in_2T/github_open_project/_index.jsonl \
  | python3 -c "import sys,json; [print(json.loads(l)['candidate_id'], json.loads(l)['strength_tags']) for l in sys.stdin]"

# 最近的 picks
seam log
```

---

## 夜間自動排程

已設定 cowork schedule（每晚 02:00）。若要手動確認排程：

```bash
# 在 Cowork sidebar → Scheduled → seam-nightly-harvest
# 或手動觸發測試
python seam_harvest_entry.py --dry-run
```

---

## 完整流程圖

```
每晚 02:00（cowork schedule 觸發）
  │
  ▼  seam_harvest_entry.py
  │
  ├─ run_pipeline()                   Phase 1
  │    ├─ GitHub Search API           多個 queries 輪流跑
  │    ├─ dedup + min_stars 過濾
  │    ├─ ollama 評分（deepseek-r1）
  │    │    fallback: stars + keyword heuristic
  │    ├─ 14 天 cooldown 過濾
  │    └─ 寫 .seam/picks.jsonl
  │
  └─ for each pick:                   Phase 2（逐 repo）
       ├─ git clone --depth 1         → 2T/<lang>/<owner>__<repo>/
       │    GIT_LFS_SKIP_SMUDGE=1
       │    timeout 300s
       │    free space check（<20G 就停）
       │
       ├─ collect_signals()           靜態掃描（零 network）
       │    tests/ CI linters LICENSE changelog semver SLOC README
       │
       ├─ analyze()                   ollama 化驗
       │    prompt = signals + README[:1500] + 8 sample files
       │    → 5 維分數(0-100) + tags + summary + evidence
       │    fallback: heuristic_score()（純 signals 計算）
       │
       ├─ write_report()
       │    → STRENGTH.md（人類可讀報告）
       │    → _index.jsonl（atomic append，idempotent）
       │
       └─ write_vein_watchlist()
            → ~/.vein/watchlist.yaml
```

---

## 常見問題

**Q: `seam run` 跑完，2T 上沒東西**
A: 對。`seam run` 只選 repo，不 clone。要 clone 跑 `python seam_harvest_entry.py`。

**Q: ollama 很慢**
A: `seam run --engine heuristic` 跳過 ollama，用星數+關鍵字快速評分。
   harvest 的 analyze 階段也會自動 fallback 到 heuristic（會印警告）。

**Q: 2T 空間不夠**
A: 降低 `harvest.max_repos_per_night: 1`，或調高 `clone.size_cap_mb` 過濾大 repo。
   也可以 `seam harvest gc --before 2026-01-01 --dry-run` 查舊 clone。

**Q: 同一個 repo 重複出現**
A: `picks.jsonl` 有 14 天 cooldown，通常不會重複。強制重找：`seam run --no-cooldown`。

**Q: harvest 中途失敗**
A: `python seam_harvest_entry.py --self-check` 重試 24h 內失敗的。
   history 在 `~/.seam/harvest_history.csv`。
