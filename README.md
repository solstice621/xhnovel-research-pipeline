# xhnovel standalone novel pipeline

This branch contains a standalone, offline-first pipeline for ingesting Chinese
novels and producing replayable, explicitly unqualified plot-analysis records.
It is independent of the retired G0-G12 evidence workflow and of the
`xuanhuan-gameplay-probes` skill.

Current boundaries:

- local TXT, directory and EPUB ingestion;
- bounded static-site ingestion with resumable checkpoints;
- content-addressed artifacts and schema-validated run records;
- title ranking, source resolution and independent collection review;
- model-backed plot extraction and analysis remain `UNQUALIFIED` and exports
  remain `DEGRADED` until a separate accuracy qualification exists.

The package never treats model output as verified source truth. See
`docs/NOVEL_WORKFLOW.md` for the executable contract and trust boundaries.
