# Geography spike operational notes

- Framework commit: `44966c9514576c72138c62c183ef0c706e2bd770`
- Profile: `geography-v1` / `xhnovel.geography` 1.0.0
- Executor: `agent-files`
- Source: locally re-materialized from the previously authorized run-004 archive; no web retrieval
- Total immutable extraction units: 676
- Initial pass: expected `WAITING_FOR_AGENT` (exit 3)
- Pilot ordinals 1-9: all nine answers accepted by the native validator and checkpoint; 667 units remained
- Pilot raw records: 322 `PLACE_MENTION`, 19 `SPATIAL_RELATION` (341 total)
- Capacity finding: ordinal 5 had about 83 direct candidates (about 73 mentions plus 10 relations), exceeding `max_records_per_unit=64`. The answer retained 63 records by prioritizing direct relations and explicitly typed mentions and by omitting redundant endpoint mentions. This is a Profile recall/capacity defect, not semantic full coverage.
- Early semantic-review lead: ordinal 9 emits `乌坦城 PART_OF 加玛帝国` from `乌坦城隶属于加玛帝国`; manual qualification must decide whether this is forbidden political affiliation rather than explicit spatial part-whole.
- Isolation correction: the first operational pilot assigned multiple disjoint task files to each subagent. Although the native validator accepted the first nine answers, this is weaker than the Skill's literal one-worker/one-task isolation rule because a worker context could retain another unit. These answers are engineering-pilot evidence only and are not eligible for the qualified whole-work semantic run.
- The restart then exposed an immutable-task ABI gap: task files did not define `evidence_bindings[*].paths` as RFC 6901 JSON Pointers and did not carry the trusted Profile evidence policy. Framework commit `6cc794fd6ad2632f1d26d00a9bf027634617c751` makes those rules self-contained and adds regressions.
- Qualification restart target: `work-qualified-6cc794f`, with one fresh subagent context per immutable unit and no reuse of worker context across units.
- Local gate for `6cc794f`: full pytest, Skill sync, compileall, diff check, wheel build, and all four out-of-checkout wheel smokes passed.
- Remote CI for `6cc794f` did not start any steps on either OS: GitHub annotated both jobs with an account payment/spending-limit failure. This is an infrastructure/billing block, not a test result; cross-platform status remains unproven for this SHA.
- Required truth labels remain `text_coverage=FULL`, `semantic_coverage=UNMEASURED`, `semantic_assurance=UNQUALIFIED`.

This file is runtime-only and must not be committed.
