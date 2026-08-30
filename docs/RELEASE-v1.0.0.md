# v1.0.0 release candidate (withdrawn)

Candidate package version: `1.0.0`
Candidate export schema compatibility surface: `0.1-draft-frozen`
Candidate date: 2026-08-29
Status: **WITHDRAWN / NOT RELEASED**

The previous G0–G12 complete statement is withdrawn. Adversarial review found P0 defects in export import verification, frozen bundle integrity, and redirect SSRF protection, plus P1 defects in qualification, claim lineage, and profile validation. The working tree contains remediations, but they do not retroactively validate this candidate or its historical evidence. G12 explicitly requires no unresolved P0/P1.

## Candidate claims requiring revalidation

- Offline Fake Provider slice: Request → candidate EvidenceExport
- Wikipedia OpenSearch collection with recorded hits, selection, stop_reason
- Mechanical HTML/PDF parse; Wikipedia Vector void-tag skip fixed in `parser-html-pdf-v0.1.1`
- Frozen bundle and claim lineage behavior, after integrity fixes
- Deterministic mock qualification with independent RUN-A/RUN-B execution, replayed PASS binding, and no claim of production LLM qualification
- Profile payload validation against the declared profile schema
- Sandbox import rejection of self-signed, incomplete, or unknown-version exports
- Collection completion, retry, and append-only history behavior
- Legacy cutover, after verification against a fixed consumer commit

Production LLM extractor, UI, distributed scheduler, and extra profiles remain outside this candidate.

## Historical drill (2026-08-29, invalid as release evidence)

The following commands were recorded against `xhnovel-pipeline run local-slice fixtures/positive/minimal-local`. They show what the candidate exercised, but the newly identified validator gaps mean their prior OK/PASS labels do not prove qualification, export integrity, or G12 completion.

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

Live Wikipedia article bytes were **not** committed. The following were recorded candidate consumer locks; they must not be treated as current consumer state or release evidence:

- EXP-LIVE-RQ002 `sha256:e8c4ca5fe0a7e65f942a38187d3255a0de57f78053cbf0c79c3e699606680ae3`
- EXP-LIVE-RQ003 `sha256:11c3b3a0be328925d3e7a9e16779b190405cbdf9ca294cb88d1585f305cde065`

## Incident pointers

See `docs/INCIDENT.md`. Revocation is a sidecar. Historical exports keep their producer commit; a new parser build does not rewrite them.

## Compatibility and requalification

The required fixes change security and validation semantics. Existing candidate exports, bundles, qualification records, and assurance labels must be treated as unqualified until audited and regenerated; they must not be silently relabeled. If a fix changes the meaning of an existing contract field or accepted export bytes incompatibly, it requires an ADR, migration note, fixture refresh, cross-repo import test, and the appropriate schema major-version change. Any affected extractor build must be replaced by a new build identity and requalified.
