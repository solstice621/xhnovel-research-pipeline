# Experiment B — model-adjudicated geography reference result

- status: **FROZEN_MODEL_GOLD**
- gold_id: `GOLD-159D8DA0B6BFD77182AF`
- gold_hash: `sha256:c2ffd3415ef4e235a72c5fc09196a8686e3569f62b6dcdfe9504b8b93fc09522`
- sample: `GEOGOLD-B-20260904`
- protocol: `geography-model-reference/v2` / `dual-model-adjudication/v1`
- compiler: `sha256:a3f4ce33dceaa5e37770a62ea9ed1438c0c28b5ba979d2bda2e88590960b12af`
- review policy freeze commit: `7f359ea7a9ac30dcf168db6c59d965b025d42708`

This is a **model-adjudicated reference set**, not human gold. Source packets,
labels, occurrences, and unique JSONL remain runtime-only because they contain
rights-bound source spans. This document records freeze identity and
decision-relevant counts only.

## 1. Freeze identity

Fresh-process `validate-frozen` reproduced the frozen set (`PASS`).

| Artifact | Identity |
| --- | --- |
| gold manifest | `GOLD-159D8DA0B6BFD77182AF` / `sha256:c2ffd3415ef4e235a72c5fc09196a8686e3569f62b6dcdfe9504b8b93fc09522` |
| source packet set | `sha256:925b633c08d802c183f8962f45bdc5735c096770d2e2021a7c21a34af0ddd365` |
| input labels (HOST_AGENT draft) | `sha256:f9b2f6eb9bd5765903da4de6bb126e860af95818d2af156703fbcc90aa548f88` |
| final labels | `sha256:3cc8124d5e84f41638f39c7c4f37cd78a04606a0324b1daf7b6c336f79c270c8` |
| disputes | `sha256:52448b4dd05935e47e6d13fb9f0a48384eafad5892257a8e2a3e6f3fca0e6444` |
| occurrence JSONL | `sha256:2c48b8367549832d6533807e680bf218862e7df83475156b12260c5575879daf` |
| unique JSONL | `sha256:9942913087ba61889845b36e8cbf63f51661f9adef7e46edda7f46a58aaca923` |
| BLIND_EXTRACTOR output | `sha256:b0a6e01f2a23e542cc1323d5bcb1029f1b860254d077e17cba05ed43a4f45749` |
| DRAFT_AUDITOR output | `sha256:700f3a4999227e9e6ea4160c33476caa040ac74f7873d0c29ea186a1db72837d` |
| DIFFERENCE_ADJUDICATOR output | `sha256:2ef439371f9f2b6917ea9ab0a322e0767651d0a69741dba4adcb0c11fdda3fd4` |

Three isolated model receipts (`cursor` / `cursor-grok-4.6-high`) are bound in
the manifest. Forbidden inputs remain
`baseline_answers`, `candidate_answers`, `capacity_statistics`.

## 2. Compiled counts

| | n |
| ---: | ---: |
| units | 10 |
| labels | 1186 |
| INCLUDE / occurrences | 628 |
| EXCLUDE | 558 |
| unique payloads | 189 |
| disputes | 56 |
| unresolved disputes | 0 |

Dispute categories: INCLUSION 34, PAYLOAD 12, RELATION_SEMANTICS 7,
SOURCE_TEXT 2, EVIDENCE 1. Resolutions: EXCLUDED 30, INCLUDED 26.

## 3. Per-unit compiled occurrence vs unique

Stress/anchor units `5,310,395,426,513,596` and hash-selected controls
`102,233,467,604`. All-ten totals are diagnostic, not an unbiased whole-work
estimate.

| ordinal | cohort | occurrences | unique | PLACE_MENTION | SPATIAL_RELATION |
| ---: | --- | ---: | ---: | ---: | ---: |
| 5 | stress | 83 | 33 | 73 | 10 |
| 102 | control | 44 | 11 | 42 | 2 |
| 233 | control | 22 | 6 | 22 | 0 |
| 310 | stress | 93 | 27 | 87 | 6 |
| 395 | stress | 134 | 24 | 117 | 17 |
| 426 | stress | 78 | 36 | 67 | 11 |
| 467 | control | 38 | 13 | 38 | 0 |
| 513 | stress | 71 | 18 | 63 | 8 |
| 596 | stress | 49 | 16 | 47 | 2 |
| 604 | control | 16 | 5 | 16 | 0 |
| **stress** | | **508** | **154** | **454** | **54** |
| **control** | | **120** | **35** | **118** | **2** |
| **all10 diagnostic** | | **628** | **189** | **572** | **56** |

Dense stress units have far more occurrences than unique payloads (395:
134→24; 5: 83→33). Unit 596 is an organization-name storm: 49 included
occurrences, 16 unique payloads, and 157 EXCLUDE rows. That pattern is the
capacity hypothesis for Experiment C (duplicate-storm + 64-cap), not a quality
score.

## 4. Limitations

- The original 536-row annotation draft was not present in this checkout. The
  `DRAFT_AUDITOR` input is a fresh isolated HOST_AGENT source-only draft
  (`input_labels` above), not that missing draft.
- Isolation and model identity are operator attestations. The compiler verified
  prompt/output byte hashes and the three-role input-artifact sets; it cannot
  prove what a model observed.
- EXCLUDE rows are a difficult-negative aid, not an exhaustive negative census.
- Software cannot prove that parallel per-unit workers did not consult forbidden
  files; envelopes forbade blind/baseline/capacity inputs during audit, and
  forbade capacity/baseline inputs during adjudication.

## 5. Gate

Experiment C may now score 10k occurrence-like (A) versus 10k unique-fact +
completion (B) against this frozen lineage. Do not implement 5k or
relation-only unless C shows 10k unique-fact is insufficient.
