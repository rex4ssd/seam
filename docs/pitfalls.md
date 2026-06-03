# Seam — Pitfalls & 雷區

> 從 Lode、Vein 的實戰 + Seam 預期會踩的雷，寫在這裡。
> 碰相關 code 前先讀對應條目。

---

## 從 Lode 繼承的 Python/CLI 雷

### P-L01 — 確認 prompt 要用 `echo`，不用 `printf`（無換行）

**來源：** Lode PITFALLS_2026-05-20.md
**症狀：** `printf "Delete? [y/N] "` + `read -r` — line iterator 等 `\n` 才 yield，導致 confirm 永遠 hang。
**Fix：** `echo "Delete? (y/N)"` 帶換行 + `read -r`。所有 `.sh` confirm prompt 一律用 `echo`。

### P-L02 — 所有 destructive 操作必須有 `--dry-run` + `--yes`

**來源：** Lode SOP
**適用：** `seam gc`、任何刪除 picks 的操作。
**Pattern：**
```python
@click.option("--dry-run", is_flag=True)
@click.option("--yes", "-y", is_flag=True)
def gc(..., dry_run, yes):
    if not dry_run and not yes:
        click.confirm(f"Delete {n} entries?", abort=True)
```

### P-L03 — 不要在 binary/script 名稱裡放版本號

**來源：** Lode release pipeline
**症狀：** `seam-run-v1.2.sh` → cron job reference 壞掉；自動化腳本找不到固定入口。
**Fix：** 固定名稱 `seam_run.sh`，版本號只進 git tag，不進檔名。

---

## 從 Vein 繼承的 Python/ollama 雷

### P-V01 — ollama 失敗不可 silent fallback

**來源：** Vein I-002
**症狀：** user 以為 AI scoring 在跑，實際上 ollama 壞掉 silent fallback 成 heuristic，picks 品質爛但不知道為什麼。
**Fix：** 明確印 `[yellow]ollama unavailable — heuristic fallback[/]`。

### P-V02 — config.yaml 必須有 `version:` 欄位

**來源：** Vein I-004
**症狀：** schema 改了，舊 `.seam/profile.yaml` 讀到新欄位報 KeyError。
**Fix：** `profile.yaml` 最頂層必須有 `version: 1`；loader 檢查版本，未知版本給 warning。

### P-V03 — `.seam/cache/` 不可進 git

**來源：** Vein I-001
**Fix：** `.seam/.gitignore` 內容：
```
cache/
*.log
```

### P-V04 — 任何 outbound HTTP 不可默默 phone-home

**來源：** Vein I-006
**適用：** Seam 的 GitHub / YouTube API call 都是 user-triggered（`seam run`）。
不可有「自動 check for updates」「匿名使用統計」。

---

## Seam 特有的雷（預測）

### P-S01 — GitHub API 429（rate limit）

**觸發：** 無 token 時 60 req/hr；有 token 5000 req/hr。夜間跑多個 query + README fetch 容易撞。
**症狀：** `seam run` 中途炸，只撈到一半 candidates。
**Fix：**
- `Retry-After` header 存在就 sleep 那麼久
- 每個 request 之間加 `time.sleep(0.5)` 基本節流
- token 沒設時印警告：「建議設 GITHUB_TOKEN，否則 rate limit = 60/hr」
- README fetch 失敗不 crash，直接用空 description scoring

```python
def _gh_get(url: str, token: str, max_retries: int = 3):
    headers = {"Authorization": f"token {token}"} if token else {}
    for attempt in range(max_retries):
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            time.sleep(wait)
            continue
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            time.sleep(60)
            continue
        return resp
    return None   # exhausted retries → caller handles gracefully
```

### P-S02 — picks.jsonl 寫一半 crash → 損壞

**觸發：** 夜間 cron 被 SIGKILL（電腦睡眠、磁碟滿、OOM）。
**Fix：** 每條 pick 單獨 append，append 後立刻 flush + fsync。不要累積再一次寫。

```python
def append_pick(path: Path, pick: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(pick, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
```

### P-S03 — 同一 repo 被多個 query 重複候選

**觸發：** `topic:tauri` 和 `topic:macos-app language:rust` 都可能回傳同一個 repo。
**Fix：** scoring 前 dedup by `(source, repo_full_name)`。

### P-S04 — "已看過的 repo" 問題：每天重複推薦同一批

**觸發：** GitHub trending 變動慢，同一批 repo 連續 7 天都進候選。
**Fix：** `picks.jsonl` 建立 "seen" set。候選 scoring 時排除最近 N 天已 pick 過的 repo。
```python
SEEN_COOLDOWN_DAYS = 14   # 同一個 repo 兩次 pick 間隔最短 14 天
```

### P-S05 — ollama scoring 對長 README 慢 / timeout

**觸發：** `deepseek-r1:14b` 處理 8K chars README 要 60-120 秒；夜間批次 10 個 repo = 20 分鐘。
**Fix：**
- README 截斷到 1500 chars 再送 scoring（description 已夠判斷相關性）
- 單次 ollama call timeout = 120s；超時 → heuristic fallback（不 crash）
- 10 個 repo 順序跑，不並行（避免 ollama OOM）

### P-S06 — cron job 沒有 log → 夜間炸了不知道

**觸發：** `seam run` 在 02:00 跑，早上沒 output，不知道炸在哪。
**Fix：** 所有輸出寫進 `~/.seam-harvest.log`，cron 設定：
```cron
0 2 * * * cd ~/Documents/seam && seam run >> ~/.seam-harvest.log 2>&1
```
log 保留 30 天，`seam gc` 順便清 log。

### P-S07 — Profile.yaml 過時 → picks 持續推無關內容

