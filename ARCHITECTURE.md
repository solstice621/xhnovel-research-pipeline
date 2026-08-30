# Frozen v0.1 architecture

status: `0.1-draft-frozen`  
implementation language: Python 3.11+ (see ADR-0002)  
this document is the runtime architecture. Object fields live in `contracts/`.

## 1. Producer vs consumer

This repository is the only evidence producer. `xuanhuan-sandbox` submits an
immutable `ResearchRequest` and later imports an immutable `EvidenceExport`.
Project Context never enters `ExtractionRun`.

## 2. Pipeline stages

```text
ResearchRequest
 → SearchCampaign + QuerySpec
 → SearchRun + DiscoveryHit          # collection, no claims
 → Source / Retrieval / Artifact
 → TriageAssessment / OriginAssessment
 → ParseRun / ParsedDocument / Segment
 → CollectionSnapshot               # what was collected
 → EvidenceBundle                   # what extraction may read
 → ExtractionRun / Claim
 → QualificationRun / AssuranceRecord
 → EvidenceExport
```

Collection and parse cannot create `Claim`. Only an extraction bound to a
frozen `bundle_hash` can.

## 3. Identity vs replica health

`Artifact` identity is SHA-256 of the exact bytes. Storage URI, last-verified
time and replica health live on `ArtifactReplicaStatus` and never enter
`artifact_id` or `bundle_hash`.

## 4. Freeze boundaries

- `CollectionSnapshot` answers: what did this campaign possess at freeze time?
- `EvidenceBundle` answers: which exact segments and assessments may this
  extraction read?
- Adding, removing or replacing any bundle member requires a new bundle id
  and hash. Old extraction runs stay bound to the old hash.

## 5. Validators

| Command | Responsibility |
|---|---|
| `validate collection` | campaign, queries, runs, hits, sources, retrievals, artifacts, triage, origin, snapshot |
| `validate evidence` | parse, segments, bundle freeze, claim support, grading, profile payload |
| `validate qualification` | build registry, RUN-A/B, manifests, isolation, invalidation |
| `validate export` | export schema, hashes, producer, policies, assurance, artifact manifest, revocation |

## 6. Runtime layout

Git holds contracts, policies, profiles, fixtures and selected reference
exports. Bytes live in a content-addressed store. The local backend uses
`.runtime/objects/sha256/` but that path is not part of the data contract.

## 7. Explicit non-goals (v1)

No UI, distributed scheduler, multi-tenant platform, vector DB, browser
farm, or automatic game-design output.
