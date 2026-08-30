# Collection quality review v1

Collection quality uses three separate controls:

```text
deterministic integrity checks
-> small-model CollectionDecision
-> blind large-model CollectionDecision
-> deterministic CollectionReview comparison
-> conservative result or adjudication
```

Model agreement is not evidence truth and never upgrades EvidenceExport
assurance. Collection still cannot emit FactClaim.

## Model tasks

Models may propose decisions for:

- `RELEVANCE`;
- `TRIAGE`;
- `ORIGIN`;
- `CHAPTER_IDENTITY`;
- `STOP`.

Hashes, Artifact availability, provider result completeness, retry lineage,
snippet normalization, budgets and schema validation remain deterministic.

## Independent review

Collector and reviewer decisions must bind the same task, subjects and frozen
input Artifact set. They must use different build identities and preserve their
normalized output envelopes as different Artifacts. The reviewer input manifest
must not contain the collector output Artifact. Validation replays each envelope
and requires its role, build, outcome, confidence and basis to match the
CollectionDecision exactly.

The reviewer first produces its decision without seeing the collector output.
`CollectionReview` then compares normalized outcomes. Rationale and confidence
may differ without changing the verdict; differences in outcome fields are
material.

## Conservative disagreement handling

Until adjudicated:

- relevance disagreement becomes `LEAD_ONLY`;
- triage uses the lower-quality Tier, `UNKNOWN` access legitimacy on conflict,
  and `LEAD_ONLY` selection on conflict;
- origin disagreement becomes `UNKNOWN`;
- chapter identity disagreement becomes `QUARANTINED`;
- stop disagreement becomes `CONTINUE`.

An `ESCALATED` review is unresolved. Its conservative outcome prevents an
unsafe action but is not an accepted assessment.

## Qualification and production review

Both collector and reviewer builds must be evaluated against human-labelled,
held-out fixtures. The initial production period reviews every model decision.
After measured stability, `policies/collection-quality-v1.yaml` permits sampling
ordinary rejects, while selected sources, Tier A/B decisions, independent-origin
decisions, stop decisions and low-confidence decisions remain fully reviewed.

Open-web completeness is not measurable. Reports may claim complete recording
of the declared provider window, benchmark recall and stop-policy compliance;
they must not claim that the internet was exhaustively searched.

## Snapshot boundary

`CollectionDecision` and `CollectionReview` are additive sidecar records in
v0.1. Existing snapshots do not become reviewed merely because reviews exist in
the same Catalog. A later acceptance gate must explicitly bind a passing quality
report before an EvidenceBundle can rely on model-produced collection choices.
