# Human audit — REQ-LIVE-RQ002

Date: 2026-08-29
Query sent: `青铜` (Wikipedia OpenSearch)
Budget: max_queries=1, max_fetches=1
Export: EXP-LIVE-RQ002
`export_hash`: sha256:e8c4ca5fe0a7e65f942a38187d3255a0de57f78053cbf0c79c3e699606680ae3

## Provider window (first hits)

| rank | title | selection |
|---|---|---|
| 1 | 青铜 | SELECTED |
| 2 | 青铜时代 | SELECTED |
| 3 | 青铜器修复及复制技艺 | REJECTED over fetch budget |
| 4 | 青铜骑士 | REJECTED over fetch budget |
| 5–10 | 青铜峡市 / 青铜器 / 青铜峡水库 / 青铜蛙 / 青铜峡事件 / 青铜葵花 | REJECTED over fetch budget |

None of these are an original-work scene of two embodied characters applying opposite control to a currently held object. Rank 1 full page is the encyclopedia article on the alloy.

## Parse replay

The frozen export used `parser-html-pdf-v0.1`. That build treated Vector `<input>` void tags as skip depth, so the ParseRun kept a single segment starting at「跳转到内容」.

Offline reparse of the **same** HTML Artifact with `parser-html-pdf-v0.1.1` yields encyclopedia body (title「青铜」, alloy/archaeology text). Still not a contested-take case. Segment hash of the v0.1.1 replay is stable across two runs on the same bytes.

## Verdict

`NO_QUALIFYING_CASE_FOUND` is correct. H-A stays UNKNOWN. No pseudo-claim.
