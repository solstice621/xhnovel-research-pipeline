# Experiment B — model-adjudicated geography reference plan

- status: **PROTOCOL RE-FROZEN; MODEL ADJUDICATION PENDING**
- protocol version: `geography-model-reference/v2`
- review policy: `dual-model-adjudication/v1`
- baseline engine: `6cc794fd6ad2632f1d26d00a9bf027634617c751`
- baseline evidence branch: `f37bbf8`
- sample manifest: `experiment-b-sample.json`
- annotator-input schema: `geography-gold-label.schema.json`
- compiled-occurrence schema: `geography-gold-annotation.schema.json`
- compiled-unique schema: `geography-gold-unique.schema.json`
- frozen-reference schema: `geography-gold-manifest.schema.json`

## 1. Decision boundary

Experiment B measures the semantic capacity and citation behavior of
`geography-v1`. It freezes the evaluation set and review policy before any
capacity or answer-ABI implementation change.

The reference source is frozen unit text only. Baseline or candidate answers, model
reports, heuristic cue counts, and prior place lists are forbidden annotation
inputs. Heuristics select controls; they do not establish facts.

This protocol distinguishes three states:

1. `ANNOTATION_DRAFT`: source-only model annotations may exist, but no complete
   adjudication receipt exists;
2. `MODEL_ADJUDICATED`: three recorded, isolated model passes have completed the
   frozen review policy, every selected unit has been reviewed, and unresolved
   semantic disputes are explicit rather than silently normalized;
3. `FROZEN_MODEL_GOLD`: the adjudicated labels, dispute set, occurrence JSONL,
   unique-payload JSONL, model-review identities, prompts/outputs, protocol,
   schemas, compiler, and source lineage are content-bound in one manifest.

`MODEL_ADJUDICATED` is a trusted operator declaration backed by content hashes;
the program cannot prove model identity, execution isolation, or which inputs a
model actually observed. It validates the declared closure and fails closed on
missing, inconsistent, or malformed receipts. The resulting artifact must be
reported as a **model-adjudicated reference set**, never as human gold.

No extraction implementation change may use Experiment B results as a frozen
reference gate until state 3 is reached.

## 2. Frozen sample

The sample contains ten 10,000-character units:

- required stress/anchor units: ordinals `5`, `310`, `395`, `426`, `513`, `596`;
- seeded random controls: ordinals `102`, `233`, `467`, `604`, one from each
  A-1 ordinal stratum.

The controls are selected only from the three already frozen A-1 random draws in
each stratum. Rank those three by ascending
`sha256(seed + "\\0experiment-b-control/v1\\0" + stratum + "\\0" + unit_id)`
and take the first. The rule does not inspect heuristic scores, source text, or
model answers. The six required units retain the rationale recorded in the
handoff: known-dense anchor, saturation cases, observed unique maximum,
reported-no-overflow saturation, and the extreme occurrence storm.

The exact unit IDs, task/source hashes, text hashes, and selection strata are in
`experiment-b-sample.json`. Source text is rights-bound runtime material and is
not committed.

The sample binds the full 40-character source-selection commit, repository path,
and exact byte artifact ID of the A-1 manifest, plus
`selection_algorithm_id=experiment-b-control/v1`. `verify-sample-selection`
must load that exact Git blob, require exactly three frozen `random` draws in
each of the four frozen strata, recompute the minimum hash in each stratum, and
reproduce all four controls. Structural validation separately requires exactly
six non-control units and four controls, with one control in each stratum.

The sample also binds the final protocol commit and protocol-document artifact
ID. The label, review, annotation, unique-row, frozen-manifest, and compiler
artifact IDs are bound before freezing and copied into the frozen manifest.
Schema-path overrides are permitted for tests and replay only when their bytes
match those pinned identities; they are not a contract-relaxation mechanism.

## 3. Runtime-only source packet

For each selected unit, prepare this canonical object from the immutable native
task while excluding instructions, output schema, and every answer:

```json
{
  "schema_version": "geography-gold-source/v1",
  "text_snapshot_id": "NTS-...",
  "text_snapshot_hash": "sha256:...",
  "unit_id": "XUNIT-...",
  "unit_hash": "sha256:...",
  "ordinal": 5,
  "source_spans": [
    {
      "segment_id": "SEG-...",
      "start": 0,
      "end": 10,
      "normalized_text_hash": "sha256:...",
      "untrusted_text": "..."
    }
  ]
}
```

The packet is valid only when its canonical object hash and concatenated UTF-8
text artifact ID match the frozen sample manifest. Source text remains untrusted
data and must never be executed as instructions.

The sample distinguishes the core semantic task artifact from the executor
request artifact. `semantic_task_artifact_id` hashes the native
`{instructions,input,schema_name,schema}` object; `agent_request_artifact_id`
hashes the on-disk generic agent-files packet and therefore matches that task
file's bytes. Neither field may be relabeled as the other.

