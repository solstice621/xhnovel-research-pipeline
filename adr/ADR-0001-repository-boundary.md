# ADR-0001 — Repository boundary

status: accepted  
date: 2026-08-29

## Decision

`xhnovel-research-pipeline` is the sole producer of collection, parse,
extraction, qualification and EvidenceExport. `xuanhuan-sandbox` is the first
consumer: it owns research questions, project mapping and design decisions.

The two repositories do not share an active worktree or submodule. They lock
each other with commit, schema version, bundle hash and export hash.

## Consequences

Extraction cannot read sandbox Project Context. Export cannot contain
`COVERED`, `NOT_A_GAP`, `current_holder` or design-map patches.
