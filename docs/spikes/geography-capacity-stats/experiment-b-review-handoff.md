# Geography model-reference freeze — remediation review handoff

> **Status 2026-09-04.** This document is the protocol-correction handoff.
> It is not the current experiment gate. Experiment B is
> `FROZEN_MODEL_GOLD` (`GOLD-6F9623B825F387835B61`); Experiment C STOP'd
> 10k unique-fact. See `experiment-b-result.md`, `experiment-c-result.md`,
> and the review-only kit in `inspection/README.md`.

## 1. Decision

The `6cc794f..88cd133` review correctly identified two blockers and four
reproducibility/methodology issues. This follow-up fixes the committed protocol,
schemas, compiler, sample closure, and tests. It does **not** promote the 536
draft labels or claim that Experiment B is frozen.

Current gate:

```text
protocol/tooling: corrected and locally verified
semantic labels: ANNOTATION_DRAFT
model adjudication: not yet executed
frozen reference: absent
C–G: not started
```

## 2. Review lineage

- implementation baseline: `6cc794fd6ad2632f1d26d00a9bf027634617c751`
- original reviewed head: `88cd133e2f775ecdaa299560445faffcb1d7cfef`
- corrected protocol freeze: `7f359ea7a9ac30dcf168db6c59d965b025d42708`
- correction implementation: `5bf21a3`
- branch: `codex/geography-capacity-abi-gold`
- follow-up review range: `88cd133e2f775ecdaa299560445faffcb1d7cfef..HEAD`

The unrelated user-owned untracked file `docs/PHASE_MINUS1_PLAN.md` remains
outside every commit.

## 3. Finding disposition

### Blocker 1 — multi-file freeze was not atomic

Accepted. The old report's claim of an atomic three-file write was false.

The corrected contract uses the manifest as the only commit marker:

1. compute and schema-validate every final byte before writing;
2. require all three targets to be absent, or all three to exist with identical
   bytes;
3. reject a mixed existing/missing set with `E-GOLD-PARTIAL` before writing;
4. write occurrences and unique rows first, then the manifest last;
5. if any in-process write fails, remove every file created by that invocation;
6. consumers recognize a frozen set only when the manifest exists and replays.

The implementation does not claim impossible portable multi-path crash
atomicity. A machine crash may leave orphan content files, but without the final
manifest they are not a frozen set. Tests inject failures on the second and
third writes, verify rollback, verify mixed-set preflight, and verify idempotent
replay of a complete identical set.

### Blocker 2 — a model cannot honestly produce `HUMAN_ACCEPTED`

Accepted. Protocol v1 has been superseded by the pre-implementation frozen
protocol `geography-model-reference/v2`:

```text
ANNOTATION_DRAFT
→ MODEL_ADJUDICATED
→ FROZEN_MODEL_GOLD
```

`FROZEN_MODEL_GOLD` means a model-adjudicated reference set, not human gold. The
frozen `dual-model-adjudication/v1` policy requires:

1. `BLIND_EXTRACTOR`: source packets only;
2. `DRAFT_AUDITOR`: source packets plus the existing draft, with an independent
   omission scan;
3. `DIFFERENCE_ADJUDICATOR`: source packets plus the first two outputs, with an
   explicit dispute set.

The final receipt binds a unique execution ID, provider, model ID, prompt file
and hash, exact input hashes, output file and hash, and completion time for each
pass. Freeze reads and verifies all six prompt/output byte artifacts. It also
verifies the original draft labels, final labels, dispute JSONL, per-unit counts,
and the exact forbidden-input declaration.

The protocol is explicit about the remaining trust boundary: software cannot
prove model identity, execution isolation, or what a model actually observed.
Those are operator attestations and must not be falsified.

### Important 3 — sample selection lacked machine closure

Fixed. The sample now binds:

- full source commit `f37bbf88b135b3629d7790f39189aae4a29c1d7a`;
- exact A-1 manifest repository path;
- raw manifest artifact ID
  `sha256:66e159f2131896e196a15fa3822810227b567f65a23e1cd3a9b9bd65bd44bec6`;
- `selection_algorithm_id=experiment-b-control/v1`;
- seed and the four exact strata.

`verify-sample-selection` reads that exact Git blob, validates the seed/strata,
requires twelve unique random rows and exactly three per stratum, recomputes all
four minima, and verifies the sample rows. The real repository replay produced:

| Stratum | Ordinal | Unit |
| --- | ---: | --- |
| 1–169 | 102 | `XUNIT-15A2604A648927C67B63` |
| 170–338 | 233 | `XUNIT-FABCF30DD2F6F77259E5` |
| 339–507 | 467 | `XUNIT-6DA264A942494F5AAE99` |
| 508–676 | 604 | `XUNIT-FA40CBB1DF4E1DAA0BFC` |

Structural validation separately requires exactly six anchors, four controls,
ten total rows, and one control per stratum.

### Important 4 — the stated protocol freeze point was inaccurate

