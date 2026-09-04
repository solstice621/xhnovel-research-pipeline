# Geography capacity / completion ABI — review handoff

## 1. Outcome and stage boundary

This branch completes the protocol, sample, validation tooling, and test harness
needed to create a blind human geography gold set. It does **not** claim that the
gold set is frozen, and it deliberately does not contain the production
extraction changes or Experiment C results that depend on that gold.

Current state:

| Requested stage | State | Evidence / reason |
| --- | --- | --- |
| A. Freeze Experiment B protocol | Complete | Frozen plan, ten-unit sample, four strict schemas, deterministic control selection and metric definitions |
| B. Produce human gold | `ANNOTATION_DRAFT` | Ten source-only packets, host-agent draft, independent source-only QA, and deterministic derived artifacts exist locally; independent human acceptance is still required |
| C. Minimal production changes | Not started | Frozen protocol prohibits using Experiment B as a gate before `FROZEN_GOLD` |
| D. Production regression tests | Not started | Depends on C; the branch contains strong tests for the protocol/tooling only |
| E. Experiment C execution | Not started | Depends on frozen gold and C/D |
| F. Score against frozen gold | Not started | No human-accepted gold exists yet |
| G. Final capacity decision | Not started | Would be unsupported before E/F |

This stop is intentional. Agent-produced labels remain unverified draft data
even after a second source-only QA pass.

## 2. Review lineage

- implementation baseline: `6cc794fd6ad2632f1d26d00a9bf027634617c751`
- supporting Experiment A evidence branch: `f37bbf8`
- review branch: `codex/geography-capacity-abi-gold`
- review range: `6cc794fd6ad2632f1d26d00a9bf027634617c751..HEAD`
- commits before this handoff report:
  - `f426754` — freeze Experiment B protocol
  - `bcd3e7d` — close the gold freeze contract with schemas, tooling, and tests
  - `a6bb00a` — record the annotation-draft human gate

The worktree also contains an unrelated user-owned untracked file,
`docs/PHASE_MINUS1_PLAN.md`. It is not part of this branch or review range.

## 3. Committed deliverables

### Protocol and contracts

- `experiment-b-plan.md`
  - freezes the decision boundary, blind annotation rules, inclusion/exclusion
    policy, unique-fact identity, metrics, and Experiment C matrix;
  - distinguishes `ANNOTATION_DRAFT`, `HUMAN_ACCEPTED`, and `FROZEN_GOLD`;
  - forbids production extraction changes based on draft annotations.
- `experiment-b-sample.json`
  - binds exactly ten 10,000-character units;
  - contains six required stress/anchor units and four controls selected by a
    frozen hash rule that reads neither source text nor model answers;
  - binds snapshot, work, ingestion, input-spec, unit, native semantic-task,
    executor-request, source-packet, and source-text identities.
- `geography-gold-label.schema.json`
  - strict `INCLUDE`/`EXCLUDE` input rows with exact evidence bindings.
- `geography-gold-annotation.schema.json`
  - strict compiled occurrence rows with deterministic identity and position.
- `geography-gold-review.schema.json`
  - explicit human review state and per-unit completion receipt.
- `geography-gold-manifest.schema.json`
  - content-bound closure over the sample, packets, labels, review,
    occurrences, and unique-payload output.

### Tooling

- `scripts/spikes/geography_gold.py`
  - `prepare`: reconstructs source-only packets from frozen native tasks and the
    qualified snapshot while validating all lineage and hashes;
  - `merge-drafts`: combines isolated annotator drafts without promoting them;
  - `validate`: validates labels and review receipts fail-closed;
  - `derive`: deterministically compiles included occurrences and unit-local
    exact-payload unique facts;
  - `freeze`: permits the state transition only from a complete
    `HUMAN_ACCEPTED` receipt and writes all three final outputs atomically;
  - `validate-frozen`: replays a frozen gold set in a fresh process and requires
    byte-for-byte agreement.
- `scripts/spikes/geography_capacity_stats.py`
  - independently replays Experiment A geography counts;
  - distinguishes raw occurrences, unit-local exact-payload uniqueness, and
    global exact-payload uniqueness;
  - treats completion fields only as `UNVERIFIED_EXECUTOR_ASSERTION`;
  - reports malformed/duplicate cue blocks and missing answer artifacts rather
    than silently treating them as evidence.

### Tests

- `tests/test_geography_gold_spike.py`
  - covers packet preparation and tampering, strict schemas, payload/evidence
    support closure, unit bounds, required relation binding groups, duplicate
    occurrence rejection, deterministic derivation, review completeness,
    freeze atomicity, manifest closure, and fresh-process replay.
- `tests/test_geography_capacity_stats.py`
  - covers exact-payload counting, kind splits, executor-assertion isolation,
    response sizing, warning behavior, deterministic output, and strict checks.

## 4. Frozen sample and lineage

The ten sample ordinals are:

```text
stress / anchor: 5, 310, 395, 426, 513, 596
hash-selected controls: 102, 233, 467, 604
```

The qualified source lineage is:

