# Migration guide

`legacy_contract_commit` is `ff8b8bb49685c411fd3b56bb61f9173e30680901`.
See `MIGRATION_BASELINE.md` and `MIGRATION_MAPPING.md`.

SCENE-001 is a legacy fixture and never auto-qualifies.
SCENE-002 is a tombstone plus a negative test family.

Old sandbox `research/scripts` remain reference copies under
`fixtures/legacy/sandbox-scripts-ff8b8bb/`. New validators are rewritten by
object, not by splitting the 691-line checker.

EXP-LIVE-RQ002 and EXP-LIVE-RQ003 were recorded as candidate imports, but they
do not establish a completed cutover while export verification and
qualification defects remain open. Whether sandbox has retired
`research/scenes/` and the old checker must be verified against an explicitly
fixed consumer commit; this document does not assert the consumer's current
state.

The integrity fixes are semantic migration boundaries. Existing candidate
exports, bundles, qualification records, assurance labels, and consumer locks
must not be silently upgraded. Audit or regenerate them after the fixes,
refresh affected fixtures and import locks, rerun cross-repo compatibility
tests, and create a new ExtractorBuild for requalification. If an existing
field changes meaning incompatibly, use an ADR and schema major-version bump
rather than reusing the old version.

`CollectionDecision` and `CollectionReview` are additive v0.1 object types, so
old Catalog payloads remain readable without synthetic review records. Adding
`collection-quality-v1` to the policy manifest changes the policy bundle hash:
new Bundles and Exports must be regenerated, and old snapshots do not inherit a
reviewed status. No historical ExtractorBuild is automatically requalified.
