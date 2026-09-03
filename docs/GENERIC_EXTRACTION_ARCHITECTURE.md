# Generic Novel Extraction Architecture A0.1

- status: **PROPOSAL — not a frozen runtime contract**
- based-on: `xhnovel-research-pipeline main@646d8d5`
- scope: v0.1 generic extraction seam and the migration path from the current Scene Scout
- supersedes: the earlier platform-scale A0 draft, not the current production workflow

---

## 1. Decision summary

The next architecture should make `xhnovel` a reusable compiler from frozen novel text to source-grounded domain corpora:

```text
Novel source
  ↓
Novel ingestion
  ↓
profile-neutral NovelTextSnapshot
  ↓
trusted, versioned Extraction Profile
  ↓
deterministic full-text ExtractionUnits
  ↓
structured semantic executor
  ↓
core-owned LocalObservation envelopes
  ↓
provenance-preserving deterministic reduction
  ↓
CorpusSnapshot
  ↓
optional Analyzer
  ↓
human design review
```

The core does **not** need to understand geography, races, cultivation techniques, organizations, items, social relations, or future domains. It owns execution, evidence, identity, rights, integrity, and replay. A Profile owns the domain payload it asks the model to extract.

The architecture deliberately does **not** attempt to finish a universal novel ontology. New research domains should normally be added as new Profiles, not as new core record kinds.

The first implementation decision is not to generalize everything before obtaining evidence. A real `geography-v1` whole-book spike must run before the complete generic contract is frozen.

---

## 2. Why this change is needed

The current implementation has already proved valuable infrastructure:

- deterministic novel ingestion and chapter ordering;
- normalized `Segment` text with source locators and hashes;
- content-addressed artifact storage;
- rights and source-quality gates;
- bounded semantic execution;
- API and `agent-files` executor seams;
- immutable task/request/response artifacts;
- checkpoint and resume;
- exact source spans;
- deterministic validation and replay.

The limitation is that the upper half of the runtime is scene-specific:

- `Catalog.ID_FIELDS` statically lists `SceneWindow`, `SceneScoutRun`, `SceneMergeRun`, and `SceneCandidate`;
- `ModelAttempt` fixes `operation = SCENE_SCOUT` and `subject_id = SWIN-*`;
- `EvidenceBundle` binds frozen text to one `request_id` and one `profile_id`;
- `ResearchRequest` requires a query-sensitive `discovery_brief`;
- the export contract only knows `scene_candidates`;
- build identity currently hashes one fixed Scene profile.

That design is valid for the current production path, but it cannot become the base for geography, cultivation, race, organization, item, and future extraction without repeatedly modifying the core.

This proposal therefore adds a parallel generic seam and leaves the existing Scene Scout intact until an interaction Profile proves it can replace it.

---

## 3. Product boundary

### 3.1 The extraction core answers

> Given immutable, legally usable novel text and a versioned built-in Profile, what source-grounded local observations did this exact semantic build produce?

### 3.2 The reducer answers

> Which observations are byte-equivalent or share a declared exact key, without resolving identity or choosing between conflicts?

### 3.3 An analyzer answers

> What higher-level interpretation can be proposed from one or more corpora and optional human-authored inputs?

Examples:

```text
Interaction Corpus + human mechanism set
→ composition patterns and unresolved interactions

Geography Corpus
→ proposed place hierarchy or travel graph

Cultivation Corpus
→ proposed technique patterns and progression dimensions
```

### 3.4 Humans retain design authority

Extraction and analysis outputs are evidence-bearing drafts, not accepted game design. They do not automatically update `xuanhuan-sandbox`, create new mechanics, or decide which literary structures belong in the game.

---

## 4. Explicit v0.1 threat model

The architecture should defend the risks that already exist in this repository, not the risks of a future multi-tenant plugin platform.

### 4.1 Trusted inputs

The following are trusted because they are reviewed repository assets at a fixed Git commit:

- core Python code;
- built-in Profile manifests, prompts, and payload schemas;
- built-in deterministic reducers;
- repository JSON Schemas and policies.

Content hashes still bind these assets to execution identity. The hashes protect replay and cache correctness, not against a malicious Profile marketplace.

### 4.2 Untrusted inputs

