# State machines v0.1-draft-frozen

Terminal states are immutable. Retry and re-run always allocate a new id.

## SearchCampaign

```text
DRAFT → RUNNING → COMPLETED
                → EXHAUSTED
                → BUDGET_STOPPED
                → FAILED
                → CANCELLED
```

Every terminal campaign has a `stop_reason` in
`coverage_reached | budget_exhausted | no_new_source | provider_exhausted | manual_stop | failed`.

## Retrieval

```text
QUEUED → FETCHING → FETCHED
                  → BLOCKED
                  → UNREACHABLE
                  → FAILED
                  → NEEDS_RENDERER
```

`NEEDS_RENDERER` is recorded for JS-only pages. v1 does not run a browser farm.

## ArtifactReplicaStatus

```text
AVAILABLE → MISSING | CORRUPT | RETENTION_DELETED
```

These transitions do not change `artifact_id`.

## ParseRun / ExtractionRun

```text
QUEUED → RUNNING → SUCCEEDED | FAILED | INCONCLUSIVE
```

## EvidenceBundle

```text
DRAFT → FROZEN → EXTRACTED → EXPORTED
               ↘ SUPERSEDED
```

FROZEN members cannot be edited. SUPERSEDED bundles keep bytes.

## ExtractorBuild

```text
UNQUALIFIED → QUALIFIED → INVALIDATED
```

Invalidation does not rewrite historical exports. New qualified exports from an
INVALIDATED build are rejected.

## EvidenceExport

```text
CREATED → VERIFIED → IMPORTED
        ↘ REVOKED
        ↘ STALE_POLICY
```

`STALE_POLICY` means a newer policy exists; it is not the same as `REVOKED`.
Revocation is reserved for integrity failure, forgery, or invalid qualification
basis.
