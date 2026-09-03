# Novel ingestion and Scene Scout workflow

## Purpose and trust boundary

The executable workflow discovers source-grounded scene candidates relevant to
a user question. It does not score literary plot importance, run a whole-book
analysis call, or promote model output into facts. Model observations remain
`DRAFT / UNVERIFIED`; a separate future promotion workflow is required before
they can become accepted evidence.

The system can prove which immutable source bytes and model exchange produced a
candidate. Replayability does not prove that the model interpreted the passage
correctly.

## Direct research specification

```json
{
  "source": {
    "kind": "txt",
    "path": "book.txt",
    "title": "Example novel"
  },
  "rights": {
    "basis": "USER_AUTHORIZED_LOCAL_COPY",
    "may_store_full_text": true,
    "may_send_to_external_model": true,
    "may_export_excerpts": false
  },
  "source_quality": {
    "edition_status": "USER_VERIFIED_COPY",
    "textual_completeness": "COMPLETE"
  },
  "request": {
    "discovery_brief": "寻找对象控制转移中遭到抵抗并改变后续行动空间的场景"
  },
  "limits": {
    "max_chapters": 5000,
    "max_bytes": 500000000
  },
  "scene_scout": {
    "window_chars": 10000,
    "overlap_chars": 1800,
    "max_input_chars": 20000,
    "max_request_bytes": 2000000,
    "max_workers": 8
  },
  "strict_order": false
}
```

Supported local kinds are `txt`, `directory`/`chapter-directory`, and `epub`.
The bounded `site`/`static-site` adapter is also available but is not a rights
signal: HTTP 200 is recorded only as successful technical access.

The model stage is gated before ingestion starts. It requires:

- all four rights fields;
- `may_store_full_text: true`;
- a rights basis other than `UNKNOWN`;
- `may_send_to_external_model: true`.

Source quality is independent of rights and access. `OFFICIAL + COMPLETE` maps
to Tier A; `PUBLISHED_EDITION`, `USER_VERIFIED_COPY`, or `UNKNOWN` plus
`COMPLETE` maps to Tier B. `UNOFFICIAL_COPY` (even if complete) and any
non-complete text map to Tier D. Unproven official or licensed status is
`UNKNOWN`, not a filter. `UNOFFICIAL_COPY` is a positive unauthorized
declaration. A/B permits `event-facts`; D is `lead-only` and creates no
SceneWindows or model calls.

## Ingestion

Ingestion writes source bytes to a content-addressed store and records discovery,
fetch receipts, parsed documents, and segments. Checkpoints are integrity-bound
to the input specification and adapter build.

Important parsing rules:

- TXT content before the first numbered heading is retained as
  `FRONTMATTER`, not silently dropped.
- Physical single-newline TXT paragraphs remain separately addressable.
- TXT locators point to original decoded-text character offsets and line
  selectors.
- HTML locators retain the actual source node name instead of pretending every
  block is a paragraph.
- EPUB spine items are classified. Frontmatter and navigation items are
  retained as `IGNORED`; an unnumbered item does not abort the book.
- Unknown chapter numbers produce an ordering warning unless a real
  contradiction, duplicate, or gap violates strict ordering.

The ingestion run has a stable logical identity. Reusing a completed checkpoint
does not create a second run merely because cached work was reused.

## Frozen lineage and classification

The workflow creates one deterministic `TriageAssessment` per ready narrative
chapter, then freezes a `CollectionSnapshot` whose retrieval, artifact, triage,
request, and `NovelIngestionRun` closure is validated exactly. The resulting
`EvidenceBundle` includes selected, duplicate, and ignored chapter IDs.

The primary workflow makes no fixed per-chapter Collector/Reviewer calls.
Collection review types remain available only as isolated rubric-bound utility
contracts; their task surface is limited to `TRIAGE` and `CHAPTER_IDENTITY`.

## Query-sensitive overlapping Scene Scout

The user's `discovery_brief` is included in every model input and its hash is
bound to the run. Changing the brief changes the request and build lineage.

Eligible normalized segment text is concatenated in chapter and source order,
then divided into windows with these hard invariants:

