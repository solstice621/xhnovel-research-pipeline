# Contract surface

The JSON Schemas in this directory define the `0.2-draft` runtime contract.
Envelope schemas reject undeclared fields.

The main lineage is:

```text
NovelIngestionRun -> CollectionSnapshot -> EvidenceBundle
  -> SceneWindow -> SceneScoutRun -> SceneMergeRun -> SceneCandidate
  -> EvidenceExport
```

`ModelAttempt` records every success, retryable response, terminal failure,
refusal, and rejected output. `SceneCandidate` is always `DRAFT` and
`UNVERIFIED`; this contract has no model path that creates a formal `Claim`.

`ResearchRequest.discovery_brief` is bound to every Scene Scout request.
`TriageAssessment` keeps technical access, declared rights, source quality, and
allowed use as separate fields. The authoritative model instructions and output
shape are `profiles/xuanhuan-gameplay-scene-v1/neutral-prompt.md` and
`scene-scout-output.schema.json`.

No schema grants model qualification. Model-backed exports remain
`UNQUALIFIED` with `DEGRADED` auditability until a separate promotion and
accuracy-review workflow exists.
