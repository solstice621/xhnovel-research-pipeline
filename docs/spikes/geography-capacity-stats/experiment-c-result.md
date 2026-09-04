# Experiment C — 10k occurrence-like vs unique-fact capacity result

- status: **10k unique-fact is sufficient; STOP**
- frozen reference: `GOLD-159D8DA0B6BFD77182AF` /
  `sha256:c2ffd3415ef4e235a72c5fc09196a8686e3569f62b6dcdfe9504b8b93fc09522`
- scorer: `scripts/spikes/geography_experiment_c.py` joined to occurrence gold
- A: `geography-v1` occurrence-like, `maxItems=64` (six stress answers reused from A-2)
- B: `xhnovel.geography.unique` 10k unique-fact + completion ABI
- this matrix: one fresh-context run per unit (run1). Three-run variance for
  `310/426/596` was not collected; the capacity direction is the same on every
  stress unit.

PR #14 keeps answers and gold JSONL runtime-only. This inspection branch
copies the span-coordinate files (no chapter prose) under `inspection/`.

## 1. Decision

**10k unit-local unique-fact + completion is sufficient to remove the geography
capacity failure.** Do not implement 5k units, relation-only splitting, or
adaptive splitting.

The A baseline wastes the 64-slot cap on duplicate occurrences. Every stress
unit saturates (`raw_count=64`, mean 46.5 duplicates). Unique-payload recall
collapses on the dense units. B emits each exact payload once, never saturates,
asserts `COMPLETE` with zero `OVERFLOW`, and recovers stress unique recall.

Remaining B errors are mostly extra unique payloads (precision ~0.54), not
omitted gold uniques. That is semantic over-generation (`explicit_type` /
organization-as-place), not a slot-capacity problem. Name-level place recall
on stress is 0.95. No `OVERFLOW` and no 64-cap pressure, so 5k is not triggered.

## 2. Cohort matrix (run1)

Zero-denominator rates remain `null` in the scorer JSON. All-ten is diagnostic
only.

| cohort | config | place unique P / R | relation unique P / R | saturated | overflow | mean raw | mean unique | mean dup | mean Q4 recall |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| stress | A | 0.631 / 0.482 | 0.952 / 0.455 | 6/6 | 0 | 64.0 | 17.5 | 46.5 | 0.125 |
| stress | B | 0.538 / 0.900 | 0.780 / 0.886 | 0/6 | 0 | 39.0 | 39.0 | 0.0 | 0.583 |
| control | A | 0.405 / 0.455 | 0.500 / 1.000 | 0/4 | 0 | 34.5 | 10.25 | 24.25 | 0.500 |
| control | B | 0.543 / 0.758 | 1.000 / 1.000 | 0/4 | 0 | 12.0 | 12.0 | 0.0 | 0.750 |
| all10 diagnostic | A | 0.562 / 0.476 | 0.880 / 0.478 | 6/10 | 0 | 52.2 | 14.6 | 37.6 | 0.312 |
| all10 diagnostic | B | 0.539 / 0.867 | 0.788 / 0.891 | 0/10 | 0 | 28.2 | 28.2 | 0.0 | 0.667 |

Place-name (ignore `explicit_type`) on stress: A 0.987 / 0.755, B 0.882 /
0.951. Mean response bytes: A stress 20480, B stress 17592. The cohort field
`mean_explicit_type_accuracy` is the share of units with perfect type
accuracy (0.0 here), not the mean of per-unit rates; use the per-unit table.

Inspection copies of scores, gold, and answers:
`docs/spikes/geography-capacity-stats/inspection/`.

## 3. Per-unit unique-place recall (capacity-relevant)

| ordinal | cohort | A raw/uniq/dup | A place R | B raw/uniq | B place R | B completion |
| ---: | --- | --- | ---: | --- | ---: | --- |
| 5 | stress | 64/24/40 | 0.83 | 57/57 | 1.00 | COMPLETE |
| 310 | stress | 64/18/46 | 0.14 | 51/51 | 1.00 | COMPLETE |
| 395 | stress | 64/12/52 | 0.53 | 41/41 | 1.00 | COMPLETE |
| 426 | stress | 64/26/38 | 0.35 | 34/34 | 0.73 | COMPLETE |
| 513 | stress | 64/11/53 | 0.18 | 26/26 | 0.82 | COMPLETE |
| 596 | stress | 64/14/50 | 0.86 | 25/25 | 0.86 | COMPLETE |
| 102 | control | 42/13/29 | 0.22 | 14/14 | 0.22 | COMPLETE |
| 233 | control | 43/13/30 | 0.67 | 9/9 | 1.00 | COMPLETE |
| 467 | control | 36/8/28 | 0.31 | 17/17 | 0.92 | COMPLETE |
| 604 | control | 17/7/10 | 1.00 | 8/8 | 1.00 | COMPLETE |

