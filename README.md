# xhnovel research pipeline

The research goal is to identify situations from xuanhuan novels that we want a
world to support, examine what makes them possible and how they could be realized,
and work backward toward a small set of reusable rules. These situations can
include contests of wits, romantic encounters, and exploration of secret realms.

The compiler ingests Chinese novels and supports two native research paths:
query-sensitive Scene discovery and Profile-based Generic extraction. Both reuse
immutable ingestion, exact normalized source spans, bounded semantic execution,
checkpoints, and deterministic replay.

```text
TXT / directory / EPUB / bounded static site
  -> immutable source snapshot and chapter map
  -> deterministic source/right classification
     |
     +-- Scene: frozen EvidenceBundle
     |     -> 8k-12k character overlapping SceneWindows
     |     -> parallel Scene Scouts using the discovery brief
     |     -> deterministic local and work-order merge
     |     -> DRAFT / UNVERIFIED SceneCandidates
     |
     +-- Generic: Profile-neutral NovelTextSnapshot
           -> versioned Extraction Profile and deterministic ExtractionUnits
           -> structured semantic extraction and exact source support
           -> deterministic reduction
           -> CorpusSnapshot and domain records
```

Generic extraction uses `xhnovel-extract`; observation research plans and tracks
that execution through Generic Handoffs and campaigns. Observation goals and
search hints stay outside the strict source-only Generic Novel Spec and native
tasks. Stage A results remain `UNQUALIFIED`, with coverage `UNMEASURED`.

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
authorized agent. Preserve an existing attestation unchanged. When a
SourceDeclaration omits rights, `prepare-handoff` fills them from the standing
attestation; explicitly supplied rights must match it exactly, or preparation
rejects with `E-PHASE0-ATTEST-MISMATCH`. Delegation must be explicit and recorded;
the acting agent cannot infer or self-grant it. Source quality is independent: `COMPLETE` plus
`OFFICIAL` is Tier A; `COMPLETE` plus `PUBLISHED_EDITION`,
`USER_VERIFIED_COPY`, `UNKNOWN`, or a positively declared `UNOFFICIAL_COPY` is
Tier B (operator research policy, 2026-09). Incomplete text remains Tier D /
lead-only. These semantics are identified as
`novel-source-classifier-v3` in generated assessments. A publisher licence is
not required merely to classify an otherwise complete source whose official
status is unproven.
The Scene send and export boundaries validate the complete Bundle → Snapshot → Ingestion
lineage, including induced members and deterministic triage, and read rights from
the immutable ingestion spec. A full validation checks each core stage once;
nested export checks reuse that validated lineage. Free-text evidence is not
filtered by a vocabulary blacklist. Export manifests mark the audit closure
`WITHHELD_BY_RIGHTS` when excerpts may not be distributed.

## Quick start

Requires Python 3.11 or later. This example ingests synthetic fixture chapters;
ingestion alone does not perform semantic research.

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


## Generic extraction and validation

List the installed built-in Profiles with `xhnovel-extract list-profiles`.
For direct Generic execution, use a prepared source-only `SPEC` and a fixed
`PROFILE` selected from that list; `W` is the native work directory. These are
placeholders for actual inputs, not supplied example files. The Scene example
above contains a discovery brief and is not a strict Generic Handoff spec.

```bash
xhnovel-extract run SPEC --profile PROFILE --executor agent-files --work-dir W
# Complete the native tasks at the returned paths, then rerun the same command.
xhnovel-extract run SPEC --profile PROFILE --executor agent-files --work-dir W
xhnovel-extract validate SPEC --profile PROFILE --work-dir W
```

The Generic API executor uses `--executor api --model MODEL`. For an observation
campaign, follow the [command workflow](docs/OBSERVATION_RESEARCH_WORKFLOW.md)
and execute through its campaign entry point so attempts and budgets are recorded.

Validation follows the output's native path:

| Output | Validation entry point |
|---|---|
| Scene Catalog and CAS | `xhnovel-pipeline validate all CATALOG --store STORE` |
| Generic extraction and corpus | `xhnovel-extract validate SPEC --profile PROFILE --work-dir W` |
| Planning, Handoffs, execution receipts, campaigns | Their corresponding validators in the [Phase 0](docs/PHASE0_INTERFACE.md) and [observation workflow](docs/OBSERVATION_RESEARCH_WORKFLOW.md) |
| Library sources, products, reports | The [library verification and replay commands](docs/LOCAL_RESEARCH_LIBRARY_USAGE.md) |

