# Seam — Decisions Log

---

## D-001 — 獨立專案，不放進 Vein

**Date:** 2026-06-01
**Decision:** Seam 是獨立 repo，不 merge 進 Vein。

**Why:** Vein 的 Path D 定位是「你自己專案的 decision lore archive」。
把 GitHub 搜尋 + YouTube 分析放進去會稀釋這個定位，讓 Vein 變成
不好解釋的混合物。關注點分離：Seam 發現，Vein 儲存。

**Trade-off:** 需要維護兩個 repo，有重複的 ollama call pattern。
接受這個代價，因為各自可以獨立演進。

---

## D-002 — CLI pipe 為主要整合方式

**Date:** 2026-06-01
**Decision:** `seam run --pipe | vein fetch` 而非 Python SDK 整合。

**Why:** Unix pipe 最具組合性。Vein 重構時 Seam 不受影響。
不引入跨專案 Python dependency（版本衝突風險）。

**Trade-off:** 略有 process overhead（subprocess fork）。
在 overnight batch 場景完全可接受。

---

## D-003 — 本地 AI 評分，不用 cloud LLM

**Date:** 2026-06-01
**Decision:** ollama (deepseek-r1:14b) 評分，fallback 到 heuristic。

**Why:** 夜間無人值守，不需要 API key 管理。repo list 可能包含
尚未公開的個人研究方向，不希望送到 cloud。成本零。

**Trade-off:** 評分品質低於 GPT-4。用 deepseek-r1:14b 的 reasoning
能力彌補部分差距。heuristic fallback 確保 ollama 掛掉時仍可運作。

---

## D-004 — 只用官方 API，不 scrape

**Date:** 2026-06-01
**Decision:** GitHub Search API + YouTube Data API v3，不爬 trending 頁。

**Why:** GitHub trending 頁無官方 API，HTML 結構隨時變動，維護成本高。
GitHub Search API 穩定、有文件、免費（需 token 提升 rate limit）。

**Trade-off:** 不能直接獲取「今日 trending」。用 topic + star + 活躍度
query 組合近似 trending 效果，且更可以針對 Rex 的 interest 客製化。

---

## D-005 — picks 存 JSON Lines，不用 SQLite

**Date:** 2026-06-01
**Decision:** `.seam/picks.jsonl`，append-only。

**Why:** Seam 是 thin tool，不需要查詢複雜度。jsonl 人類可讀、
可 grep、不需要 schema migration、天然 append-only。
SQLite 是 Vein 的責任範圍。

**Trade-off:** 無法做複雜查詢（e.g. "show picks from last month by score desc"）。
用 `seam log | grep` 或 `jq` 足夠應付。超過 10K 行再考慮 SQLite。

---

## D-006 — harvest 放進 Seam Phase 3，不開新 repo

**Date:** 2026-06-02
**Decision:** clone + 化驗能力做成 Seam 的 Phase 3（`seam harvest`），沿用既有 search→score→pick。

**Why:** harvest 吃的就是 pick 的結果，discovery 邏輯完全共用；開新 repo 會重複維護 profile/config/ollama call。仍守關注點分離：Seam 發現+化驗，Vein 吸收+規劃。

**Trade-off:** Seam 從「只輸出 owner/repo」變成會動磁碟的工具，比原本重。接受，因為化驗報告本質仍是「prospect 的產物」，沒越界到 Vein 的儲存/規劃職責。

---

## D-007 — Clone 用 shallow `--depth 1`

**Date:** 2026-06-02
**Decision:** `git clone --depth 1 --single-branch`，不留 git history。

**Why:** 化驗看的是「現在這版 code 的 structure / style / tests」，不需要歷史。depth-1 最省 2T 空間、最快，能 clone 更多 repo。LFS 一律 skip（`GIT_LFS_SKIP_SMUDGE=1`）。

**Trade-off:** 無法分析 commit 頻率/貢獻者/release 節奏（產品穩定度的部分訊號）。用 GitHub API metadata（star、pushed_at、release count）補，已足夠判斷 stability，不需整段 history。

---

## D-008 — 少量精選 + 永久保留

**Date:** 2026-06-02
**Decision:** 每晚 harvest 3–5 個高分 repo，clone 永久留在 2T，只有手動 `seam harvest gc` 會刪。

**Why:** 守 Seam「picks signal not volume」原則。少量精選讓 2T 不會爆，永久保留讓 clone 可反覆研讀、不怕自動清掉重要參考。

