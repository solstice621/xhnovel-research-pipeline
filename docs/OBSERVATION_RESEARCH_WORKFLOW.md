# Observation research operating workflow

This is the Stage A host workflow for existing generic Profiles. The host authors
semantic decisions and performs searches; the CLI seals drafts, validates their
references and runs the existing generic compiler. Neither the CLI nor the Skill
qualifies semantic quality or implements Analyzer. See the
[architecture](OBSERVATION_RESEARCH_ARCHITECTURE.md) and
[build gates](OBSERVATION_RESEARCH_BUILD_PLAN.md).

## 1. Roots and immutable references

Use one research root `R` for planning, Handoffs, attempts and campaign artifacts.
Use an explicit native work directory `W` for each admitted source/Profile
execution. Keep both trees and their `objects/` directories. A returned
`artifact_id` is the content-addressed reference; `path` is its readable copy.
Never construct IDs or hashes manually.

The new commands return JSON. Execution returns exit 0 for `SUCCEEDED`, 3 for
`WAITING_FOR_AGENT`, 2 for `PARTIAL_RETRYABLE`, and 1 for `FAILED` or rejected input.
A rejected preflight command may print `FAIL: ...` to stderr without a receipt;
record that failed source/invocation disposition in the campaign. Existing Scene
command outputs are unchanged.

## 2. Intake and independent definition

Run the existing `seal-intake` command with a truthful intake draft. Its format is
documented in [the planning Skill](../.agents/skills/xhnovel-plan/SKILL.md). Put the
intake and subsequent observation records under the same R:

```text
xhnovel-pipeline seal-intake intake-draft.json --work-dir R
```

`R/planning-manifest.json` contains `intake_artifact_id` and
`neutral_input_artifact_id`. The neutral author receives only the JSON in
`R/neutral-planning-input.json`, without the original seed-bearing conversation.
Author a definition draft with these fields:

| Field | Meaning |
| --- | --- |
| `intake_artifact_id`, `neutral_input_artifact_id` | Actual references from the manifest |
| `research_question` | Seed-free local observation question |
| `inclusion_rules`, `exclusion_rules`, `required_distinctions` | Arrays of strings; never search-derived expected answers |
| `requirements` | Items described below |
| `locality` | `UNIT_LOCAL`, `MIXED_REQUIRES_DECOMPOSITION` or `REQUIRES_CROSS_UNIT_ANALYSIS` |
| `locality_rationale` | Why the current unit can or cannot support the observations |
| `decomposition_status` | `NOT_REQUIRED`, `REQUIRED` or `DECOMPOSED` |
| `authoring` | Independent input/isolation claim described below |
| `frozen_at` | ISO timestamp supplied by the host |
| `previous_definition_artifact_id` | Only for a revised definition |

Each requirement includes `statement`, `applies_to` (string array),
`necessity` (`REQUIRED`/`OPTIONAL`), `locality` (`UNIT_LOCAL`/`CROSS_UNIT`),
`origin` (`HOST_INTERPRETATION`/`NEUTRAL_GOAL`/`EXPLICIT_SCOPE`),
`origin_pointer` and `origin_quote`. Host interpretation uses null pointer/quote;
source-derived requirements bind the actual neutral input text via pointer/quote.
The builder assigns stable requirement IDs. Retain original cross-unit
requirements during decomposition; they remain unresolved in the report.

Both `authoring` and the later `budget_authoring` contain `host`,
`input_artifact_id` (the neutral input), `assurance` and `isolation_claim`.
Legal pairs are `HOST_ISOLATED_ATTESTED` + `FRESH_SUBAGENT_NO_SEED_PAYLOAD`, or
`NOT_PROVEN` + `HOST_ISOLATION_UNAVAILABLE`/`CONTEXT_NOT_ISOLATED`/
`OPERATOR_DID_NOT_ATTEST`. These are host claims, not semantic proofs.

