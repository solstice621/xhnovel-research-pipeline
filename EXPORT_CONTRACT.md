# EvidenceExport contract v1

status: `0.1-draft-frozen`  
schema: `contracts/exports/xuanhuan-evidence-v1.schema.json`

## Producer payload

```text
schema_version
export_id
export_hash
producer.{repository_commit, collector_build_id, parser_build_id, extractor_build_id}
origin_request
bundle (id + bundle_hash)
claims
scene_facts          # claims/timeline only; no element_mapping
policies             # names + hashes
assurance
artifact_manifest    # artifact_id, byte_length, durability, availability
created_at
```

Missing producer commit/build, policy hash or bundle hash → verify fail.

One-byte mutation of the export document → `export_hash` mismatch.

If a required artifact is missing or `EPHEMERAL`, auditability must be
`LIMITED_BY_RETENTION_POLICY` or `DEGRADED`, never `FULL`.

## Consumer lock (sandbox)

```text
export_id
export_hash
producer_commit
schema_version
imported_at
```

Re-import of the same hash is idempotent. Mapping files must not modify
`export.yaml` bytes.

## Compatibility

Unknown future major versions are rejected. v1 readers may ignore unknown
fields only inside `profile_payload` that the profile schema allows; the export
envelope itself rejects unknown fields.
