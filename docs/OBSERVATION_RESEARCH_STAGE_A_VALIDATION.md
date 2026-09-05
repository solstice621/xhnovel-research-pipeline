# Observation research Stage A validation record

This records the original `ff523ef` delivery evidence. The subsequent PR #17
review fixes and their new validation are recorded in
[OBSERVATION_RESEARCH_REVIEW_FIX_VALIDATION.md](OBSERVATION_RESEARCH_REVIEW_FIX_VALIDATION.md).
Original results below do not qualify the changed v2 runtime.

This is an implementation evidence record for the acceptance matrix in
[OBSERVATION_RESEARCH_BUILD_PLAN.md](OBSERVATION_RESEARCH_BUILD_PLAN.md), not a
substitute for its gates. A-24 was explicitly retired by the user on 2026-09-05;
that acceptance change is reflected below. Local fixture tests support the
contracts and execution behavior listed below. They do not establish semantic precision/recall or a
completed live research run.

## Baseline and release identity

| Item | Recorded state |
| --- | --- |
| Evidence assembled | 2026-09-05 |
| Implementation baseline | `3372edd47666175db9f6a17bee1b8446635ce355` |
| Source checkout observed during evidence assembly | `/Users/I578130/Desktop/xhnovel-research-pipeline`, branch `main`, uncommitted implementation |
| Isolated implementation branch / checkout | `feat/observation-research-stage-a-delivery` / `/private/tmp/xhnovel-observation-stage-a-20260905` |
| Fixed original implementation SHA | `ff523ef23420dc46fd8eec6c4cc19e716ed9a429`; precommit source and wheel evidence retain their original build identity. |
| Full-suite local result | 743 passed in 171.16 s in the isolated checkout; later affected Generic tests: 61 passed in 42.22 s. Final test-only campaign additions also passed: 15 tests in 18.79 s. No production source changed after the aggregate run |
| Wheel filename / SHA-256 | `xhnovel_pipeline-0.2.0.dev0-py3-none-any.whl` / `902afe4f8d15fd764edb18ef89c81969af9a7d5cb6b723e58d2cc9d26490ab11` |
| Installed-wheel smoke result and artifact directory | All five PASS; `/private/tmp/xhnovel-stage-a-installed-smokes`; package/data roots outside both checkouts, installed `repository_commit=unknown-dev` |
| Ubuntu CI job / SHA / result | `NOT_RUN`: workflow removed; A-24 retired by the user, not passed |
| Windows CI job / SHA / result | `NOT_RUN`: workflow removed; A-24 retired by the user, not passed |
| Skill sync and diff check | Both passed in the isolated implementation; no canonical/mirror drift |
| Live host research canary / output directory | Actual host search and complete 《促織》 / 《夜叉國》 source admission passed. Post-commit native execution receipts and report are delivered separately under `.runtime/observation-stage-a-canary/`. These are operator-authored acceptance stimuli, not human-verbatim research requests or semantic gold. |

On 2026-09-05 the user explicitly retired A-24 and requested merge without this
GitHub validation workflow. A-24 is RETIRED_BY_USER and is no longer a Stage A
merge or completion gate. The CI workflow is deleted; no unexecuted platform is
marked PASS. Local verification requirements and semantic-quality limits remain.

Source changes, the final Git commit and included runtime bytes can change native
build IDs and Handoff builder lineage. That is expected. Test runs made before
the final commit must not be described as fixed-SHA release evidence; old artifacts
may require their original runtime to replay.

## Local check ledger

These entries combine direct test outputs and the responsible implementation
agents' reported command results. Overlapping runs are not additive test counts.
The final aggregate run is recorded separately above.