The following remain untrusted:

- TXT, EPUB, HTML, and other novel contents;
- model/provider responses;
- `agent-files` answers;
- runtime checkpoint files;
- copied or restored work-directory files;
- model-generated payload values and citations.

### 4.3 Facts that must never be inferred

The runtime continues to fail closed on:

- rights basis;
- permission to store full text;
- permission to send text to an external semantic executor;
- permission to export excerpts;
- source-quality claims;
- object and lineage identity.

### 4.4 Explicit non-threats for v0.1

v0.1 does not design for:

- third-party or remotely downloaded Profiles;
- arbitrary Profile code;
- unreviewed reducer plugins;
- multi-tenant execution;
- a distributed worker cluster;
- million-record interactive querying;
- a public Profile registry or marketplace.

These may be reconsidered only after a real need appears.

---

## 5. Stable architectural boundaries

## 5.1 Profile-neutral `NovelTextSnapshot`

A novel must be ingested and frozen independently from any extraction question or Profile.

`NovelTextSnapshot` answers only:

> What normalized text did this run admit as the frozen work input?

Minimum logical content:

```json
{
  "schema_version": "novel-text-snapshot/v1",
  "text_snapshot_id": "NTS-...",
  "text_snapshot_hash": "sha256:...",
  "work_id": "NWK-...",
  "ingestion_run_id": "NING-...",
  "chapter_ids": ["CHP-..."],
  "document_ids": ["DOC-..."],
  "segment_ids": ["SEG-..."],
  "retrieval_ids": ["RET-..."],
  "triage_assessment_ids": ["TRI-..."],
  "input_spec_artifact_id": "sha256:...",
  "policy_bundle_hash": "sha256:...",
  "eligible_character_count": 0,
  "created_at": "..."
}
```

It must not contain:

```text
discovery_brief
profile_id
project context
mechanism set
research lead
chapter hint
model configuration
```

Rights are not copied into a new mutable truth source. Before model egress or export, the runtime re-resolves rights from the immutable bound ingestion specification and validates its complete lineage.

The current `allowed_uses = ["event-facts"]` label is Scene-specific. The generic spike must not reinterpret that label as a universal ontology. For v0.1, generic extraction eligibility is derived by one explicit policy from the same frozen deterministic triage facts:

```text
Tier A or B
+ rights permit storage
+ rights permit the selected semantic executor
→ eligible for source-grounded semantic extraction
```

Bind the eligibility policy ID and policy hash to the snapshot/run. Tier D remains ineligible and is never sent to the semantic executor. The old Scene path continues to use its existing `event-facts` contract unchanged. A later contract may generalize `allowed_uses` only after the spike demonstrates the required semantics.

One `NovelTextSnapshot` can be reused by multiple independent Profile runs.

---

## 5.2 Built-in Profile package

A v0.1 Profile is a reviewed repository data package:

```text
profiles/
  geography-v1/
    profile.json
    prompt.md
    payload.schema.json
```

Minimum manifest:

```json
{
  "profile_manifest_version": "extraction-profile/v1",
  "profile_id": "xhnovel.geography",
  "profile_version": "1.0.0",
  "prompt": "prompt.md",
  "payload_schema": "payload.schema.json",
  "schema_name": "xhnovel_geography_observation_v1",
  "unit_policy": {
    "id": "sliding-text/v1",
    "window_chars": 10000,
    "overlap_chars": 1800
  },
  "evidence_policy": {
    "required_groups": [],
    "exempt_paths": []
  },
  "reduction": {
    "reducer_id": "exact-payload-dedup/v1",
    "key_paths": []
  }
}
```

The loader must keep only cheap, load-bearing checks:

- UTF-8 files;
- manifest references resolve inside the Profile root;
- remote schema references are rejected;
- all referenced files enter CAS;
- package content hash is part of build identity;
- the same `profile_id + profile_version` cannot silently refer to different bytes in one execution/cache context;
- payload schemas cannot define core-owned envelope fields.

v0.1 does not support `reducer.py`, shell hooks, dynamic imports, network hooks, custom source loaders, or Profile-selected model endpoints. This is a v0.1 limitation, not a permanent constitutional ban.

---

## 5.3 Core-planned full-text `ExtractionUnit`s