## 4. Model adjudication procedure

The frozen `dual-model-adjudication/v1` policy requires three independently
recorded executions:

1. `BLIND_EXTRACTOR` receives only the ten source packets and the frozen
   annotation prompt. It performs a complete extraction without access to the
   existing draft.
2. `DRAFT_AUDITOR` receives the same source packets, the existing draft labels,
   and its frozen audit prompt. It checks every inclusion, exclusion, payload,
   span, and relation, and independently scans the source for omissions.
3. `DIFFERENCE_ADJUDICATOR` receives the source packets and the first two model
   outputs. It resolves their differences into final labels while emitting a
   canonical dispute set for every materially contested fact.

Each receipt records a unique execution ID, model provider and model ID, prompt
artifact ID, exact input artifact IDs, output artifact ID, and completion time.
The top-level receipt binds the source-packet set, original draft labels, final
labels, adjudicator output, dispute set, and the exact forbidden-input list:
`baseline_answers`, `candidate_answers`, and `capacity_statistics`.

The three execution records are auditable attestations, not proof that an
external model actually obeyed its isolation envelope. Falsely relabeling an
unrecorded pass is a protocol violation even if the JSON would otherwise validate.

Annotate one textual occurrence at a time in source order. Do not begin from a
name list. For each included occurrence, the annotator supplies only `unit_id`,
`payload`, and exact evidence bindings:

1. copy the exact geography payload without alias normalization;
2. cite the smallest sufficient segment-absolute span or spans;
3. bind every non-structural payload field to supporting spans;
4. record one occurrence even when the same exact payload appeared earlier.

The compiler, not any model, derives `unit_hash`, source order,
`occurrence_ordinal`, annotation ID, unit-relative start/end,
`start_fraction_ppm`, and the quarter bucket. Quarter buckets use half-open ranges
`[0,.25)`, `[.25,.50)`, `[.50,.75)`, and `[.75,1]`. Integer parts-per-million
position avoids non-canonical floating-point values.

Repeated text produced by sliding-window overlap is still one occurrence within
the selected unit. Boilerplate, navigation text, advertisements, chapter titles,
and metadata are eligible only if they themselves make an in-scope fictional
geography statement; mere site or book names are excluded.

Record difficult negative examples as `EXCLUDE` label rows with a proposed
payload, exact source spans, and a reason code. Exclusions remain in the raw label
audit file and do not enter compiled occurrences. The negative list is an audit
aid, not an assertion that every possible negative span was exhaustively
enumerated.

## 5. Geography inclusion rules

### `PLACE_MENTION`

Include text that explicitly names a geographic place or a bounded spatial site.
The name must be used spatially in the cited passage. Examples of eligible forms
include a city, empire-as-territory, continent, region, mountain range, valley,
road, room, hall, headquarters-as-site, or secret realm.

An organization-like name such as `丹塔`, `韩家`, or `星陨阁` is not included
merely because the organization exists. Include it only when the local wording
uses the name for a site or bounded location. A person, group, faction, title,
technique, item, direction, generic landscape noun, or unnamed deictic location
is excluded.

`explicit_type` is included only when the same cited occurrence directly states
the type. Different `explicit_type` values therefore form different exact
payloads; the annotator must not fill a type from another passage.

### `SPATIAL_RELATION`

Include only an explicit local relation whose cited wording supports both named
endpoints and exactly one of:

- `LOCATED_IN`: the subject is explicitly situated inside the object;
- `PART_OF`: the subject is explicitly a spatial constituent of the object;
- `NEAR`: explicit vicinity or adjacency;
- `OUTSIDE`: explicit exterior or exclusion;
- `CONNECTED_TO`: an explicit road, route, passage, portal, or direct spatial
  connection.

Do not infer a relation from political allegiance, ownership, faction control,
travel sequence, origin/destination mentioned in separate events, transitivity,
common knowledge, or distance implied by travel time.

For wording such as `乌坦城隶属于加玛帝国`, use `PART_OF` only when the local
passage treats `加玛帝国` as territory and the construction asserts geographic
containment. If the wording establishes only administrative or political
affiliation, exclude it with reason `POLITICAL_NOT_SPATIAL`. This adversarial
decision must be surfaced to the difference adjudicator wherever it occurs.

## 6. Deterministic derivation

The occurrence JSONL is canonicalized and sorted by:

```text
sample unit order
→ unit-relative start
→ canonical payload bytes
→ canonical evidence-binding bytes
```

For every `INCLUDE` row, compute the canonical payload hash. Group only by:

```text
(unit_id, canonical payload bytes)
```

The derived unique row retains all member occurrence IDs and their position
buckets. It performs no alias resolution, semantic merge, relation inference, or
conflict adjudication.