| Ref | Command / scope | Observed result |
| --- | --- | --- |
| L1 | `.venv/bin/python -m pytest tests/test_generic_execution_boundaries.py tests/test_generic_extraction.py tests/test_generic_profile.py tests/test_generic_units_and_reducer.py` | 30 passed in 6.48 s before subsequent execution integration |
| L2 | `.venv/bin/python -m pytest tests/test_generic_handoff_execution.py tests/test_generic_execution_boundaries.py tests/test_generic_extraction.py` | 41 passed in 21.90 s at that working-tree revision |
| L3 | `.venv/bin/python -m pytest tests/test_generic_handoff_execution.py -k 'failed_new_attempt or interruption_on_continuation'` | 3 passed; ordinary failures versus host interruption |
| L4 | `.venv/bin/python -m pytest tests/test_generic_handoff_execution.py -k 'new_research_definition or two_pass or public_event'` | 5 passed in 7.34 s; A-01 and exact event/receipt validation |
| L5 | `.venv/bin/python -m pytest tests/test_observation_planning.py tests/test_phase_minus1_planning.py tests/test_phase0_contracts.py -q` | Responsible agent reported 142 passing tests |
| L6 | `.venv/bin/python -m pytest tests/test_generic_handoff.py -q` | 28 passed before additional matrix parametrizations |
| L7 | `.venv/bin/python -m pytest tests/test_observation_campaign.py -q` | Responsible agent reported 13 passing tests, including interruption and reservation binding regressions |
| L8 | `.venv/bin/python -m pytest tests/test_novel_spec_preflight.py tests/test_phase0_identity.py tests/test_phase0_contracts.py -q` | Passed; quiet output, no numeric count claimed |
| L9 | `.venv/bin/python -m pytest tests/test_novel_cli.py tests/test_agent_files_cli.py tests/test_docs_skill_contract.py tests/test_exploration_skill_contract.py -q` | Passed; quiet output, no numeric count claimed |
| L10 | `tests/test_observation_cli.py::test_campaign_invalid_api_configuration_exits_nonzero_and_preserves_prestart_failure` and `::test_observation_cli_full_slice_keeps_nonempty_zero_and_offline_results` | Responsible agents reported both passed; full source CLI fixture produced geography 1, race 1 and race zero 0, with fresh-process offline validation |
| L11 | Targeted A-03/A-05/A-06 tests in `test_generic_handoff.py` and `test_generic_handoff_execution.py`, selected with `-k 'runtime_change or new_handoff_cannot or profile_package_drift'` | 12 passed initially; the source-byte case failed only because the test expected the wrong existing error code. After correction, that case passed alone in 1.56 s. No production change |
| L12 | `.venv/bin/python -m pytest tests/test_generic_handoff_execution.py -k 'waiting_keeps_failed_units'` | 1 passed in 2.12 s after adding a completed + rejected + missing-answer combination |
| L13 | `.venv/bin/python -m pytest tests/test_observation_campaign.py -k 'failed_source_then_distinct or superset_profile or recovery_cannot_absorb'` | Responsible agent reported 3 passed: exact A-16/A-19 cases plus the updated reservation-recovery assertion |
| L14 | `/private/tmp/xhnovel-observation-test-env/bin/python -m pytest -ra` in the isolated checkout | Root reported 743 passed in 171.16 s; log `/private/tmp/xhnovel-stage-a-full-tests.log`. Later test-only additions were not yet in this initial snapshot |
| L15 | `/private/tmp/xhnovel-observation-test-env/bin/python -m pytest tests/test_generic_handoff.py tests/test_generic_handoff_execution.py -ra` after copying the final test-only additions into the isolated checkout | Root reported 61 passed in 42.22 s; production source unchanged from L14 |
| L16 | Skill synchronization check and `git diff --check` in the isolated checkout | Root reported both passed |

## A-01 through A-24

“Covered by local tests” below means the specific assertions have executed on the
development host. It does not close the fixed-SHA full-suite, installed-wheel,
or live-canary requirements; the former dual-platform gate is retired. Some rows
use complementary unit and integration tests rather than one test that exercises
every layer.