Fixed. `f426754` is retained as history, not treated as the effective v2 freeze.
The new protocol was committed alone at full SHA
`7f359ea7a9ac30dcf168db6c59d965b025d42708`, before the v2 implementation and
before any model-adjudicated result exists. The sample binds that commit and the
exact protocol-document bytes.

### Important 5 — manifest omitted schemas and compiler identity

Fixed. The sample and final manifest bind exact artifact IDs for:

- protocol document;
- `geography_gold.py` compiler;
- label schema;
- review schema;
- annotation schema;
- new unique-row schema;
- new dispute-row schema;
- frozen-manifest schema.

CLI schema overrides remain useful for tests and replay, but every supplied file
must match the pinned byte identity. A relaxed replacement schema is rejected
with `E-GOLD-CONTRACT`. The final manifest also copies the source-selection
identity, review policy, three model-review records, original-label hash,
adjudicator-output hash, dispute hash/count, and forbidden-input declaration.

### Important 6 — remote CI was red

Explained, not green. GitHub's job/check APIs show that both jobs had zero steps,
`runner_id=0`, and this annotation:

```text
The job was not started because recent account payments have failed or your
spending limit needs to be increased. Please check the 'Billing & plans'
section in your settings
```

This is an account billing/spending-limit gate before runner allocation, not a
test failure. A green Ubuntu/Windows run still requires the repository owner to
restore GitHub Actions billing/allowance and rerun CI. No cross-platform green
claim is made.

### Methodology — ten units are not an unbiased quality sample

Fixed in the frozen protocol. Experiment C must report:

- every ordinal separately;
- the six stress/anchor units as one cohort;
- the four hash-selected controls as another cohort;
- all ten only as a diagnostic aggregate.

General quality claims must prioritize controls or state predeclared weighting
and uncertainty. Capacity decisions may emphasize the stress cohort. Disputed
facts must be scored as strict consensus plus optimistic and conservative
boundaries, and the decision must state whether it changes across those views.

## 4. Runtime annotation state

The original label bytes are unchanged:

```text
labels-draft-v2.jsonl
536 rows = 464 INCLUDE + 72 EXCLUDE
sha256:69f9dc3f9f3301b12bdc2cbd1dc2a06909f0450cc9a57a8d09ece53f200cc77b
```

They have been revalidated under the v2 contract using
`review-draft-v3.json`, which remains `ANNOTATION_DRAFT`. Because annotation
schema identity changed, newly derived draft outputs have new byte identities:

```text
occurrences-draft-v3.jsonl: 464 rows
sha256:475f76e06913d49bc61c816c5a002c94f341477cf6cceaf8522e441f3b297296

unique-draft-v3.jsonl: 148 rows
sha256:f1190735814b71bf73fa41ad3cef53a148e41293049549180b7c66746386ed32
```

These are not frozen results. No `MODEL_ADJUDICATED` receipt, final labels,
dispute set, or `FROZEN_MODEL_GOLD` manifest exists.

The runtime-only review kit is located at:

```text
.runtime/generic-geography/doupo-v1-44966c9/
  experiment-b-gold/model-review-v1/
```

It contains three input-isolated prompts and no fabricated model outputs.

## 5. Validation evidence

The real ten-unit inputs passed:

- A-1 source-selection Git-blob replay;
- source-packet reconstruction from the qualified snapshot and ten native tasks;
- v2 validation and deterministic derivation of all 536 draft labels.

Repository checks at correction implementation commit preparation:

```text
python3 scripts/sync_skills.py --check       PASS
python3 -m compileall -q src tests scripts  PASS
python3 -m pytest                           582 passed in 228.54s
git diff --check                            PASS
python3 -m build --wheel                    PASS
```

Focused gold-tool tests increased from 23 to 35 and cover the reviewer-reported
failure modes, model receipt isolation/identity drift, prompt/output byte
verification, schema pinning, dispute closure, and sample-selection replay.

## 6. Next review request

Review `88cd133..HEAD` and classify findings as blocker or non-blocker. For each
finding provide an exact file/function, reproducible scenario, violated
invariant, and smallest safe correction.

Priority targets:

1. `_write_immutable_set` preflight, rollback, race, and commit-marker semantics;
2. whether `MODEL_ADJUDICATED` can be reached without all three honest records
   and actual prompt/output bytes;
3. schema/compiler/protocol identity pinning under CLI override paths;
4. A-1 Git-blob selection replay and the exact three-draw-per-stratum rule;
5. unique/dispute schema completeness and deterministic IDs;
6. manifest replay closure over every model-review and semantic artifact;
7. whether any wording still misrepresents model reference as human gold;
8. whether the stress/control and uncertainty-bound methodology is sufficient.

Do not approve the semantic labels from the committed branch alone: the
rights-bound source packets and label bytes remain runtime-only. C–G must remain
blocked until a real three-pass model adjudication is completed and
`validate-frozen` passes in a fresh process.
