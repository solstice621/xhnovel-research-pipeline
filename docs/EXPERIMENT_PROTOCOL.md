# Experiment execution protocol

This document defines how AI agents and humans must run experiments against
`xhnovel-research-pipeline`.

It is intentionally separate from development instructions. Development may
change the pipeline; experiments must evaluate the pipeline that already exists.

## Core rule

> Primary evidence generation must use the native xhnovel pipeline. Analysis may
> use custom scripts; SceneCandidate generation may not.

An experiment is invalid if its primary results were produced by a path that
**bypasses the native executor/task contract** — a bespoke splitter, a substitute
Scene Scout prompt, an out-of-band model-call loop not driven by a native task, a
replacement validator, a replacement merge, or a hand-edited SceneCandidate file.

Completing a native task packet **is** native execution, regardless of which
legal executor produced the answer (see below). It is not a bypass.

## Legal native executors

Two executors are native. Both flow through the identical production path
(`run_novel_research → build_scene_windows → executor.generate_json →
_validate_scout_output → merge_scene_candidates → EvidenceExport`); the only
difference is who answers the compiled task:

- **`API_EXECUTOR`** — an OpenAI-compatible model answers, selected with
  `--scout-model <model-snapshot>` (default `--executor api`).
- **`AGENT_FILES_EXECUTOR`** — the host code agent answers native task packets,
  selected with `--executor agent-files` (no model API key). xhnovel writes
  immutable tasks, the agent writes answers, and the same command is rerun to
  consume them. See [AGENT_FILES_EXECUTOR.md](AGENT_FILES_EXECUTOR.md) and
  [AGENT_EXECUTION.md](AGENT_EXECUTION.md).

A same-experiment run must not mix executors: the executor is bound into the build
identity, so pooling API and agent-files candidates changes the system under test.

## Required native execution path

For a direct-novel experiment, run the production entry point with the executor
under test:

```bash
# API executor
xhnovel-pipeline research-novel <spec.json> \
  --scout-model <model-snapshot> \
  --work-dir <work-dir>

# Agent-files executor (host code agent answers; no model API key)
xhnovel-pipeline research-novel <spec.json> \
  --executor agent-files \
  --work-dir <work-dir>
```

The primary result must therefore flow through the repository implementation:

```text
cli.py
  -> run_novel_research()
  -> run_novel_ingestion()
  -> prepare_novel_evidence_bundle()
  -> run_scene_scout()
  -> build_scene_windows()
  -> provider model call
  -> _validate_scout_output()
  -> merge_scene_candidates()
  -> EvidenceExport
```

Do not reimplement any stage merely because an experiment would be easier to
run that way.

## Required validation

Every successful experimental run must be checked with the native validator:

```bash
xhnovel-pipeline validate all <catalog.json> --store <objects-dir>
```

Additional targeted validation is allowed:

```bash
xhnovel-pipeline validate scene <catalog.json> --store <objects-dir>
xhnovel-pipeline validate evidence <catalog.json> --store <objects-dir>
xhnovel-pipeline validate export <catalog.json> --store <objects-dir>
```

Only SceneCandidates that survive the repository's native validation path may
enter experiment metrics.

A custom experiment script may inspect results, but its checks never replace the
native validators.

## Prohibited experiment shortcuts

Do not:

- split source text into model windows in an experiment script;
- construct a substitute Scene Scout prompt or structured-output request;
- call a model API directly, out of band, to generate the primary experimental
  candidates (completing a native task packet in `--executor agent-files` mode is
  not this — it is native execution);
- rewrite or repair provider offsets outside the production pipeline;
- implement an alternative support-span closure;
- suppress rejected provider outputs and replace them with hand-corrected JSON;
- implement a replacement candidate merge;
- invent a parallel SceneCandidate representation and treat it as production
  output;
- bypass EvidenceBundle, rights, source-quality, or triage gates;
- hand-edit `scene-candidates.json` before computing metrics;
- modify the production prompt, schema, validator, or merge halfway through an
  experiment and pool pre-change and post-change results.

If the native pipeline cannot execute the experiment, stop and report a pipeline
blocker. Do not build a temporary runner that silently changes the system under
test.

## What experiment-specific code may do

Custom analysis code may read immutable/native outputs such as:

- `catalog.json`;
- `scene-scout-run.json`;
- `scene-merge-run.json`;
- `scene-candidates.json`;
- `evidence-export.json`;
- provider/model-attempt artifacts retained by the run;
- preregistered gold annotations.

It may then:

- match candidates by source-span overlap;
- compute recall, precision, query separation, acceptance rate, cost, and merge
  metrics;