| Case | Production and named test evidence | Status |
| --- | --- | --- |
| **A-01 — different observation goal, same admitted native inputs** | [Execution wrapper](../src/xhnovel_pipeline/generic_handoff_execution.py) keeps research identity outside native input/build. [Execution tests](../tests/test_generic_handoff_execution.py): `test_new_research_definition_reuses_same_profile_native_extraction` asserts distinct Handoffs/receipts, identical native target and zero additional model calls. | Covered by local test, L4 |
| **A-02 — seeds, Leads and hints do not alter generic tasks** | [Planning](../src/xhnovel_pipeline/observation_planning.py) binds the neutral projection; [Handoff projection](../src/xhnovel_pipeline/generic_handoff.py) emits source-only spec. [Phase -1 tests](../tests/test_phase_minus1_planning.py): `test_neutral_projection_drops_every_seed_dependent_or_verbatim_field`; [Handoff tests](../tests/test_generic_handoff.py): `test_search_taint_never_enters_spec_or_handoff_text`, `test_generic_preflight_rejects_non_ingestion_fields`; [planning tests](../tests/test_observation_planning.py): `test_truthful_input_attestation_and_exact_user_origin`. A-01 additionally demonstrates unchanged native run/target across definitions. | Covered by complementary local tests, L4–L6. Host context isolation remains an attestation |
| **A-03 — all bound Profile package bytes** | [Profile binding](../src/xhnovel_pipeline/observation_planning.py) and Handoff replay reject package drift. [Handoff tests](../tests/test_generic_handoff.py): `test_profile_package_drift_rejects_old_handoff`, parameterized over `prompt.md`, `payload.schema.json`, `profile.json`, including whitespace-only byte changes. | Covered by local tests, L11; package identity is distinct from extraction identity |
| **A-04 — reducer-only change** | [Native extraction/reduction](../src/xhnovel_pipeline/generic_extraction.py) retains its split identities. [Profile tests](../tests/test_generic_profile.py): `test_profile_hashes_split_extraction_from_reduction`; [native tests](../tests/test_generic_extraction.py): `test_reducer_only_profile_change_does_not_call_model`; [boundary tests](../tests/test_generic_execution_boundaries.py): `test_exact_corpus_offline_with_multiple_reductions_and_new_pending`. | Covered by local tests, L1–L2; fixed runtime required |
| **A-05 — commit, runtime, executor or extraction identity changes** | [Native build validation](../src/xhnovel_pipeline/generic_extraction.py) and [attempt binding](../src/xhnovel_pipeline/generic_handoff_execution.py) remain exact. [Execution tests](../tests/test_generic_handoff_execution.py): `test_runtime_change_rejects_old_attempt_and_completed_replay` covers repository commit / engine source hash × pending / completed; `test_resume_rejects_actual_executor_or_directory_drift` covers model label, timeout and directory. Profile extraction-input change also covered by A-03/A-04. | Covered by local tests, L2/L11 |
| **A-06 — whole spec and current source bytes** | [Native ingestion](../src/xhnovel_pipeline/novel_ingest.py) retains checkpoint input and source-change validation. [Handoff tests](../tests/test_generic_handoff.py): `test_new_handoff_cannot_silently_reuse_mismatched_native_source_state` covers source path, rights, quality, limits, strict order and source bytes; changed input is refused in the old directory before model calls and succeeds in an explicitly fresh directory. | Covered by local tests, L11; legacy error codes retained |
| **A-07 — first agent-files invocation** | [Execution wrapper](../src/xhnovel_pipeline/generic_handoff_execution.py) records STARTED then WAITING. [Execution tests](../tests/test_generic_handoff_execution.py): `test_two_pass_selected_receipt_cached_success_and_offline_replay`; [native tests](../tests/test_generic_extraction.py): `test_agent_files_materialize_all_tasks_resume_and_detect_tampering`. | Covered by local tests, L2/L4; no terminal receipt before completion |
| **A-08 — pending + failed + completed units** | [Native checkpoint loop](../src/xhnovel_pipeline/generic_extraction.py) owns unit retries; the wrapper freezes its current checkpoint. [Execution tests](../tests/test_generic_handoff_execution.py): `test_waiting_keeps_failed_units_and_only_current_checkpoint` now keeps all three states, removes a completed unit's answer and still completes after supplying the other answers. | Covered by strengthened local test, L12 |
| **A-09 — retryable partial and budget** | [Execution wrapper](../src/xhnovel_pipeline/generic_handoff_execution.py) preserves rejected attempts; [campaign reducer](../src/xhnovel_pipeline/observation_campaign.py) budgets continuation. [Execution tests](../tests/test_generic_handoff_execution.py): `test_rejected_answers_remain_audited_after_partial_correction`; [campaign tests](../tests/test_observation_campaign.py): `test_execution_requires_reservation_and_resume_budget`; [boundary tests](../tests/test_generic_execution_boundaries.py): `test_partial_releases_native_work_dir_lock`. | Covered by complementary local tests, L2/L7 |
| **A-10 — interruption during a continuation** | [Native invocation journal](../src/xhnovel_pipeline/generic_handoff_execution.py) and [campaign reservation recovery](../src/xhnovel_pipeline/observation_campaign.py) use the new STARTED marker. [Execution tests](../tests/test_generic_handoff_execution.py): `test_interruption_on_continuation_requires_explicit_recovery`; [campaign tests](../tests/test_observation_campaign.py): `test_interrupted_native_call_requires_explicit_resume_and_consumes_resume_budget`, `test_recovery_cannot_absorb_unrecorded_native_continuation`, `test_reservation_cannot_adopt_different_native_executor`. | Covered by local tests, L2/L3/L7 |
| **A-11 — tampered task/checkpoint/CAS or changed configuration** | [Native validation](../src/xhnovel_pipeline/generic_extraction.py) is reused by the wrapper. [Execution tests](../tests/test_generic_handoff_execution.py): `test_audit_or_checkpoint_tampering_fails_closed`, `test_resume_rejects_actual_executor_or_directory_drift`; [native tests](../tests/test_generic_extraction.py): `test_agent_files_materialize_all_tasks_resume_and_detect_tampering`; [Handoff tests](../tests/test_generic_handoff.py): `test_missing_cas_dependency_fails_closed`, `test_profile_package_drift_rejects_old_handoff`. | Covered by complementary local tests, L2/L6/L11 |
| **A-12 — shared native work directory** | [Native lock](../src/xhnovel_pipeline/generic_extraction.py): `generic_work_dir_lock`; all three public mutation entry points participate. [Boundary tests](../tests/test_generic_execution_boundaries.py): `test_public_mutations_contend_across_processes`, `test_lock_token_requires_live_same_thread_same_directory_owner`, `test_pending_releases_lock_and_outer_owner_can_publish_after_validation`; [execution tests](../tests/test_generic_handoff_execution.py): `test_handoff_and_direct_caller_share_native_work_dir_lock`. | Covered locally across processes/public APIs, L1–L2; Windows NOT_RUN; A-24 retired |
| **A-13 — old success, new pending, multiple reductions** | [Selected validator](../src/xhnovel_pipeline/generic_extraction.py): `validate_selected_generic_corpus`; exact receipt validation checks the selected corpus's package too. [Boundary tests](../tests/test_generic_execution_boundaries.py): `test_exact_corpus_offline_with_multiple_reductions_and_new_pending`, `test_exact_selector_rejects_cross_binding_and_path_escape`; [execution tests](../tests/test_generic_handoff_execution.py): `test_current_pending_and_source_failure_never_use_older_native_success`. | Covered by local tests, L2 |
| **A-14 — fresh-process offline replay** | [Receipt validation](../src/xhnovel_pipeline/generic_handoff_execution.py): `validate_generic_execution` uses frozen closure. [Execution tests](../tests/test_generic_handoff_execution.py): `test_two_pass_selected_receipt_cached_success_and_offline_replay` removes the source and validates in a subprocess; `test_runtime_change_rejects_old_attempt_and_completed_replay` rejects a different runtime. [CLI test](../tests/test_observation_cli.py): `test_observation_cli_full_slice_keeps_nonempty_zero_and_offline_results`. | Covered by local source-runtime tests, L4/L10/L11; installed-runtime proof also passed in A-23 |
| **A-15 — zero observations** | [Execution wrapper](../src/xhnovel_pipeline/generic_handoff_execution.py) only publishes success after selected validation. [Execution tests](../tests/test_generic_handoff_execution.py): `test_two_pass_selected_receipt_cached_success_and_offline_replay` zero variant; [campaign tests](../tests/test_observation_campaign.py): `test_full_campaign_two_pass_stop_report_and_offline_validation` zero variant; [CLI fixture](../tests/test_observation_cli.py) separately asserts counts `[1, 1, 0]`. | Covered by local tests, L2/L7/L10; zero is a completed count, not a missing result |
| **A-16 — failed source then another source succeeds** | [Campaign journal/report](../src/xhnovel_pipeline/observation_campaign.py) retains source attempts. [Campaign tests](../tests/test_observation_campaign.py): `test_failed_source_then_distinct_source_success_preserves_both_attempts` performs an actual failed prepare with unknown rights, admits a different source and completes extraction; asserts two attempts, one failure, one successful work and receipt. `test_failed_sources_remain_in_denominator` additionally checks retained blocked attempts. | Covered by exact supplemental local test, L13; final isolated campaign suite passed (15 tests in 18.79 s) |
| **A-17 — no source, rights/quality rejection, no Profile, budgets** | [Handoff gates](../src/xhnovel_pipeline/generic_handoff.py) and [campaign journal](../src/xhnovel_pipeline/observation_campaign.py) keep separate dispositions. [Handoff tests](../tests/test_generic_handoff.py): `test_prepare_keeps_rights_quality_and_access_gates`, `test_valid_no_profile_resolution_remains_non_executable`; [campaign tests](../tests/test_observation_campaign.py): `test_no_profile_report_preserves_unresolved_requirements`, `test_search_budget_idempotency_and_result_artifact_binding`, `test_execution_requires_reservation_and_resume_budget`, `test_invalid_budget_authoring_and_premature_stop_rejected`. | Covered by complementary local tests, L6–L8; actual source-search failure distribution remains live-canary evidence |
| **A-18 — partial fit / cross-window target** | [Definition and ProfileResolution validation](../src/xhnovel_pipeline/observation_planning.py). [Planning tests](../tests/test_observation_planning.py): `test_resolution_rejects_bad_coverage_and_bindings`, `test_mixed_requires_new_decomposed_version_preserving_unresolved_scope`, `test_cross_unit_cannot_be_claimed_covered`; [Handoff tests](../tests/test_generic_handoff.py): `test_valid_no_profile_resolution_remains_non_executable`. | Covered by local tests, L5–L6; no Analyzer introduced |
| **A-19 — SUPERSET Profile and excerpt restrictions** | [Report construction](../src/xhnovel_pipeline/observation_campaign.py) declares `COMPLETE_NATIVE_CORPORA_NO_SEMANTIC_FILTER`, preserves Profile fit and emits offsets instead of excerpts. [Campaign test](../tests/test_observation_campaign.py): `test_superset_profile_keeps_full_corpus_and_exports_offsets_without_excerpts` selects a SUPERSET Profile for a places-only requirement, retains both a place and spatial relation in the corpus, and checks offset-only reporting with excerpt export forbidden. | Covered by exact supplemental local test, L13; final isolated campaign suite passed (15 tests in 18.79 s) |
| **A-20 — reconstruct report and reject forged counts** | [Campaign validation](../src/xhnovel_pipeline/observation_campaign.py) reconstructs from journal and validated receipts. [Campaign tests](../tests/test_observation_campaign.py): `test_full_campaign_two_pass_stop_report_and_offline_validation`, `test_report_forged_count_and_missing_event_rejected`, `test_cached_native_receipt_reused_by_new_campaign_without_budget`; [execution tests](../tests/test_generic_handoff_execution.py): `test_public_event_validation_replays_returns_and_rejects_start_or_wrong_handoff`. | Covered by local tests, L4/L7; corpus corruption remains a validation failure |
| **A-21 — geography and race share one route** | Generic [Handoff](../src/xhnovel_pipeline/generic_handoff.py), [executor wrapper](../src/xhnovel_pipeline/generic_handoff_execution.py), [CLI](../src/xhnovel_pipeline/observation_cli.py). [Handoff tests](../tests/test_generic_handoff.py): `test_geography_and_race_handoffs_use_identical_source_spec`; [execution tests](../tests/test_generic_handoff_execution.py): `test_two_pass_selected_receipt_cached_success_and_offline_replay`; [CLI fixture](../tests/test_observation_cli.py) checks both Profiles and unchanged assurance. | Covered by local tests, L4/L6/L10; not a semantic benchmark |
| **A-22 — original Scene workflow / complete regressions** | Shared spec/source primitives remain in [novel_spec.py](../src/xhnovel_pipeline/novel_spec.py) and [phase0_handoff.py](../src/xhnovel_pipeline/phase0_handoff.py); Scene CLI remains separate. L14 ran the complete suite; L15 covers later Generic test-only additions against unchanged production source. | Covered by local full-suite and affected tests, L14/L15; final isolated campaign additions: 15 passed in 18.79 s; original delivery SHA recorded above, without claiming a full-suite rerun at that SHA |
| **A-23 — installed wheel and all smokes** | [New smoke](../scripts/observation_research_wheel_smoke.py) and [fixture](../fixtures/positive/observation-research/) cover two-pass geography/race, nonempty + zero, report and offline subprocess validation. L10 proves source CLI execution only. Existing four wheel smokes remain required by the build plan. | Original delivery PASS: the wheel/hash and all five installed smokes are recorded above; 137 installed code/data files were compared to immutable Git blobs at `ff523ef`. The wheel was built before that commit; this is byte parity, not a rerun at the source Git identity |
| **A-24 — retired Ubuntu/Windows gate** | User explicitly removed this requirement on 2026-09-05; the GitHub CI workflow is deleted. | `RETIRED_BY_USER`: not required for Stage A merge/completion; both platforms remain NOT_RUN, with no cross-platform verification claim |