- target length: 8,000 to 12,000 Unicode characters;
- overlap: 15% to 20%;
- default: 10,000 characters with 1,800-character overlap;
- exact full JSON request byte limit, including instructions and schema;
- no use of Tier D / `lead-only` text.

Every returned candidate cites exact source spans. Each observation field has a
status (`KNOWN`, `UNKNOWN`, or `CONFLICTING`), values, and its own supporting
spans. `KNOWN` requires at least one value and support span; `CONFLICTING`
requires at least two values and a support span; `UNKNOWN` has empty values and
support. Conflicting observations set the candidate to `NEEDS_ADJUDICATION`;
processing of other windows continues.

Duplicate candidates from overlapping windows are merged using source-span and
actor/action/target evidence. Local groups use complete-link overlap, and a
second complete-link work-order stage merges duplicates that cross chapter
boundaries without transitive wide-span bridging. Results are ordered by
chapter ordinal, segment ordinal, and exact span start. There is no whole-book
model analysis call.

## Concurrency, failure, and accounting

Scene calls use a bounded thread pool (`max_workers` defaults to 8 and is capped
at 64). A checkpoint is atomically rewritten after each completed future.
Failures are isolated: all other submitted windows finish, the checkpoint is
marked `PARTIAL`, and the command exits with `E-SCENE-PARTIAL`.

On the next run, successful windows are loaded from CAS and only incomplete or
failed windows are submitted again. Attempt ordinals and `retry_of` chains span
the interruption.

Each attempt has an immutable `ModelAttempt` receipt containing:

- the exact request and optional response artifact IDs;
- HTTP status and provider response ID;
- `SUCCEEDED`, `RETRYABLE`, `FAILED`, `REFUSED`, or `REJECTED` status;
- error code/message;
- input, output, and total token observations when supplied by the provider.

The run aggregates token usage and counts attempts with unknown usage. Estimated
cost remains `null` unless a separately versioned pricing source is available;
the pipeline does not invent prices.

## Outputs and replay

Successful research outputs are stored under
`research/<scene_scout_run_id>/`:

- `catalog.json`;
- `scene-scout-run.json`;
- `scene-merge-run.json`;
- `scene-candidates.json`;
- `evidence-export.json`;
- `run-summary.json`.

Output files are immutable. Validation reconstructs windows, requests, model
outputs, attempt chains, usage totals, merge results, exact candidates, and the
artifact-manifest closure. Model-backed exports are always `UNQUALIFIED` and
`DEGRADED`; changing that flag to `FULL` is rejected. The closure remains
available for private replay, while every manifest entry is marked
`WITHHELD_BY_RIGHTS` when the immutable ingestion declaration disallows excerpt
export.

Before model egress or export, a shared resolver runs the authoritative
ingestion, snapshot, evidence-member, and deterministic-triage validators. Only
after the actual Bundle members are proven to be induced by the same Ingestion
does it read that Ingestion's immutable rights. A permissive Snapshot therefore
cannot authorize text members from a different denied Ingestion, and a refrozen
Tier D assessment cannot self-promote to `event-facts`.

Commands:

```powershell
xhnovel-pipeline ingest-novel spec.json --work-dir .runtime/ingest
xhnovel-pipeline research-novel spec.json `
  --scout-model <model-snapshot> --work-dir .runtime/research
xhnovel-pipeline validate scene <catalog.json> --store <objects-dir>
xhnovel-pipeline validate all <catalog.json> --store <objects-dir>
```

`research-famous-novel` first performs a bounded Wikipedia OpenSearch title
relevance aggregation and resolves the first matching entry in the supplied
`source_catalog`. This is not a measurement of sales or popularity and does not
locate licensed full text automatically.

## Portability and distribution

Work-directory locking uses `fcntl` on POSIX and `msvcrt` on Windows. Runtime
contracts, policies, prompts, and schemas are included in the wheel under
`xhnovel_pipeline_data`; `repo_root()` resolves either a source checkout or an
installed distribution. CI runs pytest on Linux and Windows and performs a
clean wheel-install/CLI/resource smoke test on both platforms.
