# Geography type-contract experiment D

Status: preregistered before any D extraction answers or held-out labels are read.
This is a bounded Profile quality experiment, not full-work semantic qualification.

## Frozen design

- Development: the ten native 10k units in Experiment B, with the frozen
  `GOLD-6F9623B825F387835B61` model-adjudicated reference. They are not held out.
- Acceptance: four other native units, one per existing 169-unit stratum, selected
  by minimum SHA256(`geography-type-contract-v1/holdout/` + native unit ID).
  Exclude the ten development units, their immediate neighbors, and ordinals 1–10.
  Freeze selection before reading their text. These are held out from this rule
  design, not a claim that no model has ever processed these book passages.
- Reconstruct complete standalone excerpt corpora from existing native unit
  boundaries, preserving every text byte and the original span mapping. Their
  COMPLETE declaration applies only to the standalone excerpts, never the book.
- All arms use the same excerpt bytes, rights, 10k/1800 window policy, 64-record
  limit, native agent-files executor, and default Codex model in fresh contexts.
  One context answers one task only. Record actual task/answer hashes and model
  label. Do not reuse historical answers as the new baseline.
- Arms: baseline (byte-identical shipped geography-unique-v1), strict (exact
  lexical types in PLACE_MENTION), split (PLACE_MENTION + PLACE_TYPE_ASSERTION).
  Experimental assets stay under this directory; shipped profiles/core unchanged.
- Freeze all three profile packages before execution. No tuning after results.
- One extraction per arm/unit: 42 tasks total. Structural failures retain the
  original answers/attempts; allow at most one native retry, report both rates.
- Fresh acceptance reference: source-only annotation followed by an isolated
  source-based audit before candidate outputs exist. Labels remain model reference,
  not human gold; preserve disputed cases and adjudication provenance.

## Shared semantic scoring

Analysis only projects immutable validated outputs into unit-local atoms:
`PLACE(name)`, `TYPE(name, exact_type_text)`, and the existing relation tuple.
A typed legacy PLACE contributes a PLACE and TYPE; an untyped PLACE contributes
only PLACE. Split TYPE never invents a missing PLACE. No alias/synonym merging.
Gold receives the same projection. A field absent is not a type assertion.

Report per-cohort counts and precision/recall for each atom family. Also compare
joint place facts `(name, set of asserted types)` for all arms, so dropping types
loses joint matches when gold has types. Keep legacy exact-payload metrics for
baseline/strict as diagnostics; they are not comparable to split payload JSON.

Acceptance gates (all required on the four new units):

- place name P >= .90 and R >= .90;
- joint place-fact P >= .75; R falls by no more than .05 against fresh baseline;
- type assertion P >= .80 and R >= .80;
- spatial relation P >= .85 and R >= .85;
- citation containment >= .95 on matched atoms, with denominator disclosed;
- at least 20 reference type assertions and 10 reference relations; otherwise
  acceptance is INCONCLUSIVE, never an automatic pass or a reason to cherry-pick;
- no native rejected final units and no OVERFLOW in the candidate arm.

Containment measures locator alignment, not entailment. Independently inspect
unmatched payloads for organization/place ambiguity, name boundaries and relation
semantics. Automatic categories are reference-relative: extra name, extra type,
wrong-type substitution, missing type, extra/missing relation. They are not
proofs of hallucination or evidence that the reference is exhaustive.

Correct historical type metrics before interpretation: macro unit average,
matched-name-weighted accuracy with integer numerator/denominator, and the former
perfect-unit fraction under an honest name. Preserve historical result bytes.
If original C answers are unavailable, explicitly mark that historical rescore
incomplete rather than substitute a different run.

## Stop and decision

Stop after this matrix. If either candidate passes, prefer split only when its
semantic metrics and record pressure support adoption; do not promote merely
because its place payload contains fewer fields. If neither passes, or acceptance
is underpowered, retain candidate-generation-only status and report the observed
limit. No 5k/adaptive splitter, entity resolver, adjudication runtime, or automatic
map/knowledge-graph publishing is part of this experiment.