- stratify or sample candidates for manual review;
- generate tables and experiment reports.

It must not generate, repair, enrich, or re-merge primary SceneCandidates.

## Provider rejection handling

A rejected model output is an experimental observation, not missing data to be
fixed outside the pipeline.

For every rejected window preserve, when available:

- the exact provider response artifact;
- the ModelAttempt record and receipt;
- the request artifact;
- the native rejection code/reason;
- checkpoint/replay state.

Report at minimum:

```text
total native SceneWindows
accepted windows
rejected windows
rejection reasons
```

Never convert a native rejection into an accepted candidate with an auxiliary
model call or hand-written repair.

## Query-sensitive experiments

When comparing discovery briefs, every run must use the same:

- frozen source bytes;
- source rights declaration;
- source quality declaration;
- code commit;
- profile and schema;
- model snapshot;
- Scene Scout parameters;
- native SceneWindows, modulo the request identity produced by the changed
  discovery brief.

Only the preregistered query variable should change.

A same-query repeat such as `A1 / B / A2` should use byte-identical A1 and A2
`discovery_brief` values.

Every experiment spec must explicitly contain:

```json
{
  "request": {
    "discovery_brief": "<preregistered query>"
  }
}
```

Do not rely on a default discovery brief for a query-sensitivity experiment.

## Multi-agent experiments

Use multiple agents when independence materially improves the experiment. A
recommended separation is:

### Corpus agent

Creates or acquires the legally usable source corpus and records rights. For
synthetic-domain experiments, it should not inspect Scene Scout prompts,
schemas, validators, or previous model outputs before freezing the corpus.

### Gold-annotation agent

Reads the frozen corpus and creates preregistered expected scenes / hard
negatives. It must not read the experimental Scene Scout outputs before freezing
gold.

### Pipeline-execution agent

Creates the experiment specs and runs only the native xhnovel CLI / production
entry points. It records native artifacts and validator results and does not edit
the corpus or gold.

### Evaluation agent

After all runs are frozen, computes metrics from native validated outputs and the
preregistered gold. It may not change SceneCandidates.

### Adversarial-review agent

Checks provenance and protocol compliance, including whether any custom runner,
manual candidate repair, gold leakage, or omitted rejected window invalidates
the experiment.

One coordinating agent may orchestrate these roles, but the separation of input
creation, gold creation, pipeline execution, and evaluation must remain explicit.

## Minimum provenance checklist

Before interpreting experiment metrics, answer all of the following:

1. Were all primary runs produced by `xhnovel-pipeline research-novel` (or the
   explicitly selected native workflow being tested)?
2. Were SceneWindows generated by the repository implementation?
3. Were the executor requests — provider request (API) or task packet
   (agent-files) — constructed by the repository implementation?
4. Did outputs pass the production `_validate_scout_output` / replay path rather
   than an experiment-specific validator?
5. Was the production merge used?
6. Did the resulting catalog pass `xhnovel-pipeline validate all`?
7. Did any custom code modify, repair, generate, or re-merge a primary
   SceneCandidate? (Answers produced by completing a native task packet and passing
   the production validator are native, not custom repair.)
8. Did any code path bypass the native executor/task contract — a bespoke
   splitter, a substitute prompt, or an out-of-band model call **not** driven by a
   native task?
9. Were rejected provider/agent outputs retained rather than silently removed from
   the audit record?
10. Were corpus/gold/query definitions frozen before viewing the corresponding
    experimental results?
11. For agent-files runs: was every answer produced by completing a native task
    packet and validated by the production path (not hand-authored outside the
    task/answer contract)?

If item 7 or 8 is **yes**, the experiment is invalid and must not receive a
product-level verdict.

If another item fails, report the deviation explicitly before interpreting the
metrics.

## Code changes discovered by experiments

Experiments may reveal a real product defect. When that happens:

1. freeze the failing run and its raw artifacts;
2. report the smallest reproducible failure;
3. stop the preregistered experiment if the defect prevents valid measurement;
4. fix and review the pipeline in a separate commit;
5. rerun as a new experiment/version rather than mixing old and new results.

This keeps experimental evidence distinct from implementation iteration.

## Rights boundary

Technical access is not permission. Experiments must use the same rights gates
as production research.

Do not assume that a publicly reachable novel may be stored or sent to an
external model. Use public-domain, appropriately licensed, user-authorized, or
otherwise explicitly permitted material and record the declaration in the
native input spec.

## Relationship to the workflow documentation

`docs/NOVEL_WORKFLOW.md` defines what the pipeline does and its trust boundary.
This document defines how to use that pipeline as the system under test without
accidentally replacing it during an experiment.