## Residual gates and limits

- A-16 and A-19 supplemental scenarios passed locally; the final campaign suite
  recorded 15 passed in 18.79 s. Counts overlap the earlier aggregate suite.
- The original full suite and later affected tests predate the final delivery
  commit; no fixed-SHA full-suite rerun is claimed here. Installed-wheel byte
  parity to that commit is separately recorded, not a replacement for test runs.
- All five original installed smokes passed. Original postcommit canaries for
  complete 《促織》 and 《夜叉國》 reached SUCCEEDED with 2 geography and 8 race
  records and offline validation. They were operator-authored acceptance stimuli,
  not semantic gold or proof of full-length commercial-novel recall. Their local
  evidence is under `.runtime/observation-stage-a-canary/` and described in PR #17.
- No semantic quality result is claimed. `UNQUALIFIED` / `UNMEASURED` remain the
  production declarations; Stage B evaluation and Stage C new-Profile admission
  are outside this implementation record.
- Historical checkpoint/task/receipt corruption must stay a visible validation
  failure. The matrix records deterministic validation behavior, not an assertion
  that an untrusted actor cannot delete an entire research archive.

## Original delivery evidence

These values preserve the original checklist at delivery time, before A-24 was
retired. Historical PENDING / stage_a_complete values below are not current merge
gates. Later runtime changes require new evidence:

