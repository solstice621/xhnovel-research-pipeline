# Canonical JSON and hash domains

status: `0.1-draft-frozen`

## 1. Byte hash

`artifact_id = "sha256:" + hex(SHA-256(raw_bytes))`.

Forbidden: `hash-a`, `todo`, `placeholder`, truncated digests, hashes of titles
or URLs. Derived artifacts record `parent_artifact_id` and `transform_build_id`.

## 2. Canonical JSON

Structural hashes use UTF-8 canonical JSON:

- object keys sorted by UTF-8 code units;
- no insignificant whitespace;
- arrays keep declared order;
- numbers are integers only (no floats);
- strings use JSON escaping (`json.dumps` with `ensure_ascii=False`);
- `true` / `false` / `null` as in JSON.

YAML is a serialization. Two YAML documents that load to the same JSON value
have the same hash.

## 3. Self-hash omission

When hashing an object that contains its own hash field, omit that field
(`bundle_hash`, `snapshot_hash`, `export_hash`, `result_set_hash`,
`output_hash`, `structure_hash`).

## 4. Sort semantics

| Field | Order |
|---|---|
| DiscoveryHit list / ranks | provider order (significant) |
| QuerySpec.derived_from_hit_ids | significant (derivation order) |
| Snapshot member id sets | sort by id |
| Bundle segment_ids | sort by id |
| OriginAssessment pair | sort `(source_a, source_b)` by id |
| Export claims | sort by claim_id |
| Policy file list | sort by path |

## 5. bundle_hash coverage

The hash covers the canonical records in the frozen evidence closure, not only
their ids:

```text
request + campaign + query specs + search runs + discovery hits + collection snapshot
sources + retrievals + retrieval/artifact edges + artifacts
parse runs + parsed documents + segments
triage assessments + origin assessments
profile_id + policy_bundle_hash + selection_manifest
```

The validator recomputes every Segment normalized-text hash before computing
the bundle hash. Replica health and storage URI remain outside this domain.

Changing any selected assessment or evidence record requires a new Bundle.

## 6. export_hash coverage

The complete immutable export payload except `export_hash` itself.

## 7. policy_bundle_hash

Canonical hash of the policy documents listed in `policies/manifest.yaml`.
