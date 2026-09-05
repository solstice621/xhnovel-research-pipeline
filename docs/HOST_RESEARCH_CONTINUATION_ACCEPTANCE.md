# Host continuation acceptance

Code tests verify native invariants; they cannot prove that a host follows a
Skill. Run this acceptance after fixing the checkout/build used for the run.
Keep generated material outside tracked source files.

## Environment

Use a stable project interpreter with the project dependencies installed.
`python scripts/xhnovel.py doctor` must resolve the selected checkout; record its
output and the actual interpreter. Do not repair a deleted global editable
installation by modifying unrelated global packages.

Record the host version, configured model, code commit/source-tree hash and the
actual Skill content loaded in its tool trace. Use the normal configured model
and permission policy; do not introduce a model fallback for the acceptance.

## Small complete source

Create a short, intentionally complete synthetic source, its catalog and explicit
fixture rights. Use the production acquisition/import/review/seal path. Fixture
construction can attest structural completeness because its entire authored text
is known, but this is not a test of commercial-source acquisition or fidelity.

Freeze a one-work Scene research with seed-free scope, one selected Lead, normal
planning receipt/CAS and a compatible Handoff. A deterministic fixture's neutral
frame is NOT_PROVEN rather than a claimed independent host judgment. Register the
research and source in an isolated library; leave native execution unstarted.
No expected Scene Scout answer or gold is given to the host.

Ask a fresh OpenCode session, in natural language, to complete the already
confirmed research and deliver its final report. Give only the research/library
paths and input materials, normal task scope and rights. Allow local tool use and
the host's semantic reasoning; no web acquisition is needed for this fixture.
Do not tell the host which bug is under test or supply its expected next actions.

Repeat with a second independent research/source/work directory and fresh session.
Then interrupt a separate execution after native task materialization, preserving
its original input, tasks and attempt records. Resume the same research in a fresh
host context, without resealing or silently replacing W.

## Evidence and pass criteria

Save the original prompt, JSON tool-event stream, host session ID, native
Handoff/spec, execution events/receipts, tasks/answers, products and final report.
Validate them with the same code build and a fresh process. For the recovery case,
verify the same input hash, W and attempt/retry lineage as applicable.

Required observations:

- No routine request to pick the first book, reconfirm an already confirmed
  scope/standing grant, supply an already available source, or answer native tasks.
- Native execution actually starts; WAITING_FOR_AGENT is followed by host-authored
  answers and native consumption, validation and report delivery.
- Native failures, actual host limits and unavailable dependencies remain visible.
  Task creation alone is PREPARED/WAITING, never successful research.
- No source/task modification or edits to production code by the evaluation host.
- Report findings are supported by the returned native artifacts, with the
  DRAFT/UNVERIFIED semantic assurance retained.

A host stopping at a question or running out of a real limit is recorded as a
failed or incomplete scenario. Preserve the run, fix only demonstrated problems,
and rerun a fresh scenario. Do not claim all long-form research is reliable from
two small-source successes.

## Selected-work entrypoint acceptance

For work-first workflow changes, create a separate fixture with a sealed complete
source and compiled planning but no ResearchLead, prepared Handoff, registered
source or execution. Give a fresh host only the already-selected work, library /
planning / sealed-source locations and normal research request. Do not provide a
scene hypothesis, expected semantic answer, dummy reference URL or gold.

The host should prepare from that source, allocate W, use `execute-handoff` for
both passes, and validate/register its product and final report. Replay the final
Handoff to verify empty Lead references and FULL_WORK scope. Inspect the tool trace
for fabricated Lead records, avoidable scene searches, direct `research-novel`
execution and user handback. Mark the result according to actual behavior.
This fixture checks the known-work route, not open-ended work-selection quality.

## Regression and integration

Run the focused `test_research_status.py` tests, affected planning/library/
execution tests, Skill synchronization and required full-suite checks. When
runtime loading changes, build a wheel and use the out-of-checkout
`phase_minus1_wheel_smoke.py --require-wheel` in a real installation environment
(a pip target directory alone may not reproduce wheel data-file placement).

For shared acquisition, wait for its separately developed interface to land and
test a busy first source with an independent available second source. A missing
integration dependency is NOT RUN, not PASS. Preserve the original failed-source
records and remaining native budget.
