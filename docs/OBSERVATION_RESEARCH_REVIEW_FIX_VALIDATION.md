# PR #17 execution recovery fixes

The review baseline is `ff523ef23420dc46fd8eec6c4cc19e716ed9a429` on
`feat/observation-research-stage-a-delivery`. A follow-up commit preserves that
reviewed history. Its exact delivery SHA and local evidence hashes are recorded
in PR #17; this file does not assert its own future commit ID.

The reproduced failures concerned execution ownership, attempt accounting,
crash recovery and reporting. Source-only spec isolation, exact corpus replay,
rights declarations and UNQUALIFIED / UNMEASURED remain unchanged.

## Corrections and regression evidence

| Review finding | Final behavior | Regression scope |
| --- | --- | --- |
| Campaign/direct caller race | Campaign holds the Handoff lock from native predecessor inspection through campaign finish publication. Only a live token for the same process/thread/directory can be reused. | Barrier tests force a direct caller before native entry and after native return; both are rejected without adding another invocation. Token tests cover copy, other thread/directory, expiry and valid reentrancy. |
| Ownership after prestart crash | Native STARTED v2 binds the published campaign reservation artifact. Matching model/work-dir and event adjacency alone cannot claim ownership. A foreign invocation produces an explicit FAILED_PRESTART disposition, without its event/receipt; subsequent foreign continuation remains rejected. | Prestart crash followed by both identical and different executors; lost owned return remains recoverable; an owned native start cannot be hidden as FAILED_PRESTART. |
| Interrupted retry budget | A pure native next-invocation planner controls runtime and campaign replay. Retrying an interrupted invocation starts a new attempt and consumes full-work budget. | Budget 1 refuses attempt 2; budget 2 allows it even with resume budget 0. Existing interrupted resume, waiting/partial continuation and cached-success budget tests remain required. |
| Killed writer temporary file | Native history enumerates published JSON events, not staging files. Normal file, sequence, canonical bytes, CAS and chain checks remain mandatory. | Real child writer pauses between fsync and hard-link publication; concurrent reads succeed, forced kill leaves an orphan, and continuation succeeds without cleanup. Malformed/unknown published JSON still rejects. |
| Last-entry report summaries | Report v2 keeps all source dispositions, latest status per Handoff and separate historical execution statuses. MIXED exposes multiple outcomes. Work summaries use their own Handoff IDs. | Success/interruption, failure/success, success/unexecuted Handoff and blocked/eligible source cases preserve both outcomes and accurate counts. |
| Executor initialization before reservation | Configuration resolution is free of work-dir writes. Agent files materialize after reservation and native locks. | README write error is recorded as FAILED_PRESTART; the test asserts both locks and the reservation are already present. Zero budget leaves W absent. |
| Conflicting recovery flags | Rejected before reservation or work-dir creation. Other invalid recovery transitions also reject before a new reservation. | Combined resume/retry and zero-budget checks retain zero execution counts. |
| A-23 stale documentation | The original record now distinguishes completed original wheel evidence from this changed runtime's checks. | Original wheel/hash/five smokes and 137-file committed-byte parity are historical facts, not reused as validation of this fix. |

Primary regressions are in
[test_observation_execution_recovery.py](../tests/test_observation_execution_recovery.py),
with updated cases in [test_observation_campaign.py](../tests/test_observation_campaign.py).

## Contract and archive scope

GenericHandoffAttemptEvent and ObservationResearchEvent now require v2. Campaign
reservations freeze recovery mode; native starts require a nullable
`campaign_start_artifact_id`, null only for direct execution. Non-null references
must resolve to a published canonical campaign event and match its Handoff,
predecessor, work-dir, recovery and complete executor descriptor. Native history
rejects reuse of one reservation for multiple starts. Terminal receipts retain
their existing format and bind ownership transitively through the start artifact.

The report is v2: `source_statuses` lists distinct source-attempt dispositions;
`execution_statuses` is current per Handoff (including HANDOFF_READY), while
`execution_history_statuses` preserves past invocation states. MIXED is a summary,
not a replacement for the arrays or full attempt lists.

Old v1 archives require their original runtime. There is no automatic migration,
inferred owner or rewrite of existing events/receipts. Build-bound IDs change
with these source/contract bytes. Installed-wheel `repository_commit=unknown-dev`
must not be represented as a source checkout Git SHA.

## Verification ledger

Host: macOS arm64, Python 3.12.8. Tests use authorized fixtures and native
agent-files or controlled API test executors; no external model API is called.

