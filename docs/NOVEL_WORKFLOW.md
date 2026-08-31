# Novel collection and plot workflow

This is an experimental, auditable workflow. It does not qualify a model build
or upgrade an export beyond `UNQUALIFIED`.

## Inputs

Direct ingestion accepts a JSON specification with one `source`:

- `txt`: one UTF-8 or detectable text file split by chapter headings;
- `epub`: EPUB spine order, with member and total uncompressed-size limits;
- `directory`: naturally sorted `.txt`, `.html`, `.htm`, or `.xhtml` files;
- `site`: a static HTML index plus bounded chapter and next-page URL patterns.

See `examples/novel-direct.json`. Relative local paths are resolved against the
specification file.

Minimal source configurations are:

```json
{"source": {"kind": "directory", "path": "chapters", "recursive": false}}
```

```json
{"source": {"kind": "txt", "path": "book.txt", "encoding": "auto"}}
```

```json
{
  "source": {
    "kind": "epub",
    "path": "book.epub",
    "max_member_bytes": 10000000,
    "max_total_bytes": 200000000
  }
}
```

```json
{
  "source": {
    "kind": "site",
    "index_url": "https://authorized.example/books/demo/index.html",
    "chapter_url_pattern": "/books/demo/chapters/[0-9]+\\.html$",
    "next_index_url_pattern": "[?&]page=[0-9]+$",
    "max_index_pages": 20,
    "max_index_bytes": 50000000,
    "max_chapters": 1000
  },
  "limits": {"max_chapters": 1000, "max_bytes": 100000000},
  "strict_order": true
}
```

Directory input recognizes `.txt`, `.html`, `.htm`, and `.xhtml` files and is
non-recursive unless `recursive` is true. TXT defaults to UTF-8/GB18030/Big5
detection and the built-in Chinese chapter-heading pattern; `encoding` and
`chapter_pattern` can override those defaults. EPUB follows package spine order.
Top-level `limits.max_chapters` bounds discovered chapters. `limits.max_bytes`
caps the cumulative fetched chapter bytes and is also applied before parsing any
single local source, site response, or EPUB container. EPUB's member and total
uncompressed-size limits are additional archive defenses. For the built-in site
transport, a smaller top-level byte limit is enforced while the response is read,
not only after download. Retained index-page provenance is cumulatively capped by
the smaller of `limits.max_bytes` and `source.max_index_bytes`; the latter defaults
to and cannot exceed 50 MB.

Site patterns are Python regular expressions matched against canonical absolute
URLs. Chapter and index redirects are checked again after fetching. Chapter
links are same-origin by default. `allow_external_chapters` can relax only the
chapter-origin check; pagination remains on the index origin. Do not enable it
unless the external chapter host is an intended, authorized source.

The site adapter uses the hardened HTTP fetcher, rejects unsupported MIME types,
checks every redirect, defaults to same-origin traversal, and does not render
JavaScript or bypass authentication, paywalls, CAPTCHAs, or access controls.
The user-declared `evidence` field, if present, survives only inside the
frozen original specification Artifact and is ignored when creating source
ratings. Formal novel triage is materialized only from the bound independent
review, and it does not qualify a model build, bundle, or export.

## Commands

Ingest and mechanically parse without model calls:

```text
xhnovel-pipeline ingest-novel examples/novel-direct.json \
  --work-dir .runtime/novels/demo
```

Validate the emitted catalog against its CAS (replace `<NING-ID>` with the ID
printed by ingestion):

```text
xhnovel-pipeline validate novel \
  .runtime/novels/demo/ingestions/<NING-ID>/catalog.json \
  --store .runtime/novels/demo/objects
```

Run collection review, plot extraction, cross-chapter analysis, and export:

```text
export OPENAI_API_KEY=...
xhnovel-pipeline research-novel examples/novel-direct.json \
  --collector-model <small-model-snapshot> \
  --reviewer-model <large-model-snapshot> \
  --extractor-model <extractor-model-snapshot> \
  --analyst-model <analyst-model-snapshot> \
  --work-dir .runtime/novel-research/demo
```

Collector and reviewer model identifiers must differ. For logical model calls
that complete and pass local validation, the exact model identifier, request
bytes, final successful response bytes, prompt, parameters, and frozen inputs
are retained in the CAS. API credentials are sent only as request headers and
are not written to artifacts.

The experimental v0.1 model path does not yet retain HTTP-error, refusal,
invalid-JSON, or invalid-schema responses as immutable failed runs. The client
also retries selected transient HTTP statuses automatically (up to three
attempts by default), but only the final successful response is retained; the
intermediate retry responses have no run record or `retry_of` lineage. Model-
backed novel exports therefore declare `auditability=DEGRADED`, even when the
command succeeds. `UNQUALIFIED` describes model/build assurance and does not
remove this audit-history limitation.

The current experimental review stage is deliberately conservative and not
cost-optimized: each ready chapter triggers two blind `CHAPTER_IDENTITY` calls
and two blind `TRIAGE` calls (collector plus reviewer), followed by batched plot
extraction and one whole-run analysis call. Check the ready chapter count and
model pricing before using `research-novel` on a full book; `ingest-novel`
performs no model calls and is the safe first pass.