**觸發：** Lode 做完了，改做別的專案，但 profile.yaml 還寫 `interests: [tauri, macos-app]`。
**Fix：** picks.jsonl 裡每條 pick 記錄 profile version（profile 的 hash 或 date），方便 debug「為什麼當時挑這個」。每次大方向改變時更新 profile.yaml。

### P-S08 — YouTube Data API quota 耗盡

**觸發：** 每天 10K units free。search = 100 units；video details = 1 unit。
一天 50 search = 5000 units；200 video details = 200 units；總計 ~5200 OK。
但如果 bug 導致 retry loop → 一天 quota 在幾分鐘內耗盡。
**Fix：**
- API call 前 check daily budget counter（存在 `.seam/state.json`）
- 超過 budget → 跳過該 source，不 crash
- quota 耗盡時印明確警告，不 silent skip

---

## Phase 3 harvest 特有的雷（預測）

### P-H01 — 2T 磁碟滿 → clone 寫一半損壞

**觸發：** 永久保留策略下 2T 越來越滿；clone 大 repo 中途空間不足。
**Fix：**
- clone 前 `shutil.disk_usage(target_dir)` 檢查剩餘空間 < `min_free_gb`（預設 20G）就停止整個 harvest，印明確警告。
- 用 GitHub API 的 repo `size`（KB）預估，超過 `size_cap_mb` 直接跳過、在 `_index.jsonl` 記 `skipped: size`。
- clone 到 temp 目錄成功後才 `mv` 進正式 layout（避免半成品占位）。

### P-H02 — `git clone` 夜間 hang 卡死整批

**觸發：** 網路抖動、超大 repo、LFS 拉取。一個卡住 → 整晚 harvest 停擺。
**Fix：**
- subprocess 帶 `timeout=clone.timeout_sec`（預設 300s），超時 kill + 記錄 + 續跑下一個。
- `--single-branch --depth 1` 降低資料量。

```python
try:
    subprocess.run(["git", "clone", "--depth", "1", "--single-branch", url, tmp],
                   timeout=cfg.timeout_sec, check=True,
                   env={**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"})
except subprocess.TimeoutExpired:
    log.warning("clone timeout, skip %s", repo); continue
```

### P-H03 — 重複 harvest 同一 repo → 重 clone 浪費

**觸發：** 同一 repo 被再次 pick；或重跑當天 harvest。
**Fix：** 落地路徑已存在 → 不重 clone，改 `git fetch --depth 1 && git reset --hard origin/HEAD` 更新；`_index.jsonl` 用 `(repo, commit)` 去重，commit 沒變就跳過化驗（承 I-004 冪等）。

### P-H04 — Git LFS / 大 binary 拖垮 shallow clone

**觸發：** repo 用 LFS 存模型/資料集，depth-1 仍會 smudge 拉 GB 級檔案。
**Fix：** `GIT_LFS_SKIP_SMUDGE=1` 環境變數；化驗只看程式碼與設定檔，不需 LFS 內容。

### P-H05 — ollama 化驗整個 repo → context 爆 / timeout

**觸發：** 把整包 source 丟給 ollama 必爆 context window。
**Fix：**
- 只送：靜態 signal 摘要 + 截斷 README（`readme_truncate`，預設 1500）+ 取樣代表檔案（`sample_files`，預設 8，挑入口/設定/測試各一）。
- 單次 ollama `timeout=ollama_timeout_sec`（預設 180s），超時 → heuristic fallback（純 signal 評分），印警告（承 P-V01）。

### P-H06 — harvest 夜間炸了沒 log / 重觸 / 鎖沒放（無 cron，cowork schedule 觸發）

**觸發：** harvest 比 search/score 久（含 clone）；cowork schedule 可能重觸或上次中途死掉。
**Fix：**（orchestration = `seam_harvest_entry.py` 一次性 entry，非 cron，見 `harvest_architecture.md` §7）
- entry 自身 logging（不靠 cron 重導向）；每階段寫 `harvest_history.csv`（running→pass/fail）。
- lock file `.seam/harvest.lock`（含 pid），啟動檢查；stale lock（pid 不存在）自動清，防 cowork schedule 雙觸。
- 中斷後重跑：`(repo, commit)` 已 finalized 跳過；只有 `running` 沒終局的非冪等階段（analyze/veinout）保守跳過，不重燒 ollama（語意參考 `schedule_entry.py` 的 in_progress）。
- 補跑 / 重試：`--self-check` 模式掃前 24h fail 逐筆重試（參考 `run_self_check()`）。

### P-H07 — 絕不執行 clone 下來的程式碼

**觸發：** 為了測 validation 強度，誘惑去 build / 跑測試。
**Fix：** 化驗**純靜態**。不 `cargo build`、不 `pytest`、不 `npm install`、不跑任何 repo 內 script（守 D-009 安全 trade-off）。validation 強度只由靜態跡象（CI config、test 檔數、coverage badge）推估。

---

## Invariants（不可違反）

### I-001 — GITHUB_TOKEN 不可進 profile.yaml

profile.yaml 是 committed file。token 只走 `.env` 或 env var `GITHUB_TOKEN`。

### I-002 — picks.jsonl 只 append，不 modify，不 delete（除非 gc）

歷史 picks 是 point-in-time 紀錄。用 `seam gc --before DATE` 歸檔，不直接刪行。

### I-003 — seam run 必須能無 ollama 跑完（heuristic fallback）

夜間 cron 不可因 ollama 掛掉就全程 crash。heuristic scorer 是必要的非 AI path。

### I-004 — seam run 必須冪等（idempotent）

同一天跑兩次 → 結果相同，不重複寫 picks，不重複 API call（cache 結果）。