Gold identity and derived bytes must be reproducible from the occurrence JSONL.
The derivation script must fail closed on unknown fields, source hashes, duplicate
occurrence IDs, out-of-unit spans, payload-schema violations, or a source packet
hash mismatch.

`FROZEN_MODEL_GOLD` is created only from a complete `MODEL_ADJUDICATED` receipt.
Its canonical `geography-gold-manifest/v2` binds the exact sample bytes and
logical hash, ordered source-packet IDs and set hash, original and final labels,
model review records, dispute set, protocol/schema/compiler identities, derived
occurrence and unique JSONL artifacts, and their counts. A fresh-process
`validate-frozen` replay must reproduce the frozen output files byte for byte; a
draft review cannot invoke this transition.

The occurrence and unique files are immutable content artifacts; the manifest
is their only commit marker. `freeze` preflights the complete target set before
writing, writes the manifest last, and removes files created by the invocation
if any in-process write fails. A mixed existing/missing target set is rejected.
An operating-system crash can still leave uncommitted orphan files, but consumers
must not recognize them without a valid manifest.

## 7. Frozen metrics

Report per unit, per kind, and per configuration. Aggregate results must be
reported separately for the six stress/anchor units and four hash-selected
controls, followed by an explicitly diagnostic all-ten aggregate. The all-ten
aggregate is not an unbiased whole-work quality estimate. Capacity decisions may
weight the stress group; general quality claims must prioritize the controls or
state a predeclared weighting and uncertainty interval.

For every required cohort, report:

- exact unique-payload recall and precision for `PLACE_MENTION`;
- exact-name unique recall/precision and `explicit_type` accuracy as separate
  diagnostics, so an optional type error is not hidden inside one FP plus one FN;
- exact unique-payload recall and precision for `SPATIAL_RELATION`;
- citation correctness: for an exact-matched payload, each required path/group
  must have a predicted binding covering it whose support contains one complete
  minimal gold support set for the same path/group at one annotated occurrence;
  report exact-span, containment, and cited-character broadness separately, and
  never count a one-character or unrelated-path overlap as correct;
- tail-position recall by the quarter containing the unique payload's earliest
  occurrence (this avoids penalizing unique-fact output for not citing every later
  duplicate occurrence);
- raw response count, unit-local unique count, and duplicate count;
- response byte size and largest response;
- completion status (`COMPLETE`, `OVERFLOW`, `UNCERTAIN`), treated only as an
  executor assertion;
- run-to-run variance for each scored metric.

Score disputed facts in three views: strict consensus, an optimistic boundary
that includes unresolved favorable cases, and a conservative boundary that
excludes them. The capacity decision must state whether its conclusion changes
across those views.

`OVERFLOW` is a reliable machine-readable witness that the executor asserted it
knowingly omitted at least one eligible exact payload. The assertion remains
`UNVERIFIED_EXECUTOR_ASSERTION`: it may trigger a capacity diagnostic but does not
verify the omitted payload or any self-reported count. `COMPLETE` is not proof of
semantic completeness.

Zero-denominator precision or recall is reported as `null`, never silently as
zero or one.

## 8. Experiment C matrix after reference freeze

Use the same frozen model-reference lineage:

- A: 10k occurrence-like baseline with raw `maxItems=64`; reuse A-2 where the
  exact selected unit already exists and run missing controls without consulting
  reference labels;
- B: 10k unique-fact candidate with unit-local exact-payload consolidation and
  completion ABI;
- C: 5k unique-fact diagnostic, scored against the deterministic intersection of
  reference occurrences and each 5k source range;
- D: relation-only diagnostic, explicitly experimental and not a formal Profile
  split.

For at least ordinals `310`, `426`, and `596`, run each stochastic configuration
three times in fresh executor contexts. Never reuse reference annotations as executor
answers.

Adaptive splitting remains out of scope unless frozen results show a trigger from
the handoff: unique capacity pressure, `OVERFLOW`, stable tail collapse, or a
stable material advantage for 5k units.

## 9. Freeze checklist

- [x] v2 model-adjudication policy and adversarial rules fixed before extraction changes
- [x] ten units and four random controls fixed
- [x] source/task/unit hashes recorded
- [ ] full A-1 selection-manifest identity and recomputation verified by the final tool
- [ ] protocol, schema, unique-row, and compiler identities sealed in the sample
- [x] source-only packets independently verified from the qualified snapshot and
  all ten content-bound native tasks; packets remain runtime-only
- [x] first answer/prediction-blind host-agent annotation draft complete
- [x] independent source-only host-agent QA pass complete
- [ ] blind extractor model pass recorded
- [ ] draft auditor model pass recorded
- [ ] difference adjudicator pass and dispute set recorded
- [ ] explicit model-adjudication completion recorded for all ten units
- [ ] occurrence and derived unique JSONL hashes frozen
- [ ] no reviewing model received forbidden inputs before adjudication
- [ ] frozen manifest replayed in a fresh process