```text
implementation_branch = feat/observation-research-stage-a-delivery
implementation_checkout = /private/tmp/xhnovel-observation-stage-a-20260905
implementation_sha = ff523ef23420dc46fd8eec6c4cc19e716ed9a429
full_pytest_command = /private/tmp/xhnovel-observation-test-env/bin/python -m pytest -ra
full_pytest_result = 743 passed in 171.16s, isolated source snapshot before final test-only additions
full_pytest_log = /private/tmp/xhnovel-stage-a-full-tests.log
targeted_post_snapshot_tests = Generic: 61 passed in 42.22s; isolated campaign: 15 passed in 18.79s
skill_sync_result = PASS in isolated delivery checkout; final SHA ff523ef
diff_check_result = PASS in isolated delivery checkout; final SHA ff523ef
wheel_filename = xhnovel_pipeline-0.2.0.dev0-py3-none-any.whl
wheel_sha256 = 902afe4f8d15fd764edb18ef89c81969af9a7d5cb6b723e58d2cc9d26490ab11
installed_smoke_directory = /private/tmp/xhnovel-stage-a-installed-smokes
agent_files_wheel_smoke = PASS
generic_extraction_wheel_smoke = PASS
phase_minus1_wheel_smoke = PASS
phase0_vertical_slice_wheel_smoke = PASS
observation_research_wheel_smoke = PASS
ubuntu_fixed_sha_verification = PENDING
windows_fixed_sha_verification = PENDING
live_canary_directory = .runtime/observation-stage-a-canary/
live_canary_report_and_receipts = geography 2 / race 8, SUCCEEDED and offline validation PASS at ff523ef
stage_a_complete = false
```

The final affected campaign command was `/private/tmp/xhnovel-observation-test-env/bin/python -m pytest tests/test_observation_campaign.py -ra`: **15 passed in 18.79 s**, log `/private/tmp/xhnovel-stage-a-final-campaign-tests.log`. These results overlap the aggregate suite and are not additive totals.

All five installed-wheel smoke commands returned exit 0. Each ran from `/private/tmp/xhnovel-stage-a-installed-smokes`, with PYTHONPATH and OPENAI_API_KEY removed and UTF-8 enabled. `installation-proof.json` binds the exact wheel, installed source/data roots and build hashes. Observation fixtures produced counts `[1, 1, 0]`; original-source-free fresh-process verification passed. This remains distinct from actual host research.
