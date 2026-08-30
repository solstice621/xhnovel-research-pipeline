# Incident guide

| Symptom | Action |
|---|---|
| export hash mismatch | treat as tamper; do not import |
| artifact MISSING/CORRUPT | restore from replica; mark DEGRADED if unrecoverable |
| extractor prompt/model change | invalidate build; do not rewrite historical exports |
| qualification basis invalid | REVOKED, not STALE |
| provider schema drift | fail the SearchRun; keep raw JSON artifact |
| SSRF/path escape | fail closed; do not fetch |

Audit: `explain-claim`, `trace-request`, `verify-export`, `check-artifact`, `diff-bundle`.
