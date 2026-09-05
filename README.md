# xhnovel research pipeline

`xhnovel-pipeline` ingests Chinese novels and performs query-sensitive discovery
of gameplay-relevant scenes. It is designed for long works: source ingestion and
model work are replayable, scene windows overlap, model calls use bounded
concurrency, and an interrupted run resumes only unfinished windows.

The primary path is:

```text
TXT / directory / EPUB / bounded static site
  -> immutable source snapshot and chapter map
  -> deterministic source/right classification
  -> frozen EvidenceBundle
  -> 8k-12k character overlapping SceneWindows
  -> parallel Scene Scouts using the discovery brief
  -> deterministic local and work-order merge
  -> DRAFT / UNVERIFIED SceneCandidates
```

Scene Scout output is not a verified fact or a formal claim. Every known or
conflicting field must cite an exact normalized-text `(segment_id, start, end)`
span; conflicting fields carry at least two values. Merged candidates retain each
observation's exact support span even when their broader scene spans coalesce.
Unknown observations have no values or support. Conflicts become
`NEEDS_ADJUDICATION`. A successful run may contain zero candidates.

Rights and trust are explicit. Technical access never implies permission. The
research command requires a declared non-`UNKNOWN` rights basis,
`may_store_full_text: true`, and `may_send_to_external_model: true`.
Phase 0 exploration may reuse one standing `operator-attestation.json` at the
work root: the operator declares rights once, directly or through an explicitly
authorized agent, and `prepare-handoff` auto-applies them to each
SourceDeclaration. Delegation must be explicit and recorded; the acting agent
cannot infer or self-grant it. Source quality is independent: `COMPLETE` plus
`OFFICIAL` is Tier A; `COMPLETE` plus `PUBLISHED_EDITION`,
`USER_VERIFIED_COPY`, `UNKNOWN`, or a positively declared `UNOFFICIAL_COPY` is
Tier B (operator research policy, 2026-09). Incomplete text remains Tier D /
lead-only. These semantics are identified as
`novel-source-classifier-v3` in generated assessments. A publisher licence is
not required merely to classify an otherwise complete source whose official
status is unproven.
The send and export boundaries validate the complete Bundle → Snapshot → Ingestion
lineage, including induced members and deterministic triage, and read rights from
the immutable ingestion spec. A full validation checks each core stage once;
nested export checks reuse that validated lineage. Free-text evidence is not
filtered by a vocabulary blacklist. Export manifests mark the audit closure
`WITHHELD_BY_RIGHTS` when excerpts may not be distributed.

Quick start:

```powershell
python -m pip install -e ".[dev]"
xhnovel-pipeline ingest-novel examples/novel-direct.json --work-dir .runtime/demo
python -m pytest
```

Scene discovery runs through one of two native executors.

**API executor** — an OpenAI-compatible model answers:

```powershell
xhnovel-pipeline research-novel examples/novel-direct.json `
  --scout-model <model-snapshot> --work-dir .runtime/demo-research
```

**Agent-files executor** — the host code agent (Cursor, Claude Code, Codex)
answers native tasks; no model API key. It is a two-pass flow: the first run
materializes tasks and exits with code **3 = `WAITING_FOR_AGENT` (not a failure)**;
you fill in one JSON answer per task, then rerun the identical command to complete:

```powershell
xhnovel-pipeline research-novel examples/novel-direct.json `
  --executor agent-files --work-dir .runtime/demo-research
# exit 3: answer each task under .runtime/demo-research/scene-scout/agent-files/tasks/
xhnovel-pipeline research-novel examples/novel-direct.json `
  --executor agent-files --work-dir .runtime/demo-research
```

See [.agents/skills/xhnovel-agent-files/SKILL.md](.agents/skills/xhnovel-agent-files/SKILL.md) for
the host-agent operating contract and
[docs/AGENT_EXECUTION.md](docs/AGENT_EXECUTION.md) for worker sandboxing.


## Running experiments

If an AI agent or human is evaluating the pipeline, read
[docs/EXPERIMENT_PROTOCOL.md](docs/EXPERIMENT_PROTOCOL.md) before running the
experiment. Primary evidence generation must use the native xhnovel workflow;
custom scripts may analyze native outputs but must not replace SceneWindows,
model request construction, validation, merging, replay, or SceneCandidate
generation. Multi-agent experiments should keep corpus creation, gold annotation,
pipeline execution, evaluation, and adversarial review explicitly separated.

The runtime prompt and structured-output schema are the exact files under
`profiles/xuanhuan-gameplay-scene-v1/`; their bytes are bound into the model
build identity. See [docs/NOVEL_WORKFLOW.md](docs/NOVEL_WORKFLOW.md) for the
input contract, checkpoints, outputs, and trust boundaries.

## Observation-driven research

For a research request, the host Skills continue through source discovery,
acquisition/import, completeness and fidelity review, source freezing, native
task completion and reporting. The host owns the commands and configuration;
the user need not provide a novel file or restart each stage by default. See the
[shared source workflow](docs/SOURCE_ACQUISITION_WORKFLOW.md) for both Scene and
Observation routes and their bounded stop conditions. The compiler remains a
deterministic tool; automatic progression is implemented in the host Skills.

The [observation research architecture](docs/OBSERVATION_RESEARCH_ARCHITECTURE.md)
and [build plan](docs/OBSERVATION_RESEARCH_BUILD_PLAN.md) define the workflow:
research goal → fixed Profile selection → source discovery → native generic
extraction → an auditable research report. The Stage A implementation adds strict
planning contracts, Generic Handoffs, resumable execution receipts and campaign
reports over the existing compiler. Start with the
[xhnovel-observe Skill](.agents/skills/xhnovel-observe/SKILL.md) or
[command workflow](docs/OBSERVATION_RESEARCH_WORKFLOW.md).

The host performs semantic planning and searches. Existing Profiles may be reused;
new Profile admission and independent quality qualification are later stages.
Results remain `UNQUALIFIED`, coverage `UNMEASURED`. Scene Scout retains its
separate query-sensitive path. See the build plan for the actual validation gates;
implementation availability does not by itself close those gates. The
[Stage A validation record](docs/OBSERVATION_RESEARCH_STAGE_A_VALIDATION.md) maps
the implemented boundaries to tests. The Ubuntu/Windows CI workflow is removed;
on 2026-09-05 the user retired A-24 as a Stage A gate. Local checks remain required,
and unexecuted platforms are not claimed as verified.
