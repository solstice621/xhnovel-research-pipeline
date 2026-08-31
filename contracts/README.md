These JSON Schemas are the complete contract surface of the standalone novel
pipeline. `additionalProperties` is false on envelope objects. Plot profile
payloads are governed by
`profiles/xuanhuan-gameplay-scene-v1/profile.schema.json`.

`CollectionDecision` and `CollectionReview` record independent source and
chapter-identity review. `NovelWork`, `NovelChapter`, `NovelIngestionRun`,
`NovelRankingRun`, `NovelSourceResolution`, and `PlotAnalysis` record the novel
workflow. `EvidenceBundle`, `ExtractionRun`, `Claim`, and `EvidenceExport` bind
the successful model path to frozen inputs and CAS artifacts.

No schema in this branch grants model qualification. Model-backed exports must
remain `UNQUALIFIED` and `DEGRADED`.