Profiles select a built-in UnitPolicy; they do not implement their own splitter.

The first required policy is:

```text
sliding-text/v1
```

with the current proven defaults:

```text
window_chars = 10000
overlap_chars = 1800
```

Each unit binds:

```text
text_snapshot_id
unit_policy_id
unit_policy parameters
ordered source spans
normalized text hashes
ordinal
unit hash
```

The source text is materialized into the model request by the core.

The validator reconstructs all units and proves:

```text
TEXT_COVERAGE_FULL
eligible_character_count == covered_character_count
uncovered_ranges == []
```

This is a technical coverage statement only. Every corpus must separately report:

```text
SEMANTIC_COVERAGE_UNMEASURED
```

unless a matching semantic qualification run proves otherwise.

### No cross-unit mutable context

An ExtractionUnit request must not contain model output from previous units.

This rule preserves:

- deterministic inputs;
- bounded parallel execution;
- checkpoint independence;
- protection against early hallucinations contaminating the whole book.

Cross-window and cross-chapter interpretation belongs after local extraction.

---

## 5.4 Core-owned observation envelope

The model only returns a Profile payload plus evidence bindings. The runtime creates identity, lineage, trust, and aggregate source spans.

Model-facing shape:

```json
{
  "records": [
    {
      "payload": {},
      "evidence_bindings": [
        {
          "paths": ["/field"],
          "source_spans": [
            {
              "segment_id": "SEG-...",
              "start": 0,
              "end": 10,
              "normalized_text_hash": "sha256:..."
            }
          ]
        }
      ]
    }
  ]
}
```

Persisted `LocalObservation` shape:

```json
{
  "schema_version": "local-observation/v1",
  "observation_id": "OBS-...",
  "observation_hash": "sha256:...",
  "text_snapshot_id": "NTS-...",
  "work_id": "NWK-...",
  "unit_id": "XUNIT-...",
  "extraction_run_id": "XRUN-...",
  "extraction_build_id": "BLD-...",
  "profile_id": "xhnovel.geography",
  "profile_package_hash": "sha256:...",
  "payload_schema_artifact_id": "sha256:...",
  "payload": {},
  "evidence_bindings": [],
  "source_spans": [],
  "status": "DRAFT",
  "verification": "UNVERIFIED"
}
```

Core invariants:

- the Profile cannot set IDs, lineage, rights, trust, or assurance;
- every evidence path is a valid JSON Pointer into `payload`;
- every required evidence group has at least one binding;
- every cited span belongs to the current unit;
- span bounds and `normalized_text_hash` match the frozen Segment;
- `source_spans` is derived by the core as the exact union of evidence spans;
- source text remains untrusted data, never instructions.

### Evidence groups, not scalar-leaf explosion

A Profile explicitly declares which sets of fields may share evidence:

```json
{
  "evidence_policy": {
    "required_groups": [
      ["/subject_name", "/relation", "/object_name"]
    ],
    "exempt_paths": ["/kind"]
  }
}
```

A single cited passage can support the entire group. Structural constants such as `/kind` may be exempt. A closed enum is **not** automatically structural: if the model chooses `LOCATED_IN`, `ENSLAVED`, or `ELDER` as a factual claim, that value still requires evidence unless explicitly exempted for a valid reason.

v0.1 must not use a small citation-character budget as a hard rejection gate. The unit boundary already limits the maximum citation surface. Broad but legal citations are a quality issue for qualification and review, whereas missing, out-of-unit, out-of-bounds, or hash-mismatched citations are integrity failures.

---

## 5.5 Reducer versus Analyzer

### Provenance-preserving reducer

The reducer may:

- remove byte-identical duplicate payloads;
- merge evidence for byte-identical payloads;
- group records by declared exact key;
- sort deterministically;
- preserve all conflicting members.

It may not:

- call an LLM;
- use network, clock, or randomness;
- resolve aliases;
- decide two names identify the same entity;
- choose one conflicting fact;
- summarize facts into a new claim;
- merge same names across works by default.

The initial reducer registry needs only:

```text
exact-payload-dedup/v1
```

`exact-key-bucket/v1` may be added when a real Profile needs it.

A name bucket means:

