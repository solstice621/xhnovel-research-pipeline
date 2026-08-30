# Legacy object mapping (old research/ → v0.1 domain)

This is a mapping draft, not an auto-upgrade. Historical scenes never gain
qualification by being copied here.

| Old object / field | New object | Notes |
|---|---|---|
| `research/README.md` workflow | not a runtime object | Consumer process; producer contract lives in this repo |
| RESEARCH-QUESTIONS.md row | `ResearchRequest.discovery_brief` origin | Consumer still owns why the question matters |
| `scene/` directory | not a root | Split into Request + Campaign + Snapshot + Bundle |
| `evidence.yaml` scene card | ResearchRequest + CampaignReport notes | Scene identity is not reproduced as a system root |
| `sources[]` | `Source` | Logical identity; no permanent tier |
| `sources[].retrievals[]` | `Retrieval` + `TriageAssessment` | Tier lives on the assessment |
| `access_kind: search_snippet` (and aliases) | Retrieval `access_kind` + forced Tier D | Snippet never becomes a page |
| `same_platform_as` / platform case-fold | `OriginAssessment` + platform class | UNKNOWN cannot satisfy dual-B |
| `materials.file` + `sha256` | `Artifact` + replica status | Bytes identity; no `/tmp`, no placeholder |
| `run_manifest` | `ExtractionRun.input_manifest` + `execution_environment` | Isolation proven by allowlist |
| `claims.yaml` ACTIVE row | `Claim` | Must cite Retrieval + Artifact + Segment + text hash |
| `LEGACY_UNRESOLVED` | Claim `status: ARCHIVED` or omit | Not live |
| `element_mapping` | **not produced** | Sandbox mapping after import |
| `COVERED / NOT_A_GAP / current_holder` | **forbidden in export** | Consumer-only |
| `qualification.md eligible_build_ids` | `ExtractorBuild` registry + `QualificationRun` | Build identity, not a model nickname |
| `schema_version: 0.1-legacy` SCENE-001 | `fixtures/legacy/scene-001` | Never auto-qualified |
| SCENE-002 tombstone | `fixtures/legacy/scene-002-tombstone` + negative family | 0 live claims; historical FAIL retained |

Checker attack tests map to new validators as follows:

| Old test | New gate |
|---|---|
| fake qualified / missing registry | `validate_qualification` |
| placeholder / mismatched / absolute path hashes | collection + qualification |
| snippet case/hyphen as B | collection triage |
| same platform / alias chain | origin independence |
| post-isolation source on live claim | evidence freeze |
| SUPERSEDED + ACTIVE | evidence |
| empty claims eligible | qualification |
| unauthorized reprint still allowed as A | access-kind policy (no tier cap) |
