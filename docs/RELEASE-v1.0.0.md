# v1.0.0 release

Package version: `1.0.0`
Export schema compatibility surface: `0.1-draft-frozen` (breaking schema changes still require an ADR).
Date: 2026-08-29

This release is the G12 gate: G0–G11 have runnable evidence on the implementation branch.

## What v1.0 commits to

- Offline Fake Provider slice: Request → verified EvidenceExport
- Wikipedia OpenSearch collection with recorded hits, selection, stop_reason
- Mechanical HTML/PDF parse; Wikipedia Vector void-tag skip fixed in `parser-html-pdf-v0.1.1`
- Frozen bundles; no in-place member edit
- Isolated mock extractor `BLD-MOCK-DETERMINISTIC-V1` with adversarial fixture PASS
- Dual-run positive fixture export assurance `BUNDLE_VERIFIED`
- Sandbox import lock; mapping cannot rewrite export bytes
- Two real requests (RQ-002, RQ-003) completed as `NO_QUALIFYING_CASE_FOUND`
- Legacy SCENE-001/002 remain unqualified fixtures; sandbox scenes/checker left the main path

Not in v1.0: production LLM extractor, UI, distributed scheduler, extra profiles.

## Drill (2026-08-29, local)

Commands run against `xhnovel-pipeline run local-slice fixtures/positive/minimal-local`:

| Command | Result |
|---|---|
| `verify-export` | OK on local-slice EXP-FIXTURE-001; assurance `BUNDLE_VERIFIED` |
| `explain-claim … CLM-MOCK-001` | chain Retrieval → Artifact → Segment + locator |
| `trace-request … REQ-FIXTURE-001` | campaigns, search_runs, hits, export id |
| `check-artifact` / `scan-artifacts` | all OK |
| `backup` → empty store `restore` | referenced artifacts restored |
| JSON field tamper of export | `E-EXPORT-TAMPER` |
| `gc --apply` on restore store with junk object | only unreferenced id removed |
| `revoke-export` on a **copy** | sidecar `export.revocation.json`; export bytes unchanged |
| `invalidate-build` on a **copied** registry | copy INVALIDATED; live `builds/extractors/registry.json` still QUALIFIED |
| `qualify fixtures/positive/minimal-local` | PASS / BLD-MOCK-DETERMINISTIC-V1 QUALIFIED |

Local-slice `export_hash` includes `producer.repository_commit`, so it changes when HEAD changes. Re-run `xhnovel-pipeline run local-slice` to refresh the consumer fixture import.

Live Wikipedia article bytes were **not** committed. Frozen consumer locks:

- EXP-LIVE-RQ002 `sha256:e8c4ca5fe0a7e65f942a38187d3255a0de57f78053cbf0c79c3e699606680ae3`
- EXP-LIVE-RQ003 `sha256:11c3b3a0be328925d3e7a9e16779b190405cbdf9ca294cb88d1585f305cde065`

## Incident pointers

See `docs/INCIDENT.md`. Revocation is a sidecar. Historical exports keep their producer commit; a new parser build does not rewrite them.
