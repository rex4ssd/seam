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