| Check | Result |
| --- | --- |
| Affected execution/campaign/CLI suites | 58 passed in 97.28 s; includes 16 new recovery/report cases |
| Full suite | 761 passed in 209.85 s |
| Skill validation and canonical/mirror sync | PASS (quick_validate and sync_skills --check) |
| Diff and document links | PASS; 99 local documentation/Skill links resolved |
| New wheel and five out-of-checkout installed smokes | All five PASS; installed source/assets outside checkout, no PYTHONPATH or API key |
| Ubuntu / Windows at final fixed SHA | NOT RUN; A-24 RETIRED_BY_USER on 2026-09-05, no longer a merge/completion gate |

The targeted and full-suite counts overlap and must not be added. Source changes
after a passing check require relevant checks again; this ledger must not transfer
the original `ff523ef` test/canary results to the changed runtime.

## Acceptance change and integration

On 2026-09-05 the user requested removal of the Ubuntu/Windows GitHub validation
flow and merge of PR #17. A-24 is RETIRED_BY_USER; it is not a passing platform
check. GitHub reports the removed CI workflow (`345467671`, `.github/workflows/ci.yml`)
as `deleted`, and all 165 historical runs are completed. Local verification
requirements remain in [the build plan](OBSERVATION_RESEARCH_BUILD_PLAN.md).

Before merge, the branch incorporates main `bee3123` (PR #18, explicit
UNOFFICIAL_COPY Tier B policy) in integration commit
`c1f7c557e868889e06da2aee0f0d55308565f6b9`. The observation workflow text and
Handoff tests are aligned with that existing policy: complete declared copies
retain UNOFFICIAL_COPY and Tier B through replay; unknown completeness still
rejects. No additional production-source or contract change was needed.

| Combined-code check on macOS / Python 3.12.8 | Result |
| --- | --- |
| Full suite at integration source | 760 passed, 1 failed in 189.28 s; the failure was the obsolete UNOFFICIAL_COPY rejection expectation |
| Handoff suite after correcting that expectation and adding positive replay coverage | 37 passed in 17.68 s; includes the replacement unknown-completeness rejection and new Tier B admission case |
| Skill sync | PASS; main's canonical/mirror update is retained |
| Fresh wheel and all five installed smokes | PASS; source/assets loaded outside checkout with no PYTHONPATH or model API key |

The full-suite and focused counts overlap. There is no claim of a second full
suite run after the test-only correction. Production source and package assets
are unchanged from the combined-code full suite and wheel; final commit-byte
parity and documentation checks are recorded in PR #17. Git commit changes can
still change build identity, so source and installed identities remain distinct.

The combined-code wheel SHA-256 is
`ba20be591b175ce7deac05ce62bb791137ff804d11490b9afd5540b23131b02b`.
Installed smoke durations: agent-files 2.488 s, generic extraction 3.502 s,
Phase -1 1.671 s, Phase 0 5.245 s, observation research 42.545 s.
This evidence is retained in `.runtime/observation-stage-a-merge/`; the earlier
761-test and wheel evidence above remains tied to the review fix.

## Remaining scope

No cross-platform completion or semantic qualification is claimed. Historical
canaries remain tied to their original runtime; this fix uses regression and
installed fixture evidence. Stage B/C and Analyzer remain outside Stage A.

The review's internal-exception diagnostics, cumulative replay cost, CLI naming,
future allOf support and public/private helper naming remain follow-up work.
FAILED still includes integrity/internal exceptions and exits nonzero. Full
history/receipt validation remains in place; no performance shortcut weakens it.
Model labels remain host assertions, zero-record success remains legitimate,
and replay still requires retained research/native trees and matching runtime.

New wheel: `xhnovel_pipeline-0.2.0.dev0-py3-none-any.whl`, SHA-256
`cc33dad116b3c7249ebbd0a07dd45a4c3e2f94388606bf1eab1b9e1123012d8b`. All 138 packaged source/data files match
the installed files and the tested working-tree bytes. The wheel was built in
an isolated setuptools environment before the follow-up commit; it records
`unknown-dev`, not a Git release identity. Commit-byte parity is recorded
separately in the PR delivery evidence. No production source changed after the
full suite and installed checks.

Installed smoke durations: agent-files 2.605 s; generic extraction 3.642 s;
Phase -1 1.748 s; Phase 0 5.498 s; observation research 46.629 s. The installed
environment used jsonschema 4.26.0, PyYAML 6.0.3 and pypdf 6.17.0.
Local logs and immutable fixture outputs are retained under
`.runtime/observation-stage-a-review-fix/`; they are not committed or uploaded
as GitHub attachments.
