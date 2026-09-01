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
span; conflicting fields carry at least two values. Unknown observations have
no values or support. Conflicts become `NEEDS_ADJUDICATION`. A successful run
may contain zero candidates.

Rights and trust are explicit. Technical access never implies permission. The
research command requires a declared non-`UNKNOWN` rights basis,
`may_store_full_text: true`, and `may_send_to_external_model: true`. Only source
quality classified as Tier A or B is eligible for event-scene discovery; Tier D
content remains `lead-only` and is never sent to the Scene Scout.
The send and export boundaries first validate the complete Bundle → Snapshot →
Ingestion lineage, including induced members and deterministic triage, then
re-resolve rights from the immutable ingestion spec. Export manifests mark the
audit closure `WITHHELD_BY_RIGHTS` when excerpts may not be distributed.

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
