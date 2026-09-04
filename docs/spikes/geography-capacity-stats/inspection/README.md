# Geography capacity inspection kit

**Do not merge this overlay to `main`.** It is a review-only dump of
Experiment B/C artifacts that PR #14 left runtime-only.

This branch is `cursor/geography-capacity-inspection-f136`, stacked on the
Experiment C STOP commit. Source packets (`untrusted_text`) stay out.

## What a reviewer can do here

- Replay Experiment C scores from committed files (payload P/R, citation,
  saturation, completion).
- Inspect B extras/misses without opening `.runtime`.
- Inspect the three-pass review chain (prompts, role outputs, disputes,
  final labels) and the freeze identity in `frozen/gold-manifest.json`.
- See protocol metrics that the short C report omitted (citation,
  place-name, `explicit_type`, response bytes).

## What remains impossible without authorized source

- `validate-frozen` against source packets.
- Checking that a span actually covers the cited name in chapter text.
- Treating model isolation as more than an operator attestation.

## Rights / contents

| Included | Contains |
| --- | --- |
| unique / labels / answers / occurrences | toponyms and numeric `source_spans` (offsets + hashes, no chapter prose) |
| `review/disputes.jsonl` | short source excerpts inside `note` |
| scores / gold-manifest / review-model | counts and hashes only |
| source packets | **not included** |

## Freeze identity

These copied files match the Experiment B freeze table:

| File | Identity |
| --- | --- |
| `frozen/unique.jsonl` | `sha256:9942913087ba61889845b36e8cbf63f51661f9adef7e46edda7f46a58aaca923` |
| `frozen/occurrences.jsonl` | `sha256:2c48b8367549832d6533807e680bf218862e7df83475156b12260c5575879daf` |
| `review/labels-final.jsonl` | `sha256:3cc8124d5e84f41638f39c7c4f37cd78a04606a0324b1daf7b6c336f79c270c8` |
| `review/input-labels.jsonl` | `sha256:f9b2f6eb9bd5765903da4de6bb126e860af95818d2af156703fbcc90aa548f88` |
| `review/disputes.jsonl` | `sha256:52448b4dd05935e47e6d13fb9f0a48384eafad5892257a8e2a3e6f3fca0e6444` |

`frozen/gold-manifest.json` file bytes are
`sha256:0d0c909eb6225da23907aef74862ea478a3d9fc5aeef2c5c0d710afd8ed68a50`.
The logical freeze id inside the file is `GOLD-159D8DA0B6BFD77182AF` /
`gold_hash=sha256:c2ffd3415ef4e235a72c5fc09196a8686e3569f62b6dcdfe9504b8b93fc09522`.

Byte hashes of every file in this directory: `MANIFEST.sha256`.

## Replay Experiment C scores

From the repository root on this branch:

```bash
python3 scripts/spikes/geography_experiment_c.py \
  --sample docs/spikes/geography-capacity-stats/experiment-b-sample.json \
  --unique docs/spikes/geography-capacity-stats/inspection/frozen/unique.jsonl \
  --occurrences docs/spikes/geography-capacity-stats/inspection/frozen/occurrences.jsonl \
  --answers-dir docs/spikes/geography-capacity-stats/inspection/experiment-c/answers/B \
  --output /tmp/B-run1.json
```

Same command with `answers/A` for the occurrence-like baseline. The output
must match `experiment-c/scores/{A,B}-run1.json`.

## Layout

```text
frozen/            gold manifest, unique JSONL, occurrence JSONL
review/            three-pass prompts, role outputs, disputes, labels
experiment-c/
  scores/          committed scorer JSON
  answers/{A,B}/   run1 executor answers (offsets, no chapter prose)
  payload-diff.json
  C-worker.md
```

`payload-diff.json` lists extra and missing unique payloads per unit so the
B precision ~0.54 claim can be inspected without a scorer.

## Dispute views

Frozen disputes: 56 rows, `UNRESOLVED=0` (EXCLUDED 30, INCLUDED 26).
Strict / optimistic / conservative unique-gold views therefore coincide.
The Experiment C STOP does not change across those views.

## Still not in this kit

- Source packets and authorized chapter text.
- Three fresh runs on ordinals `310/426/596`.
- 5k / relation-only answers (not run; STOP does not trigger them).