```text
NAME_CLUSTER_NOT_ENTITY
```

It does not prove that same-named mentions identify one place, person, race, or technique.

### Semantic analyzer

Alias resolution, entity splitting, map hierarchy, technique synthesis, mechanism composition, and cross-work pattern discovery belong to optional Analyzers.

Analyzer outputs are new DRAFT derived records. They never rewrite the original Observation or CorpusSnapshot.

---

## 5.6 Independent Profile runs

Each Profile has its own:

- ExtractionBuild identity;
- unit manifest;
- checkpoint;
- ModelAttempt ledger;
- ExtractionRun;
- ReductionRun;
- CorpusSnapshot;
- output directory.

A failed geography run does not invalidate a completed interaction run over the same `NovelTextSnapshot`.

v0.1 does not need a `NovelCompileManifest`. A multi-Profile summary can be introduced later as a regenerable view when a real multi-Profile CLI exists.

---

## 6. Minimal v0.1 object model

Only four new Catalog-level objects are proposed:

```text
NovelTextSnapshot
ExtractionRun
ReductionRun
CorpusSnapshot
```

Existing objects are generalized rather than duplicated:

```text
ExtractorBuild  → generic extraction build identity
ModelAttempt    → v2 generic operation and subject identity
Artifact        → unchanged
```

High-volume rows stay outside the in-memory Catalog:

```text
units.jsonl
observations.jsonl
corpus.jsonl
```

Each file is:

- UTF-8 canonical JSON Lines;
- deterministically ordered by complete record hash;
- stored as one CAS artifact in v0.1;
- accompanied by record count and a logical result hash.

No sharding, SQLite, or streaming record database is required for v0.1.

### Logical result identity

The logical identity of a record set is:

```text
hash(ordered list of record hashes)
```

It is not defined only as the byte hash of one JSONL file. This keeps future mechanical sharding from changing the logical corpus identity.

---

## 7. Build identity and invalidation

Extraction and reduction identities must be separate.

### ExtractionBuild binds

```text
engine extraction implementation hash
profile execution-closure hash
core prompt hash
profile prompt hash
payload schema hash
semantic-eligibility policy id/hash
unit policy id and parameters
executor kind and build id
model identifier and parameters
tool policy hash
```

### ReductionRun binds

```text
input observation result hash
reducer id
reducer implementation hash
reducer config hash
```

No standalone `ReducerBuild` object is needed.

Expected invalidation:

| Change | Required work |
|---|---|
| source bytes or ingestion semantics | ingestion, extraction, reduction |
| Profile prompt or payload schema | extraction, reduction |
| unit policy or parameters | extraction, reduction |
| model or executor build | extraction, reduction |
| reducer config or implementation | reduction only |
| report renderer | rendering only |
| unrelated new Profile | no invalidation of prior runs |

Audit timestamps must not enter Observation IDs, record ordering, or logical result hashes. ModelAttempt may retain actual attempt time as audit data.

---

## 8. Replay and integrity states

The current runtime tends to treat validation and exact functional replay as one concept. The generic path must report three orthogonal facts:

```json
{
  "artifact_integrity": "VALID",
  "exact_runtime": "AVAILABLE",
  "functional_replay": "VERIFIED"
}
```

Valid alternatives include:

```json
{
  "artifact_integrity": "VALID",
  "exact_runtime": "UNAVAILABLE",
  "functional_replay": "NOT_RUN"
}
```

Meanings:

- `artifact_integrity`: hashes, schemas, and lineage are intact;
- `exact_runtime`: the exact code/profile/executor assets required for replay are available;
- `functional_replay`: the runtime actually reconstructed requests, outputs, validation, and reduction.

Missing an old runtime must not be reported as artifact corruption.

A technically successful corpus still defaults to:

```text
semantic_assurance = UNQUALIFIED
```

---

## 9. Adversarial review obligations

The generic seam must be reviewed against real failure modes.

### 9.1 Identity and cache attacks

- modify one Profile prompt byte without changing version;
- modify reducer implementation without changing reducer ID;
- rerun workers in a different completion order;
- reuse a checkpoint after Profile or unit-plan changes.

Expected result: identities change or checkpoint reuse fails closed; output ordering remains stable.

