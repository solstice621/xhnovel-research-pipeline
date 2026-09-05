# Continuing a research request

For complete research requests, the host carries the work through native execution
and reporting within the confirmed scope and available budget. A planning-only
request ends at planning. This guide is shared by the Scene and Observation Skills.

## Checkout entrypoint

From the trusted repository checkout, use the selected project Python:

```bash
python scripts/xhnovel.py doctor
python scripts/xhnovel.py --help
```

In the existing Skill examples, replace the `xhnovel-pipeline` executable with
`python scripts/xhnovel.py`; keep its arguments and exit codes unchanged.
The launcher imports this checkout, so an editable installation pointing at a
deleted temporary worktree cannot select the wrong runtime. It does not install
dependencies or change global Python configuration. Doctor reports the interpreter,
module paths and native source-tree hash. Missing dependencies are an environment
issue, not a reason to ask the user to redefine the research.

## Reconcile before continuing or stopping

Allocate a new research with the local library before neutral planning. Preserve
the existing P/R roots when continuing an already-frozen research. After Scene
planning, on recovery, and before a final status claim, use the actual returned
record ID and paths:

```bash
python scripts/research_library.py --library-root L research-status RESEARCH_RECORD_ID --planning-root P
```

Optional repeated `--legacy-root OLD_P` inspects Handoffs and retained
`sources/*/book.txt` in explicitly supplied legacy roots without registering them.
Repeated `--acquisition-root SOURCE_RUN` replays acquisition inputs and coverage
without writing a verification report. Use `--attestation-root ROOT` to inspect a
specific standing attestation; the default is the repository's `attestations/`.
After planning, `--work-ref-id WORK_REF_ID` looks up sources for that exact known
work. A genre-word lookup returning no works is not proof that a book is absent.

The view reads immutable library registrations and calls production validators.
It does not refresh the SQLite index, advance native execution or acquire sources;
production validators retain their ordinary locking behavior. It is not an atomic
snapshot or an evidence record. Recheck affected inputs at the actual write.
An unavailable item retains its error while independent items remain inspectable.

For Observation, continue to use the native campaign report for Definition,
Profile, scope and budget. The library view can inspect linked Generic executions;
its `CHECK_NATIVE_CAMPAIGN` annotation is not Scene planning assurance.

Read the dimensions separately:

- Planning replay and the visible Brief/Plan must agree before reuse.
- A valid standing attestation can be copied unchanged into a new work root.
  Validate each source's declarations; a missing or conflicting grant needs an
  actual error before treating it as a user blocker.
- Source eligibility comes from production replay, not a historical PASS, report,
  declaration of COMPLETE, file size or modification time.
- A historical WAITING count is distinct from files currently present.
  `LOCATORS_ONLY` and `current_pending_count=null` mean tasks have not been
  regenerated/validated by the native wrapper. Resume that wrapper to obtain
  its current pending tasks; answer files present are not yet consumed results.
- A legacy Handoff is unregistered research, even if it validates. Check its
  original Brief, source and W before adopting it. Reusing source bytes for a new
  Brief requires a new ordinary Handoff; do not reuse old semantic answers.
- A deleted acquisition cache is not a resumable source. A separate retained text
  may enter a new import and quality review, but is not automatically COMPLETE.

## Choose the next action

Within the frozen selection budget, scope and diversity, prefer compatible
existing executions, then verified reusable sources, then bounded new source
work. This is execution order, not permission to drop less convenient selected
works. The host chooses routine ordering without asking which book to start.

| Current fact | Host action |
|---|---|
| Plan sealed, no local text | Select candidates and begin the shared source workflow |
| Source present, quality unresolved | Perform the bounded import/review/verification |
| Handoff ready | Freeze and execute with the same declared P/W |
| WAITING_FOR_AGENT | Read and answer native tasks; rerun the same wrapper |
| FAILED | Inspect the error and remaining budget; use native explicit retry when appropriate |
| Another task is acquiring this source | Use the shared acquisition result when available; work on other eligible items |
| Native success | Validate/register products and finish the research report |

### Bind execution and register the delivery

`ALLOCATE_EXECUTION` identifies a registered source whose Handoff replays against
this supplied planning root. Check the frozen research request and P, then allocate
before choosing W. Use the returned `record.work_dir` exactly; do not invent a
`campaign` directory inside the research library.

```bash
python scripts/research_library.py --library-root L allocate-execution RESEARCH_RECORD_ID SOURCE_RECORD_ID --handoff HANDOFF --native-root P --key KEY
```

Keep the returned execution record ID. Freeze and execute its ordinary Handoff
with that W according to the Scene or Observation Skill. On native success,
locate the actual native execution receipt returned by that wrapper, then:

```bash
python scripts/research_library.py --library-root L register-product EXECUTION_RECORD_ID --receipt RECEIPT_PATH
python scripts/research_library.py --library-root L register-report RESEARCH_RECORD_ID REPORT_PATH --executions EXECUTION_RECORD_ID --products PRODUCT_RECORD_ID
python scripts/research_library.py --library-root L research-status RESEARCH_RECORD_ID --planning-root P
```

Write the report in the allocated execution's sibling `reports/` directory before
registering it. Retain actual returned IDs. A terminal execution with
`REGISTER_PRODUCT` or `WRITE_AND_REGISTER_REPORT` still has delivery work remaining.
`REVIEW_RESEARCH_COVERAGE` means review the frozen scope, source coverage and actual
findings; registrations and valid citations do not prove semantic completeness.
For an inherited external W, use the existing external-registration workflow.

Native source budgets, frozen selection budgets, and host time/context limits are
different constraints. Measure a small batch before predicting throughput. At an
actual limit, preserve the native task/answer files and record the exact command,
P/W/Handoff and remaining work in the existing execution reports directory.
Recovery replays native state; the host note never replaces it. Do not promise a
background continuation unless the host actually has an authorized wakeup running.

The [host acceptance protocol](HOST_RESEARCH_CONTINUATION_ACCEPTANCE.md) verifies
fresh-session execution and recovery with a small complete synthetic source.

## When a final response is warranted

Finish when the requested scope is complete, the user requested a finite stage,
or actual blocked dependencies/budget limits leave no currently permitted work.
If one selected work is blocked, continue independent eligible work first.
Ask only for the specific missing information or permission that changes the next
action. Previously confirmed scope and valid standing permissions remain in force.

A blocked or paused report identifies the checked inputs, actual error or limit,
uncompleted works and concrete resume step. It does not claim completion merely
because planning, downloading or task materialization finished. A CLI status view
cannot force host behavior: validate this workflow using real fresh host sessions
and preserved native execution artifacts.
