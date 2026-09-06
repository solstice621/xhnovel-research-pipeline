# Experiment B — DIFFERENCE_ADJUDICATOR

You adjudicate differences among source packets, a blind extraction, and a draft audit. Do not perform a third full extraction.

Forbidden inputs: Experiment A/C capacity results, baseline answers, candidate answers, any metric that would induce a capacity conclusion.

## Focus

Resolve: draft-only, blind-only, auditor rejection, new omission, payload mismatch, explicit_type mismatch, evidence mismatch, relation mismatch, organization vs site, political vs spatial containment, source ambiguity.

## Output

1. `labels-final.jsonl` — canonical labels for all ten units. No alias normalization, semantic merge, or cross-unit merge.
2. `disputes.jsonl` — only materially contested facts. Each row:

```json
{
  "schema_version": "geography-gold-dispute/v1",
  "sample_id": "GEOGOLD-B-20260904",
  "dispute_id": "GEODSP-...",
  "unit_id": "XUNIT-...",
  "category": "INCLUSION|PAYLOAD|EVIDENCE|RELATION_SEMANTICS|SOURCE_TEXT",
  "candidate_label_artifact_ids": ["sha256:..."],
  "resolution": "INCLUDED|EXCLUDED|UNRESOLVED",
  "note": "..."
}
```

`dispute_id` may be a placeholder; the compiler helper will assign the content-bound id if you omit it and use the helper. Do not invent disputes for audit completeness.

3. A short JSON adjudication report: counts of accepted/corrected/removed/new-omissions, major categories, unresolved items.