### 9.2 Evidence attacks

- cite outside the current unit;
- cite a valid offset with the wrong text hash;
- omit evidence for a required group;
- use one source span to support an unrelated payload;
- return payload fields reserved by the core.

Expected result: integrity violations fail; semantic overreach remains visible for review and qualification rather than being silently promoted.

### 9.3 Reduction attacks

- overlapping windows emit identical records;
- same work contains two different entities with the same name;
- later text contradicts earlier text;
- two works share the same place name.

Expected result: only exact duplicates are collapsed; name buckets are not entities; conflicts and work boundaries are preserved.

### 9.4 Runtime attacks

- novel text contains prompt injection;
- `agent-files` task is modified;
- one unit response is malformed;
- process crashes after partial completion;
- one Profile fails while another has already completed.

Expected result: source text remains data; task tampering hard-fails; completed units resume; Profile runs remain isolated.

---

## 10. Migration strategy

There must be no big-bang rewrite.

### Phase A — geography spike first

Build the smallest parallel generic path needed to run `geography-v1` over a real frozen novel:

```text
existing ingestion
→ provisional profile-neutral snapshot view
→ generic units
→ generic executor request
→ core envelope validation
→ observations.jsonl
→ exact-payload-dedup
→ corpus.jsonl
```

Do not first implement a complete schema family or migrate the current Scene path.

### Phase B — freeze the minimum contract from real failures

After the spike, freeze only fields and validators demonstrated to be necessary.

### Phase C — add a third simple Profile

Add a small Profile such as `race-mention-v1`. When it reuses the existing UnitPolicy and reducer, it must not modify the Python extraction engine.

### Phase D — migrate interaction

Only after two non-identical Profiles run through the seam should the current Scene Scout be migrated into an `interaction-v2` Profile. Until then:

- `research-novel`;
- `SceneWindow`;
- `SceneScoutRun`;
- `SceneCandidate`;
- existing Scene validation and export

remain supported.

The generic work starts from current `main`, not from the run-004 snapshot branch. Runtime artifacts and full text remain out of source control.

---

## 11. v0.1 acceptance gates

v0.1 is acceptable only when all of the following hold:

1. One novel ingestion can be reused by at least two Profiles.
2. When an existing UnitPolicy and reducer suffice, a new Profile does not modify the Python extraction engine.
3. Eligible text reconstructs to `TEXT_COVERAGE_FULL = 100%`, while semantic coverage remains unmeasured by default.
4. A one-byte Profile prompt change changes ExtractionBuild identity.
5. Reducer config or implementation changes create no model calls but create a new ReductionRun identity.
6. Worker completion order does not change Observation/Corpus logical hashes or output bytes.
7. Reducers do not discard, select, or reconcile conflicting observations.
8. Remote schema references and paths escaping the Profile root are rejected.
9. Missing required evidence, out-of-unit spans, invalid bounds, and text-hash mismatches are rejected.
10. Modified `agent-files` tasks hard-fail.
11. Failure of one Profile does not invalidate another completed Profile.
12. A successful replayable CorpusSnapshot remains `semantic_assurance = UNQUALIFIED` unless a matching qualification record exists.

Not required in v0.1:

```text
record sharding
SQLite
100k-record performance tests
third-party Profile loading
Profile code plugins
ReducerBuild
NovelCompileManifest
universal entity ontology
automatic alias resolution
automatic game-mechanism invention
```

---

## 12. Open questions deliberately left to the geography spike

The architecture does not yet freeze:

- the exact `NovelTextSnapshot` schema;
- whether ExtractionUnits need a Catalog object or only a JSONL manifest;
- the final generic ModelAttempt v2 field names;
- whether `exact-payload-dedup/v1` is sufficient for the first corpus;
- how much place typing can be reliably extracted from local windows;
- whether chapter boundaries need a second built-in UnitPolicy;
- the minimum useful CorpusSnapshot summary.

These decisions should be made from the real spike output and failures, not from speculative completeness.

---

## 13. Working rule

> Unify execution and evidence, not every literary domain.

And for v0.1:

> Defend untrusted text, model output, runtime files, rights, identity, and lineage. Do not build a plugin platform or a large-scale corpus warehouse before the project has a second working Profile.
