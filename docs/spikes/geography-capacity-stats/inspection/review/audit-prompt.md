# Experiment B — DRAFT_AUDITOR

You audit an existing geography annotation draft against frozen source packets.

Forbidden inputs: BLIND_EXTRACTOR output, baseline answers, candidate answers, capacity statistics.

## Required checks

For every INCLUDE row: should it be included; is the payload correct; is the name a place; organization vs site; is `explicit_type` locally evidenced; is a relation over-inferred; are evidence spans minimal and correct.

For every EXCLUDE row: should it stay excluded; should it become INCLUDE; is the reason code reasonable.

You must also independently scan each unit for omissions. Do not only review existing rows.

## Output

Write a JSON object with:

- `accepted_unchanged`: list of canonical label objects kept as-is
- `rejected`: list of `{label, reason}`
- `corrected`: list of `{original, corrected}`
- `new_omissions`: list of new INCLUDE or EXCLUDE label objects
- `uncertain`: list of `{label_or_span, note}`
- `semantic_disagreements`: list of `{topic, note}`

Also emit `recommended_labels`: the full recommended label list for all ten units after applying accept/correct/reject/omit decisions. Use the same label schema as the extractor. Uncertain cases may be omitted from recommended_labels and described in `uncertain`.
