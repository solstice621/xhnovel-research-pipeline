# Geography capacity — reviewer guide

Start here. This is the ordered review for Experiment B freeze + Experiment C
STOP. It is experimental-grade, not forensic-grade.

**Do not merge** `cursor/geography-capacity-inspection-f136` to `main`.
Code/ABI review lives on [PR #14](https://github.com/solstice621/xhnovel-research-pipeline/pull/14).
This kit is the evidence overlay.

Classify findings as **blocker** or **non-blocker**. For each finding give:

- exact file/function or artifact;
- a concrete failing scenario;
- the violated invariant;
- the smallest safe correction.

Do not reopen frozen architecture merely because another design is also
reasonable. Reopen it only when implementation evidence shows a contradiction,
a security/correctness hole, or an impossible acceptance condition.

## 1. Questions this review answers

1. **STOP:** Is 10k unit-local unique-fact + completion enough to remove the
   geography 64-slot capacity failure, so 5k / relation-only stay unimplemented?
2. **ABI:** Does `geography-unique-v1` keep `geography-v1` extraction hash
   unchanged, enforce unique payloads, and require
   `COMPLETE`/`OVERFLOW`/`UNCERTAIN`?
3. **Reference:** Is `GOLD-6F9623B825F387835B61` a closed model-adjudicated
   freeze (not human gold), with hashes that match the copied files?

A GitHub-only reviewer can answer (1) from scores + payload-diff, (2) from
PR #14, and (3) as identity closure. Span-vs-chapter checks need authorized
source packets, which this kit does not contain.

## 2. Reading order

| Step | Read | Why |
| ---: | --- | --- |
| 1 | this file | scope and overturn conditions |
| 2 | `../experiment-c-result.md` | claimed STOP matrix |
| 3 | `../experiment-b-result.md` | freeze identity and compiled counts |
| 4 | `README.md` | kit layout, hashes, replay command |
| 5 | `../experiment-b-sample.json` | ten units, stress vs control |
| 6 | `../experiment-b-plan.md` §§7–8 | required metrics and C triggers |
| 7 | PR #14 unique ABI | `profiles/generic/geography-unique-v1/`, `generic_extraction.py`, `generic_profile.py` |

`../experiment-b-plan.md` is the frozen pre-execution protocol snapshot; its
pending checklist intentionally preserves that point in time. Execution
closure is recorded in `../experiment-b-result.md` and
`frozen/gold-manifest.json`.

`../experiment-b-review-handoff.md` is **historical**. It records the
protocol-correction review (`88cd133` era). The banner at the top is the
current gate. Do not treat its section 6 “C–G blocked” paragraph as live.

## 3. Track A — GitHub-only (this kit)

Work on this branch. No `.runtime` and no chapter text required.

### 3.1 Replay C scores

```bash
python3 scripts/spikes/geography_experiment_c.py \
  --sample docs/spikes/geography-capacity-stats/experiment-b-sample.json \
  --unique docs/spikes/geography-capacity-stats/inspection/frozen/unique.jsonl \
  --occurrences docs/spikes/geography-capacity-stats/inspection/frozen/occurrences.jsonl \
  --answers-dir docs/spikes/geography-capacity-stats/inspection/experiment-c/answers/B \
  --output /tmp/B-run1.json

python3 scripts/spikes/geography_experiment_c.py \
  --sample docs/spikes/geography-capacity-stats/experiment-b-sample.json \
  --unique docs/spikes/geography-capacity-stats/inspection/frozen/unique.jsonl \
  --occurrences docs/spikes/geography-capacity-stats/inspection/frozen/occurrences.jsonl \
  --answers-dir docs/spikes/geography-capacity-stats/inspection/experiment-c/answers/A \
  --output /tmp/A-run1.json
```

`/tmp/{A,B}-run1.json` must match `experiment-c/scores/{A,B}-run1.json`.
Mismatch is a blocker for the published rates.

Also confirm copied freeze bytes:

```bash
sha256sum \
  docs/spikes/geography-capacity-stats/inspection/frozen/unique.jsonl \
  docs/spikes/geography-capacity-stats/inspection/frozen/occurrences.jsonl \
  docs/spikes/geography-capacity-stats/inspection/review/labels-final.jsonl \
  docs/spikes/geography-capacity-stats/inspection/review/disputes.jsonl
```

Those four must equal the table in `README.md` / `experiment-b-result.md`.

### 3.2 Capacity claim (question 1)

From `experiment-c/scores/` and `experiment-c-result.md`, check:

| Check | Expected if STOP holds |
| --- | --- |
| A stress `raw_count` | 64 on all six stress units |
| A stress duplicates | high (mean 46.5); unique recall collapses on dense units |
| B stress `raw_count` | all `< 64` (max 57 on unit 5) |
| B duplicates | 0 |
| B `OVERFLOW` | 0; every unit `COMPLETE` |
| B stress place unique recall | 0.90 vs A 0.48 |
| Control 102 | both configs place unique R=0.22 (quality miss, not cap) |

`COMPLETE` is an unverified executor assertion. It may not be used as proof of
semantic completeness. `OVERFLOW=0` plus `raw<64` is the machine-readable
capacity witness.

Inspect extras, not just rates: `experiment-c/payload-diff.json` config `B`.
STOP requires that B misses are not a 64-slot omission pattern. Extra unique
payloads (precision ~0.54) are in scope as quality, not as a 5k trigger.

Cohorts: stress may decide capacity; controls bound general quality; all-ten
is diagnostic only.

### 3.3 Freeze identity (question 3)

In `frozen/gold-manifest.json`:

- `state=FROZEN_MODEL_GOLD`, `review_state=MODEL_ADJUDICATED`;
- three `model_reviews` with distinct input-artifact sets
  (blind = packets only; auditor = packets + draft; adjudicator = packets +
  both outputs);
- `forbidden_inputs` includes `baseline_answers`, `candidate_answers`,
  `capacity_statistics`;
- `disputed_count=56` and `review/disputes.jsonl` has 56 rows,
  `resolution` never `UNRESOLVED`.
- every `candidate_label_artifact_ids` entry exists in the bound input/final
  labels and belongs to the dispute's own `unit_id`; one valid ID must not mask
  an unknown or cross-unit ID.

The last condition is enforced by
`geography_gold.py::_validate_input_labels` and `_validate_disputes`. Its two
negative tests cover cross-unit-only and valid-plus-unknown candidate lists.
The old `GOLD-159D8DA0B6BFD77182AF` identity is superseded because its compiler
only required a nonempty global-set intersection.

Because `UNRESOLVED=0`, strict / optimistic / conservative unique-gold views
coincide. If a reviewer finds an unresolved dispute, STOP must be re-scored
under all three views.

Do not treat this set as human gold. Wording that calls it `HUMAN_ACCEPTED`
or a quality score for the whole novel is a blocker.

### 3.4 Unique ABI (question 2)

On PR #14 / this branch:

- `profiles/generic/geography-v1/` extraction hash must remain
  `sha256:d7256c57bc4668a77d6b98912e66745bdcdda3ce2f6a305808d0a8bceb78e671`;
- `geography-unique-v1` has `record_mode=UNIQUE_PAYLOAD` and
  `completion_required=true`;
- duplicate canonical payload → `E-GENERIC-UNIQUE-PAYLOAD`;
- missing/invalid completion envelope rejected when required;
- tests: `tests/test_geography_unique_abi.py`,
  `tests/test_geography_experiment_c.py`.

### 3.5 Optional ABI/tooling tests

```bash
python3 scripts/sync_skills.py --check
python3 -m pytest tests/test_geography_unique_abi.py tests/test_geography_experiment_c.py tests/test_geography_gold_spike.py -q
```

GitHub Actions on this repo currently fail before runner start (billing).
A local green run is not a cross-platform CI claim.

## 4. Track B — authorized source (not in this kit)

Only if the reviewer has the rights-declared snapshot and source packets
(`source_packet_set_hash=sha256:925b633c08d802c183f8962f45bdc5735c096770d2e2021a7c21a34af0ddd365`).

Then, and only then:

- `python3 scripts/spikes/geography_gold.py validate-frozen` in a fresh
  process;
- spot-check that INCLUDE spans cover the payload string in packet text;
- confirm packets were not fed baseline/capacity files (operator attestation
  plus isolation layout; software cannot prove what a model observed).

Absence of Track B does not by itself overturn STOP. It limits how far gold
*quality* can be certified.

## 5. What overturns STOP (blockers)

Any one of:

- B stress unit with `raw_count>=64` or `OVERFLOW`;
- B unique-payload recall on stress collapsing to A-like levels for a
  slot-exhaustion reason;
- replayed scores disagree with `experiment-c/scores/`;
- freeze hashes disagree with the copied JSONL;
- `geography-v1` extraction hash changed, or unique ABI does not actually
  reject duplicates / missing completion;
- an unresolved dispute set that changes the capacity decision across
  strict/optimistic/conservative views.

## 6. Already disclosed — not new blockers unless they change STOP

- One run per unit; protocol asked for three fresh contexts on `310/426/596`.
- A stress answers reused from A-2; controls newly extracted.
- B place unique precision ~0.54 (over-generation, not cap).
- Model-adjudicated reference, not human gold; original 536 draft was absent,
  auditor input is a fresh HOST_AGENT source-only draft.
- Isolation/model identity are operator attestations.
- Cited-character broadness is not a separate published rate (containment and
  exact-span are).
- Remote CI is a billing gate, same as `main` since PR #13.
- The refreeze changed only compiler/sample/manifest identity. All 56 disputes
  passed the stronger unit-local closure check; occurrence, unique, and A/B
  score bytes are unchanged.

## 7. Finding format

```text
[blocker|non-blocker]
file/function/artifact:
scenario:
violated invariant:
smallest safe correction:
```

Priority order: STOP overturn conditions, unique ABI hash/envelope, freeze
identity closure, then gold-label disagreements (Track B).
