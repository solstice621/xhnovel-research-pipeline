# Experiment A-1/A-2 summary — stratified dense-window baseline (engine 6cc794f)

Runtime artifacts copied verbatim from `.runtime/generic-geography/doupo-v1-44966c9/`.
Not part of the repository source of truth.

## A-1 sampling (zero model calls)

- All 676 units scored with a geography-cue heuristic (selection only, never extraction results); seed 20260904.
- Sample: 4 ordinal strata x (3 random + 3 heuristic-dense) + known-dense anchor ordinal 5 = 25 units.
- Finding: cue density is NOT front-loaded; top-20 windows all sit in chapters 362-1548.
- Calibration caveat: ordinal 5 (real ~83 candidates) scores only 11.2 — heuristic misses real semantic density, so random controls are mandatory.
- File: `experiment-sample.json`.

## A-2 baseline execution (config A, current ABI, agent-files executor)

13 units executed (12 stratified + anchor), one fresh subagent context per unit, strict
quote->span tooling, then native `xhnovel-extract run` validation. Native checkpoint:
**13 completed, 0 failed** (build XBLD-04EEF485082B283CE18B). Raw answers preserved
under `answers/`.

| ordinal | emitted | unique payloads | distinct names | identified occurrences | omitted mentions |
|--------:|--------:|----------------:|---------------:|-----------------------:|-----------------:|
| 5       | 64      | 24              | 20             | 75                     | 11               |
| 151     | 54      | 6               | 5              | 54                     | 0                |
| 156     | 51      | 18              | 14             | 51                     | 0                |
| 163     | 55      | 10              | 8              | 55                     | 0                |
| 290     | 50      | 18              | 13             | 50                     | 0                |
| 301     | 17      | 4               | 4              | 17                     | 0                |
| 310     | 64      | 18              | 15             | 68                     | 4                |
| 395     | 64      | 12              | 8              | 82                     | 18               |
| 426     | 64      | 26              | 15             | 69                     | 5                |
| 438     | 64      | 10              | 7              | unknown (report lost)  | unknown          |
| 513     | 64      | 11              | 7              | 64                     | 0                |
| 596     | 64      | 14              | 13             | 185                    | 121              |
| 600     | 28      | 8               | 8              | 28                     | 0                |

## Headline

- **Raw saturation is real and book-wide**: 7/13 units emitted at the 64-record cap;
  5 units confirmed identified > 64, max 185 (ordinal 596).
- **Unique payload capacity has huge headroom**: p50 = 12, max = 26 (distinct-name max 20)
  — far below 64 everywhere. Under consolidation all 13 units fit in <= 26 records with
  zero distinct-fact loss (per executor reports, not yet gold-verified).
- **Relations**: 34 emitted, 0 omitted (relations-first prioritization held everywhere).
- The prefix-only pilot conclusion now holds on a stratified full-book sample.

## Executor-reported data caveats

- `identified` / `omitted` counts come from executor final messages (subagent reports),
  not machine artifacts; ordinal 438's report was lost in transport. This is direct
  evidence that overflow manifests must live in the answer ABI, not in session messages.
- "All omitted occurrences are duplicates" is executor-reported; Experiment B (human gold
  on source text) is still required before freezing the Profile ABI.

## Engineering findings during the run

1. Native validation caught real contract violations: 3 answers rejected with
   `E-GENERIC-EVIDENCE-MISSING: payload field /explicit_type has no evidence binding`;
   rebuilt with binding paths `["/name", "/explicit_type"]` and quotes containing the
   type word.
2. The task ABI does not define how the executor obtains unit text; tooling reconstructed
   it from the ingestion catalog with per-segment sha256 verification. Second confirmed
   ABI gap (after overflow manifest).
3. Host-agent operational reliability: one unit returned empty results twice (426) and
   needed manual resume; one final report was lost (438). Checkpoint/resume worked as
   designed.

## Next

Experiment B: human gold on source text for ordinals 596 / 395 / 426 / 5 / 513 + 2 random
controls. Then Experiment C comparison matrix (requires the minimal dual-capacity +
consolidation change).