```text
xhnovel-pipeline seal-observation-definition definition-draft.json --work-dir R
```

## 3. Reuse an admitted Profile

Read the actual built-in Profile before assessing coverage. A reusable resolution
draft contains:

```json
{
  "definition_artifact_id": "<returned definition artifact>",
  "decision": "REUSE_EXISTING",
  "selected_profile_ref": "race-mention-v1",
  "fit": "EXACT",
  "admission": {
    "status": "HOST_REVIEWED_EXECUTABLE",
    "reviewer": "<actual reviewing host>",
    "review_reference": "<review evidence or recorded rationale>"
  },
  "coverage": [{
    "requirement_id": "<returned requirement ID>",
    "disposition": "COVERED",
    "payload_kinds": ["RACE_MENTION"],
    "payload_paths": ["/name"],
    "prompt_rules": [],
    "rationale": "<why this requirement is supported>"
  }],
  "rationale": "<actual fit assessment>",
  "assessor": "<host>",
  "frozen_at": "<timestamp>"
}
```

Map every requirement. `prompt_rules` entries must quote actual prompt text.
`SUPERSET` may collect additional native kinds/fields; this is disclosed, without
an extra semantic filter. For `CREATE_REQUIRED` or
`UNSUPPORTED_BY_LOCAL_EXTRACTION`, omit `selected_profile_ref`, `fit` and
`admission`; retain each requirement's honest disposition and rationale. These
branches produce a deliverable report but cannot prepare an executable Handoff.

```text
xhnovel-pipeline seal-profile-resolution resolution-draft.json --work-dir R
```

## 4. Campaign, search and source attempts

Initialize a campaign draft with `definition_artifact_id`,
`resolution_artifact_id`, `search_strategy` (`queries` array and
`selection_rationale`), `budget_authoring`, `frozen_at`, and `budget` containing:

```json
{
  "target_works": 1,
  "max_search_rounds": 3,
  "max_source_attempts": 4,
  "max_full_work_attempts": 2,
  "max_resume_invocations": 4
}
```

Budgets are host-authored from neutral intent independently of search strategy.
The illustrative numbers are not evidence of appropriate coverage for a research
question. Changing scope/budget requires a new frozen campaign.

```text
xhnovel-pipeline observation-research init campaign-draft.json --work-dir R
xhnovel-pipeline observation-research attach search-output.json --research-root R
xhnovel-pipeline observation-research record RUN event-draft.json --research-root R
```

`RUN` is the returned run record path. `attach` stores raw bytes through CAS as
audit data, with no evidence promotion. Event drafts contain `operation_id`,
`event_type`, `detail` and `recorded_at`. Reusing the same operation ID with the
same event is idempotent; it must not hide a different action.

