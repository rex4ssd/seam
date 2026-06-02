# Seam — Working Style & Principles

> 給接手的 Claude 看。從 Lode + Vein 兩個專案的實戰中蒸餾出來。

---

## 1. 工作原則

### 程式碼

- **繁體中文 + 技術詞英文**：docs/comments 繁中，code identifier 英文
- **terse > verbose**：回答清楚有邏輯，不寫不必要的 postamble
- **user 說 "ca"** → 直接 `git add -A && git commit -F -`，不問
- **遇到 index.lock** → `rm -f .git/index.lock` 後再 commit
- **多行 cmd → .sh**；**多行 .sh → .py**（參考 cmd_entry 模式）
- **sandbox 沒有的東西**（ollama call、cron 設定）→ 包成 .sh 給 user 跑

### 決策

- 任何 trade-off → 寫進 `docs/decisions.md`
- 不踩 Lode/Vein 踩過的同類雷（見 `docs/pitfalls.md`）
- Phase 0 先可跑，不要提前最佳化

---

## 2. 從 Vein 學到的 Python CLI 原則

### Config 設計（Vein D-015、I-004）

```python
# ✅ 正確：versioned config，backend 抽象化
config = {
    "version": 1,
    "model": {
        "base_url": "http://localhost:11434",   # 不寫死 URL
        "score_model": "deepseek-r1:14b",
    }
}

# ❌ 錯誤：hardcode URL + model
result = httpx.post("http://localhost:11434/api/chat", ...)
```

- `version: 1` 必須在 config 最頂層（未來 migration 用）
- `base_url` 從 config 讀，不 hardcode
- 未來切換 Rapid-MLX / LM Studio → 只改 base_url，不改 code

### 密鑰管理（Vein D-020）

```
.seam/profile.yaml   ← 非敏感設定（committed）
.env                 ← GITHUB_TOKEN、YOUTUBE_API_KEY（gitignored）
.env.example         ← key template（committed）
```

**GITHUB_TOKEN 絕對不進 profile.yaml**，永遠走 env var。

### ollama 失敗要顯式報錯（Vein I-002）

```python
# ✅ 正確
result = _call_ollama_score(...)
if result is None:
    console.print("[yellow]ollama unavailable — falling back to heuristic scorer[/]")
    result = _heuristic_score(candidates)

# ❌ 錯誤：silent fallback，user 以為 AI 在跑但其實沒有
try:
    result = _call_ollama_score(...)
except:
    result = _heuristic_score(candidates)  # 完全靜默
```

### Graceful degrade 順序

```
deepseek-r1:14b（scoring）
  → 連不上 ollama → heuristic scorer（stars + keyword match）
  → heuristic 也壞 → 直接輸出 top-N by stars，印明確警告
```

三層，永遠有輸出，永遠知道用了哪層。

---

## 3. Seam 特有的雷（預先記錄）

完整版見 `docs/pitfalls.md`。最重要的三條：

### P-001：GitHub API 429 → 程式掉的地方

```python
# ✅ 正確：per-request retry + exponential backoff
for attempt in range(3):
    resp = requests.get(url, headers=headers)
    if resp.status_code == 429:
        wait = int(resp.headers.get("Retry-After", 60))
        time.sleep(wait)
        continue
    break

# ❌ 錯誤：429 讓整個 seam run 炸掉，不 retry
resp.raise_for_status()
```

### P-002：picks.jsonl 寫一半崩 → append-only 寫法

```python
# ✅ 正確：atomic line append
with open(picks_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(pick_dict, ensure_ascii=False) + "\n")
    f.flush()
    os.fsync(f.fileno())   # 確保 flush 到 disk

# ❌ 錯誤：一次寫很多行，crash 半路就壞了
```

### P-003：同一個 repo 被多個 query 挑到 → 先 dedup 再 score

```python
# 多個 query 可能回同一個 repo
# dedup by (source, id) 在送 ollama 之前做
seen = set()
unique = []
for c in candidates:
    key = (c.source, c.id)
    if key not in seen:
        seen.add(key)
        unique.append(c)
```

---

## 4. 不做的事（from Vein D-029 focus lesson）

Vein 在 Phase 0 做了 13 個 command，結果定位模糊。**Seam 不重蹈這個覆轍。**

Phase 0 只有 6 個 command：`init / search / score / pick / run / log`

以下功能**Phase 0 不做**，等 core 穩了再說：
- YouTube source（Phase 1）
- HN / RSS source（Phase 2）
- `seam watchlist`（Phase 1）
- Web dashboard
- Slack/email notification
- Multi-machine sync

---

## 5. Data 生命週期（from Vein D-026）

```
picks.jsonl（append-only）
    ↓ 每天累積
seam gc --before 2026-01-01   # 定期歸檔
    ↓ 不刪，archive
.seam/archive/picks-2026-H1.jsonl
```

picks 是 point-in-time 斷言（「這個 repo 當時對你有用」）。
不自動刪，因為回頭看 pick history 是很有用的 pattern。