```text
text snapshot id: NTS-8A3D80CA9182B92E8368
text snapshot hash: sha256:5f1b8b15d4a17623b4ff2e72a7517c10897dfbb0c8da2c6c03845d10bec39959
work id: NWK-F56E9D349D7D1368568B
ingestion run id: NING-DDAAA77E9454E04BFBCF
input spec artifact: sha256:fb6f18a74d565ea5d782468b918ca4290d4db783426d4a9574ff846d9c5cb1c6
input spec hash: sha256:e1899ea0ad2ff99fb31cb888856376a68ce123b7560a39688392245b0b9f8166
```

The sample deliberately distinguishes the native semantic-task artifact from
the on-disk agent-request artifact. The tooling independently reconstructs and
validates both identities instead of relabeling one as the other.

## 5. Draft annotation evidence — not committed gold

Rights-bound source packets and annotation artifacts remain under `.runtime/`
and are not committed. The current independently QA'd draft is:

```text
labels-draft-v2.jsonl
  rows: 536 (464 INCLUDE, 72 EXCLUDE)
  artifact: sha256:69f9dc3f9f3301b12bdc2cbd1dc2a06909f0450cc9a57a8d09ece53f200cc77b

occurrences-draft-v2.jsonl
  rows: 464
  artifact: sha256:a6b126b44cf5ee6fa1d45e25bb6aba1c1b513c3c7801fdebf46c2cf848efa319

unique-draft-v2.jsonl
  rows: 148
  artifact: sha256:c7869c5a1a609822ee85a8058b5a7beb5d79095296faa6a1a5a7c9db7e6984c1
```

All ten units have source-only annotation coverage. Annotators and the QA pass
were isolated from baseline/candidate answers, statistics, checkpoints, and
candidate files. Eight semantic disagreements remain explicitly surfaced for
human adjudication, including organization/site metonymy, spatial versus
political containment, local antecedents, source typos, and destination versus
organization usage. The human reviewer must inspect every row and packet, not
only those eight cases.

The original task handoff contains aggregate/model-report information. A person
who has read it is therefore not strictly report-blind. The frozen protocol
requires an independent reviewer who has not seen that material, unless the
protocol itself is explicitly revised and re-frozen first.

The current draft receipt was tested against `freeze`; it fails with
`E-GOLD-NOT-ACCEPTED` and writes no frozen outputs.

## 6. Independently replayed Experiment A-2 capacity facts

These are diagnostic baseline counts, not gold-scored quality results:

| Measure | Count |
| --- | ---: |
| Raw geography rows | 703 |
| Sum of unit-local exact-payload unique rows | 179 |
| Global exact-payload unique rows | 138 |
| Unit-local duplicates | 524 |
| Raw `PLACE_MENTION` rows | 669 |
| Unit-local unique `PLACE_MENTION` rows | 145 |
| Global unique `PLACE_MENTION` rows | 104 |
| Raw `SPATIAL_RELATION` rows | 34 |
| Unit-local/global unique `SPATIAL_RELATION` rows | 34 |
| Response bytes | 223,369 |

The replay also reports duplicate `geo_cues` blocks and missing sample answers;
strict `--check` therefore exits non-zero. Completion values are isolated as
executor assertions and are not presented as semantic completeness evidence.

## 7. Validation performed

At the report-preparation HEAD, the local full suite completed with:

```text
570 passed in 209.47s
```

The required repository checks run during this stage also passed:

```bash
python3 scripts/sync_skills.py --check
python3 -m pytest
python3 -m compileall -q src tests scripts
git diff --check
python3 -m build --wheel
```

The final report commit changes documentation only. No Ubuntu/Windows CI result
is claimed for this branch, and the branch does not claim completion of a
cross-platform production stage.

## 8. Requested adversarial review

Review the exact range in section 2. Classify findings as blocker or
non-blocker, and for each finding provide an exact file/function, reproducible
scenario, violated invariant, and smallest safe correction.

Focus on:

1. whether sample construction can consult text, heuristic scores, or answers;
2. whether every source packet and task identity is reconstructed from qualified
   content-bound artifacts;
3. whether strict schemas and code reject unknown/tampered input consistently;
4. whether every named payload value and required relation group has exact local
   support;
5. whether occurrence identity and unit-local exact-payload derivation are
   deterministic and avoid semantic/alias merging;
6. whether a draft or incomplete review can cause any frozen output side effect;
7. whether the manifest and fresh-process replay close over every final byte;
8. whether capacity statistics keep executor claims separate from verified
   evidence;
9. whether the tests exercise the production primitives rather than duplicate a
   weaker validation implementation;
10. whether any committed change crosses the frozen human-gold gate or weakens
    the repository trust boundaries.

## 9. Next authorized transition

After an independent human produces canonical `labels-human.jsonl` and a
complete `HUMAN_ACCEPTED` `review-human.json`, the next sequence is:

1. validate human labels and the review receipt without draft allowance;
2. freeze `occurrences.jsonl`, `unique.jsonl`, and `gold-manifest.json`;
3. run `validate-frozen` in a fresh process;
4. only then implement C/D, execute Experiment C, score against frozen gold,
   and write the actual capacity decision report.

