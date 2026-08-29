# ADR-0003 — Canonical hashing

status: accepted  
date: 2026-08-29

## Decision

Artifact identity is SHA-256 of raw bytes. All other identities that need
stable hashes use canonical JSON as defined in
`HASHING_AND_CANONICALIZATION.md`. YAML formatting never enters a hash.

## Consequences

Placeholder hashes fail closed. Replica metadata cannot be stuffed into
`artifact_id`. Bundle and export hashes omit their own hash fields.
