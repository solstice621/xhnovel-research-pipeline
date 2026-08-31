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

Scene Scout output is not a verified fact or a formal claim. Every known field
must cite an exact normalized-text `(segment_id, start, end)` span. Unknown and
conflicting observations are represented structurally; conflicts become
`NEEDS_ADJUDICATION`. A successful run may contain zero candidates.

Rights and trust are explicit. Technical access never implies permission. The
research command requires a declared non-`UNKNOWN` rights basis,
`may_store_full_text: true`, and `may_send_to_external_model: true`. Only source
quality classified as Tier A or B is eligible for event-scene discovery; Tier D
content remains `lead-only` and is never sent to the Scene Scout.

Quick start:

```powershell
python -m pip install -e ".[dev]"
xhnovel-pipeline ingest-novel examples/novel-direct.json --work-dir .runtime/demo
xhnovel-pipeline research-novel examples/novel-direct.json `
  --scout-model <model-snapshot> --work-dir .runtime/demo-research
python -m pytest
```

The runtime prompt and structured-output schema are the exact files under
`profiles/xuanhuan-gameplay-scene-v1/`; their bytes are bound into the model
build identity. See [docs/NOVEL_WORKFLOW.md](docs/NOVEL_WORKFLOW.md) for the
input contract, checkpoints, outputs, and trust boundaries.