`validate all` covers the Scene path; it does not validate Generic corpora or
host library records. A native validation pass proves the implemented integrity
and replay checks, not semantic qualification or measured recall.

## Running experiments

If an AI agent or human is evaluating the pipeline, read
[docs/EXPERIMENT_PROTOCOL.md](docs/EXPERIMENT_PROTOCOL.md) before running the
experiment. Primary evidence generation must use the native xhnovel workflow;
custom scripts may analyze native outputs but must not replace SceneWindows,
model request construction, validation, merging, replay, or SceneCandidate
generation. Multi-agent experiments should keep corpus creation, gold annotation,
pipeline execution, evaluation, and adversarial review explicitly separated.

The Scene runtime prompt and structured-output schema are the exact files under
`profiles/xuanhuan-gameplay-scene-v1/`; their bytes are bound into the model
build identity. See [docs/NOVEL_WORKFLOW.md](docs/NOVEL_WORKFLOW.md) for the
input contract, checkpoints, outputs, and trust boundaries.
Generic tasks bind the shared core prompt and the selected Profile's assets under
`profiles/generic/`; they do not reuse the Scene discovery brief.

## Planning and source preparation

For new planned Scene research, start with the
[xhnovel-plan Skill](.agents/skills/xhnovel-plan/SKILL.md): preserve the request in
ResearchIntake, seal the seed-free NeutralPlanningInput and NeutralResearchFrame,
then compile ExplorationPlan and ExplorationBrief before Phase 0 exploration.
The host authors semantic drafts; `seal-intake`, `seal-neutral-frame`, and
`compile-exploration-plan` own deterministic sealing and compilation. Seeds steer
exploration only. See [Phase -1 planning](docs/PHASE0_EXPLORATION.md#phase--1-freeze-planning-before-exploration)
for input isolation and planning-to-Handoff lineage. Observation research uses
its own Definition and ProfileResolution path described below.

Already-selected works do not require web scene hypotheses before preparation.
For library research with sealed planning and a complete sealed source, use
`prepare-scene-work` in the [host continuation guide](docs/HOST_RESEARCH_CONTINUATION.md).
It prepares the ordinary Handoff and registers the source and execution, returning
the native commands to continue. The same guide covers the checkout launcher
`python scripts/xhnovel.py` and `research-status` reconciliation before resuming or
reporting completion; Observation still uses its native campaign report.

For a research request, the host Skills continue through source discovery,
acquisition/import, completeness and fidelity review, source freezing, native
task completion and reporting. The host owns the commands and configuration;
the user need not provide a novel file or restart each stage by default. See the
[shared source workflow](docs/SOURCE_ACQUISITION_WORKFLOW.md) for both Scene and
Observation routes and their bounded stop conditions. The compiler remains a
deterministic tool; automatic progression is implemented in the host Skills.

The host tools `scripts/source_acquisition.py` and `scripts/research_library.py`
run from a trusted repository checkout. They acquire/import and seal reviewed
chapter sources, allocate research directories, and replay native results before
evidence lookup. The default library root is `~/Documents/xhnovel-library`.
See the [source commands](docs/SOURCE_ACQUISITION_USAGE.md),
[library commands](docs/LOCAL_RESEARCH_LIBRARY_USAGE.md), and
[library design](docs/LOCAL_RESEARCH_LIBRARY_DESIGN.md).

Source sealing, native freezing, semantic execution, and product registration are
separate states. Library lists require source verification before reuse; SQLite
is a rebuildable metadata index. Chapter text matches are search results, not
evidence observations. Sources remain chapter-based; full-book TXT generation is
not included. Host tools and library schemas are not shipped in the core wheel.

## Observation-driven research

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

## Mechanism research notes

[World systems and mechanism research](docs/mechanisms/README.md) collects manual
research notes under six world-system areas, starting from concrete situations
and their contributing factors, possible implementations, and candidate rules.
Web findings remain lead-only, and design hypotheses are distinguished from
adopted choices. These notes do not extend the Evidence Compiler or produce
`MechanismCandidate` artifacts.
