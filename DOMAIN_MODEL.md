# Domain model v0.1-draft-frozen

Every object below answers: who creates it, mutability, ID, hash domain,
retry/supersede, deletion audit, validator.

Timestamps are UTC `YYYY-MM-DDTHH:MM:SSZ`. Structural hashes are SHA-256 of
canonical JSON (see `HASHING_AND_CANONICALIZATION.md`). `artifact_id` is
`sha256:` plus the hex digest of the raw bytes.

ID prefixes: `REQ-` `CAM-` `QRY-` `SRUN-` `HIT-` `SRC-` `RET-` `TRI-` `ORI-`
`CDEC-` `CRV-` `PRUN-` `DOC-` `SEG-` `SNP-` `BND-` `ERUN-` `CLM-` `BLD-`
`QRUN-` `ASR-` `EXP-`.

SearchRun uses `SRUN-` (not generic `RUN-`) so parse/extract runs cannot collide.

## ResearchRequest

- Creator: consumer (or a local fixture standing in for one).
- Mutable: no after `created_at`.
- ID: `REQ-` assigned by producer on ingest.
- Hash: canonical payload excluding none (request has no self-hash; export cites the object).
- Retry: no. Replacement uses `supersedes`.
- Delete: keep the object; mark superseded. Never silently drop origin.
- Validator: collection (ingest) + export (origin_request).

## SearchCampaign

- Creator: producer planner (v0.1: human-supplied QuerySpec + runner).
- Mutable: status may move DRAFT→RUNNING→terminal. Terminal records are frozen.
- ID: `CAM-`.
- Hash: not content-addressed; cited by id + stop_reason.
- Retry: new campaign. Do not rewrite history.
- Validator: collection.

## QuerySpec

- Creator: human in v0.1; later a planner build.
- Immutable after first SearchRun.
- ID: `QRY-`. `parent_query_id` and `derived_from_hit_ids` record lineage.
- Validator: collection.

## SearchRun

- Creator: provider adapter.
- Immutable. Retry → new id + `retry_of`.
- ID: `SRUN-`.
- Hash: `result_set_hash` over ordered hit ids/urls/snippets. Self hash omitted from its own input.
- Validator: collection. Provider raw JSON is an Artifact.

## DiscoveryHit

- Creator: SearchRun materialization.
- Immutable. Selection is recorded, not deleted.
- ID: `HIT-`.
- Ordered by `rank` as returned by the provider. This order is significant.

## Source

- Creator: collection identity layer.
- Logical identity, not a fetch. No permanent tier.
- ID: `SRC-`.
- Delete: tombstone the id; retrievals remain.

## Retrieval

- Creator: fetcher.
- Immutable. Retry → new Retrieval + `retry_of`.
- ID: `RET-`.
- Status machine in `STATE_MACHINES.md`.
- Validator: collection.

## RetrievalArtifact

- Creator: fetcher/parser.
- Many artifacts per retrieval (`RAW_RESPONSE`, `RESPONSE_HEADERS`, `PROVIDER_JSON`, …).
- Not a hash identity of its own; hashed via artifact_id.

## Artifact

- Creator: CAS `put(bytes)`.
- Immutable. Identity = bytes.
- ID: `sha256:<hex>`.
- Replica health is **not** this object.
- Validator: collection + export durability.

## ArtifactReplicaStatus

- Creator: store backend. Mutable health record.
- Does not enter bundle/export identity hashes.
- Validator: collection (availability) + hardening scan.

## TriageAssessment

- Creator: triage policy build.
- Immutable. Re-triage → new assessment id; new bundle if used.
- ID: `TRI-`.
- Tier A/B/C/D; access_legitimacy and retention are separate fields.
- Validator: collection.

## OriginAssessment

- Creator: origin policy build / human.
- Immutable pair assessment.
- ID: `ORI-`.
- `UNKNOWN` cannot satisfy dual-B confirmation.
- Validator: collection + evidence (grading).

## CollectionDecision / CollectionReview

- A CollectionDecision is an immutable normalized proposal from either a
  collector or an independent reviewer build.
- It binds task, subject ids, frozen input Artifact ids, input manifest hash,
  normalized output Artifact and exact build id.
- Collector and reviewer builds and output Artifacts must differ. Blind reviewer
  input cannot include the collector output Artifact.
- CollectionReview is a deterministic comparison, not a third model opinion.
- Material disagreement is `ESCALATED` with a conservative temporary outcome;
  it is not silently rewritten into the collector assessment.
- Review status is separate from EvidenceExport assurance.
- Validator: collection.

## ParseRun

- Creator: parser.
- Immutable. Reparse → new ParseRun + `supersedes` / `retry_of`.
- ID: `PRUN-`.
- `output_hash` over parsed document + segments. Does not fetch.
- Validator: evidence.

## ParsedDocument / Segment

- Creator: parser. Immutable for a given ParseRun.
- Segment ID: `SEG-`. Hash of normalized text is `normalized_text_hash`.
- Locator is required. Claims must cite segment_id.
- Validator: evidence.

## CollectionSnapshot

- Creator: collection freeze.
- Immutable after FROZEN.
- ID: `SNP-`. `snapshot_hash` covers member id sets (unordered, sorted by id)
  plus campaign_id.
- Validator: collection.

## EvidenceBundle

- Creator: bundle builder.
- DRAFT until freeze; FROZEN is immutable.
- ID: `BND-`.
- `bundle_hash` covers: segment set + retrieval/artifact refs + assessments +
  profile_id + policy_bundle_hash + selection_manifest.
- Validator: evidence.

## ExtractionRun

- Creator: extraction runner.
- Immutable. Retry → new run + `retry_of`.
- ID: `ERUN-`.
- Must have `bundle_hash` equal to the frozen bundle.
- Input manifest is the isolation proof.
- Validator: evidence + qualification.

## Claim

- Creator: extractor only.
- Live rows: `ACTIVE`. `SUPERSEDED` cannot stay ACTIVE.
- ID: `CLM-`.
- Each Claim names the exact `extraction_run_id` that produced it.
- Support must include retrieval_id, artifact_id, segment_id, normalized_text_hash.
- Validator: evidence.

## ExtractorBuild / QualificationRun / AssuranceRecord

- Build identity covers model, prompts, parameters, profile, executor, tools.
- Qualification is per build, not per scene.
- A PASS record binds the exact build identity, fixture bytes and two distinct
  execution-result records; assurance records reference that PASS.
- Historical exports are not rewritten on invalidation.
- Validator: qualification.

## EvidenceExport

- Creator: export builder.
- Immutable bytes. Tamper → verify fail.
- ID: `EXP-`. `export_hash` over payload omitting itself.
- Revocation is a sidecar record, not an in-place edit.
- Validator: export.