| Event | `detail` |
| --- | --- |
| `SEARCH_STARTED` | `query` |
| `SEARCH_FINISHED` | `start_event_artifact_id`, `outcome` (`COMPLETED`/`FAILED`), `result_artifact_ids`, `error` (null or string) |
| `LEAD_RECORDED` | `lead_artifact_id`, `search_event_artifact_id` (null or actual finished search reference) |
| `SOURCE_STARTED` | `lead_artifact_ids`, `source_input_artifact_id` |
| `SOURCE_FINISHED` | `start_event_artifact_id`, `status` (`ELIGIBLE`/`UNRESOLVED`/`BLOCKED_BY_RIGHTS`/`INELIGIBLE_QUALITY`/`FAILED`), `handoff_artifact_id` (null unless eligible), `reason` |
| `STOP` | `reason`, `rationale` (use the current event schema's allowed reasons) |

Reserve STARTED before invoking host tools. Persist failed and interrupted
operations. The journal limits recorded operations; it cannot observe unlogged
host tool use. The native execution events are written only by the wrapper.

A WorkLead draft has `definition_artifact_id`, `work_claim` (`title`, `author`,
`language`, `aliases`), `relevance_hypothesis`, `lead_sources`, `location_hints`
and `frozen_at`. `lead_sources` reuse existing source kinds and locators, e.g.
`{"source_kind":"OTHER","locator":"https://...","supports":["WORK_IDENTITY"]}`.
Hint references must use real lead-source identities; empty hints are fine.

```text
xhnovel-pipeline seal-observation-work-lead lead-draft.json --work-dir R
```

Prepare input contains `definition_artifact_id`, `resolution_artifact_id`,
`work_lead_artifact_ids`, `requested_at` and either
`source_declaration_artifact_id` or inline `source_declaration`. The latter reuses
the established declaration format: `work`, `source`, `rights`, `source_quality`,
`edition_label`, `declared_at`. An applicable standing
`R/operator-attestation.json` supplies rights under the existing rules. Seed a
new R from the repository canonical `attestations/operator-attestation.json` if
absent, copying bytes and identity unchanged and preserving any existing file.
Omit draft rights to use it; explicit rights must match the standing attestation.
Never author or re-sign authorization per run.

Rights, access, identity and source quality are checked separately. Required
storage and model permissions must be explicit. `COMPLETE` text with
`edition_status=UNKNOWN` can be eligible; positively declared `UNOFFICIAL_COPY`
cannot. Preparation materializes a source-only complete Novel Spec. No search
query, Profile, lead hint or observation definition is passed into that spec.

```text
xhnovel-pipeline prepare-generic-handoff prepare-input.json --work-dir R
xhnovel-pipeline validate-generic-handoff HANDOFF --research-root R
```

## 5. Native two-pass execution and report

Use the campaign wrapper so budgets and source dispositions remain linked:

```text
xhnovel-pipeline observation-research execute RUN HANDOFF --research-root R --work-dir W --executor agent-files
xhnovel-pipeline observation-research execute RUN HANDOFF --research-root R --work-dir W --executor agent-files
```

The first command creates native tasks and returns pending paths. Each fresh
semantic worker reads only one task's `instructions`, `input`, `output.schema`
and writes raw JSON to the declared answer path. Use the exact generic answer
ABI carried by that task. No second prompt, custom window, Scene candidate
adapter, cross-unit context or hidden offset repair is allowed.

For WAITING or PARTIAL_RETRYABLE, repeat the identical command without recovery
flags. `--resume` is reserved for an interrupted invocation with the same binding;
`--retry` explicitly starts another failed-attempt ordinal. Successful cached
invocations also use no recovery flags. The API
executor uses `--executor api --model MODEL`. An agent model label is a host
assertion, not authenticated backend identity. Changing build/executor/source
may invalidate continuation. Concurrent native mutations to the same W are
locked, including direct generic commands.

For independent Handoff use there is also `execute-generic-handoff HANDOFF
--research-root R --work-dir W ...`; it does not book campaign budgets.

```text
xhnovel-pipeline validate-generic-execution RECEIPT --research-root R --work-dir W
xhnovel-pipeline observation-research report RUN --research-root R --output report.json
xhnovel-pipeline observation-research validate RUN --research-root R --report report.json
```

Successful receipts bind exact ingestion, extraction, reduction and corpus IDs.
Historical validation uses frozen sources and does not require the original TXT
or HTTP endpoint, but still enforces runtime/Profile/artifact closure. It does
not rerun a stochastic model. Reports rebuild from the journal and those precise
receipts; changing a saved count cannot change the result. Successful zero
records, pending/partial/failure and missing sources stay distinct. Results remain
`UNQUALIFIED`, coverage `UNMEASURED`; raw excerpts remain subject to source export
permission. A report never resolves entities or generalizes mechanisms.

The [installed smoke script](../scripts/observation_research_wheel_smoke.py) and
[authored fixtures](../fixtures/positive/observation-research) provide executable
examples. They are regression inputs, not real research or quality gold.