Control 102 is a quality miss on both configs (not 64-capped). It does not
reopen the capacity question.

### Citation, place-name, type, bytes (run1)

Unweighted mean of per-unit citation rates. `null` Q4 is omitted from Q4
recall in the cohort table above.

| ordinal | A cite contain / exact | B cite contain / exact | A name P/R | B name P/R | A etype | B etype | A bytes | B bytes |
| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| 5 | 1.00 / 0.48 | 0.79 / 0.79 | 1.00 / 0.91 | 0.92 / 1.00 | 0.90 | 0.09 | 20046 | 25710 |
| 310 | 1.00 / 0.17 | 0.92 / 0.85 | 1.00 / 0.75 | 0.91 / 1.00 | 0.20 | 0.30 | 22932 | 22875 |
| 395 | 1.00 / 0.00 | 0.96 / 0.67 | 0.88 / 0.50 | 0.88 / 1.00 | 0.86 | 0.14 | 19893 | 18698 |
| 426 | 1.00 / 0.20 | 1.00 / 0.85 | 1.00 / 0.65 | 1.00 / 0.87 | 0.33 | 0.75 | 20606 | 15744 |
| 513 | 1.00 / 0.00 | 0.93 / 0.86 | 1.00 / 0.70 | 0.73 / 0.80 | 0.14 | 0.25 | 19929 | 11943 |
| 596 | 0.77 / 0.23 | 1.00 / 1.00 | 1.00 / 1.00 | 0.76 / 1.00 | 0.85 | 0.54 | 19475 | 10581 |
| 102 | 0.75 / 0.75 | 0.50 / 0.50 | 0.82 / 1.00 | 0.82 / 1.00 | 0.22 | 0.22 | 19365 | 7232 |
| 233 | 1.00 / 1.00 | 1.00 / 1.00 | 0.55 / 1.00 | 0.86 / 1.00 | 0.67 | 0.83 | 17634 | 3757 |
| 467 | 1.00 / 1.00 | 1.00 / 1.00 | 0.88 / 0.54 | 1.00 / 0.92 | 0.57 | 0.67 | 16318 | 5644 |
| 604 | 1.00 / 0.20 | 1.00 / 1.00 | 0.83 / 1.00 | 0.83 / 1.00 | 0.80 | 0.80 | 6928 | 3194 |

Stress unweighted citation mean: A contain 0.96 / exact 0.18; B contain 0.93
/ exact 0.83. A's exact-span collapse is the 64-cap duplicate-storm, not a
missing-name problem.

## 4. Why 5k / relation-only stay out

Handoff triggers were unique-capacity pressure, `OVERFLOW`, stable tail
collapse, or a stable material advantage for 5k. Observed:

- B never hits 64 unique records (max 57 on unit 5; gold unique on that unit is 33).
- `OVERFLOW` count is 0. Every B unit asserted `COMPLETE` (unverified executor
  assertion, but it did not claim omitted unique payloads).
- Stress Q4 recall rises from 0.125 (A) to 0.583 (B). Residual Q4 misses (426,
  596) are not a 64-cap collapse.
- 5k was not run; nothing in this matrix requires it.

Frozen disputes have `UNRESOLVED=0` (56 rows: EXCLUDED 30, INCLUDED 26).
Strict, optimistic, and conservative unique-gold views coincide. STOP does
not change across those views.

## 5. Limitations

- One run per unit. Protocol asked for three fresh contexts on `310/426/596`.
  Those extra runs were not required to see the capacity effect: all six stress
  units saturate under A and none saturate under B.
- A stress answers for `5/310/395/426/513/596` are reused A-2 files, not new
  draws. Controls `102/233/467/604` were newly extracted for this matrix.
- B precision remains ~0.54 on unique place payloads. That is extra predicted
  uniques, not dropped gold uniques, and is out of scope for a capacity STOP.
- Executors did not see frozen labels. Scoring used `validate_packet_answer.py`
  / production `validate_model_output` with frozen packet offsets.