## Automatic famous-novel workflow

`research-famous-novel` ranks candidates inside a declared provider/query/page
window, then selects the highest-ranked candidate that has exactly one matching
entry in `source_catalog`:

First copy the example and replace its placeholder EPUB path and example site
with sources you are authorized to access. The checked-in file is a
configuration template, not a runnable promise that those placeholder sources
exist.

```text
xhnovel-pipeline research-famous-novel /path/to/famous-novel-research.json \
  --collector-model <small-model-snapshot> \
  --reviewer-model <large-model-snapshot> \
  --work-dir .runtime/famous-novel-research/demo
```

`source_catalog` is deliberate. A ranking result identifies a work; it does not
prove that a search-result URL is the novel text or that it is an allowed input.
Entries match by exact `candidate_id` or normalized `candidate_titles` and carry
the concrete TXT, EPUB, directory, or site source configuration. Ambiguous
matches and missing sources fail closed.

The resulting `NovelSourceResolution` binds:

- ranking run and candidate rank;
- the complete declared source catalog Artifact;
- the selected ingestion specification Artifact and hash;
- the final ResearchRequest, Snapshot, Bundle, analysis, and export.

## Outputs and recovery

Chapter bytes, provider responses, model exchanges, policy files, checkpoints,
and manifests are content-addressed under the selected work directory. The
ingestion checkpoint is updated after every completed chapter. To resume, rerun
the same command with the same resolved specification and `--work-dir`. The
pipeline verifies the checkpoint and prior CAS objects and fetches only
unfinished chapters. A changed input specification, changed source bytes where
the adapter can bind the complete source, changed adapter build, corrupt CAS, or
corrupt checkpoint fails closed. Completed immutable run outputs are preserved;
a resumed run is a distinct `NovelIngestionRun` rather than an in-place rewrite.

SITE transport attempts are also retained before an ingestion run can finish.
Each index or chapter request writes an immutable receipt under
`site-attempts/`; failed attempts additionally write
`failures/<RET-ID>/failure-manifest.json` and retain any bounded response body
available from the transport. Retrying the same stage in the same work directory
links the new receipt to the prior one with `retry_of`. A successful final
catalog materializes that attempt history and its raw artifacts so Snapshot,
export-manifest, and garbage-collection live-set validation preserve the full
ingestion closure. Under `research-novel`, these records live inside its
ingestion work subdirectory rather than beside the final model outputs.

That recovery contract currently applies to ingestion only. Collection review,
plot extraction, and plot analysis do not have failed model-run checkpoints or
retry lineage; rerunning the command repeats those model stages and must not be
treated as a resumable, fully audited model run.

Use an explicit, durable work directory for any run that may need recovery. The
default directory is convenient for experiments but is derived from the spec
file name, so two different specs with the same name can collide. Do not use
`/tmp` as the only location for evidence intended to support an export.

Duplicate normalized chapter content is retained as lineage but excluded from
the extraction Bundle. Missing, duplicated, or decreasing declared chapter
numbers produce `WARNING`, or `FAILED` when `strict_order` is true.

The chapter-identity gate is deliberately narrower than an official edition
lookup. A deterministic check derives a chapter number from the first frozen
body segment and compares it with the discovered declared number (or discovery
ordinal when no declared number exists). The two model reviewers see only that
body-heading observation and the frozen segments; they are not shown the
discovered title or ordinal. A match therefore proves internal discovery/body
consistency only. It does not prove that the upstream site, directory, or EPUB
used the publisher's canonical chapter identity.

Successfully completed model request batches are replayed against frozen
Segment text. Claims must be exactly reproducible from the retained successful
extractor responses. Alias groups, event groups, timeline, and importance
scores must likewise replay from the retained successful analysis response and
policy. This success-path replay does not reconstruct unretained failed calls or
automatic-retry intermediates. Output files are immutable; conflicting reruns
do not overwrite them.

## Current boundary

The implemented live ranking provider is Chinese Wikipedia OpenSearch. Ranking
means reciprocal-rank fusion over the recorded window, not a universal measure
of fame. Static sites requiring browser execution are unsupported. A full-book
analysis must fit the configured analysis context limit; oversized analysis
fails explicitly instead of silently dropping claims.

## Exit status

- `0`: the requested command completed. `ingest-novel` may still report
  `status=PARTIAL` with a warning; inspect the emitted record before downstream
  use.
- `1`: a configuration, validation, access, provider, model, integrity, or
  immutable-output error prevented completion.
- `2`: `ingest-novel` completed an auditable record with `status=FAILED` (for
  example strict chapter-order failure). `argparse` also uses `2` for invalid
  command-line syntax and prints a `usage:` message.

Model-backed novel exports remain explicitly `UNQUALIFIED` and
`auditability=DEGRADED`. A successful exit means the experimental workflow and
its success-path validators completed; it is not ExtractorBuild qualification,
bundle verification, or proof that every failed/automatic-retry model exchange
was retained.

## Repository validation

Run the standalone boundary and regression suite with:

```text
python -m compileall -q src tests
python -m pytest
```

`tests/test_standalone_boundary.py` fails if a retired G0-G12 module or contract
is restored, a relative import is missing, or the policy manifest expands beyond
the three standalone runtime policies.