**Trade-off:** 需手動維護磁碟。可接受 — 2T 容量大，3–5/晚的成長很慢，且 gc 有 `--dry-run`/`--yes` 防呆。

---

## D-009 — 靜態 signal + ollama 判讀，heuristic fallback

**Date:** 2026-06-02
**Decision:** 化驗 = 先抽純檔案靜態 signal（tests/CI/linter/license…），再餵 ollama 產 tags/分數/summary/evidence；ollama 不可用時用純 signal 的 heuristic 評分。（注：原含 vein_seeds，已由 D-013 移除。）

**Why:** 靜態 signal 客觀、零成本、可當證據（evidence）；ollama 補上「coding style 好不好、技術新不新」這種需判讀的部分。承 I-003，夜間 ollama 掛掉仍要跑得完。

**Trade-off:** 絕不 build/執行 clone 下來的程式碼（安全），所以拿不到 runtime/coverage 實測，只能靠靜態跡象推估 validation 強度。接受 — 執行未知程式碼風險太高。

---

## D-010 — Vein 整合走 CLI pipe，輸出 STRENGTH 報告

**Date:** 2026-06-02
**Decision:** Vein 整合走 CLI pipe。延續 D-002。（注：具體契約於讀 Vein code 後改為 slug + 分類 tags，見 D-011；不再輸出 STRENGTH.md 路徑給 Vein。）

**Why:** 維持 Unix pipe 鬆耦合，Vein 重構不影響 Seam。產品規劃（吸收 strengths → 新產品）留在 Vein，Seam 只丟種子。

**Trade-off:** 跨 process 有 overhead，且 Seam 不知道 Vein 有沒有真的吸收。可接受 — overnight batch 場景無所謂即時性。

---

## D-011 — 介面契約 = slug + 分類 tags（讀 Vein code 後定案）

**Date:** 2026-06-02
**Decision:** Seam 給 Vein 的單位是 `owner/repo` slug + 一串分類 tag（`lang:*`、`strength:*`、`seam-score:*`），由既有 `vein fetch` / watchlist 吃，Vein 不需改 code。

**Why:** 讀 vein code 發現 `vein fetch` 的 ingest 單位就是 slug，且本來就會 clone+ollama 抽 narrative。Seam 不該重做 narrative，只要補 Vein 缺的 discovery 與程式碼層分類。tag 讓 Vein 端可 `vein recall strength:tech_strong lang:rust`。

**Trade-off:** Seam clone 到 2T、Vein 又 clone 到 temp → **同一 repo 下載兩次**。Phase 3 接受（保零耦合）。選配解法：建議 Vein 加 `vein fetch --from-path`，但那要動 Vein，記成 Vein 端決策、非 Seam 阻塞。

---

## D-012 — 不做 cron：一次性 python entry + cowork schedule

**Date:** 2026-06-02
**Decision:** orchestration 是 `seam_harvest_entry.py`（跑一輪即結束），由 **cowork schedule** 觸發。可靠性語意（catch-up / self-check / in-progress 保守跳過 / 冪等）**參考 `/Users/lion/Documents/py/schedule_entry.py`**，但不抄常駐 `while True` scheduler loop，也不 import `task_runner.py`（跨專案耦合），改 Seam 本地最小重寫（zero deps）。

**Why:** Rex 指定不用 cron、由 cowork schedule 觸發。一次性 entry 比常駐 daemon 簡單、好測、好被外部排程器叫。schedule_entry 的 history-based 冪等與重試語意成熟，值得借（但只借語意）。

**Trade-off:** 重複實作一份精簡 history/catch-up，與 py 專案有概念重疊。接受 — 換來零跨專案 dependency，符合 Seam thin + Vein D-002 原則。

---

## D-013 — 規劃新產品是 Vein 的職責，Seam 不做 ideation

**Date:** 2026-06-02
**Decision:** 「吸收 open src 強項 → 規劃未來新產品」全程交給 Vein（`study compare` / debrief / morning brief）。Seam 只負責餵「對的 repo + 分類」。原規劃的 `vein_seeds` 欄位從 Seam 移除。

**Why:** Vein 已有跨 repo 合成與 brief 能力，那本就是 decision-lore archive 的價值所在。Seam 做 ideation 會越界、變胖，違反 thin 原則與關注點分離。

**Trade-off:** Seam 的 StrengthReport 少一個「點子」欄位，少一點即時靈感輸出。接受 — ideation 品質在 Vein（有更多上下文）會更好，且職責邊界乾淨。
