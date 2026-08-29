# ADR-0004 — Artifact durability

status: accepted  
date: 2026-08-29

## Decision

Git is not the artifact data lake. Formal export requires every cited
artifact to have non-`EPHEMERAL` durability in at least one replica.
Retention-limited objects must advertise degraded auditability instead of
pretending full replay.

Local development may use `.runtime/objects/sha256/`. Writes use
temp → flush → atomic rename → verify hash.

## Consequences

`/tmp` materials cannot qualify a bundle. Missing replicas become
`MISSING`/`CORRUPT` on `ArtifactReplicaStatus` without mutating Artifact bytes
or historical exports.
