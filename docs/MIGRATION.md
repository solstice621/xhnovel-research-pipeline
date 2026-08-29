# Migration guide

`legacy_contract_commit` is `ff8b8bb49685c411fd3b56bb61f9173e30680901`.
See `MIGRATION_BASELINE.md` and `MIGRATION_MAPPING.md`.

SCENE-001 is a legacy fixture and never auto-qualifies.
SCENE-002 is a tombstone plus a negative test family.

Old sandbox `research/scripts` remain reference copies under
`fixtures/legacy/sandbox-scripts-ff8b8bb/`. New validators are rewritten by
object, not by splitting the 691-line checker.
