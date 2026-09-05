# GPT-Pro review packet

This temporary branch contains Experiment D scoring changes, all three frozen experimental Profile packages, the plan and result, and a deliberately exported source-free review snapshot. It is based on engine commit `3372edd47666175db9f6a17bee1b8446635ce355`. It does not include the concurrently developed Observation Research work.

Read [result.md](result.md) and [plan.md](plan.md), then inspect the scorer diff and tests. [review-evidence/export-manifest.json](review-evidence/export-manifest.json) records the exact-byte exports from the local sealed run. The original report is preserved as an experiment-time snapshot; its `.runtime` links describe local-only artifacts. For browser review use these committed copies:

- [Unified metrics and 42 per-unit rows](review-evidence/metrics.json)
- [Reference-relative payload differences](review-evidence/payload-diff.json)
- [Fresh baseline/strict legacy scores](review-evidence/fresh-legacy-metrics.json)
- [Fresh-process native validation](review-evidence/fresh-process-validation.json)
- [Sealed final manifest](review-evidence/final-manifest.json)
- [Reference freeze](review-evidence/reference-freeze.json)
- [Design freeze](review-evidence/design-freeze.json)
- [Semantic diagnostic](review-evidence/semantic-audit.json)
- [Locator diagnostic](review-evidence/locator-audit.json)
- [Historical C rescore limitation](review-evidence/historical-rescore-status.json)
- [Checkout incident](review-evidence/checkout-interruption.json)
- [Exact scoring/validation driver snapshots](review-evidence/audit-scripts/)

The audit scripts are historical execution snapshots, not a new runtime or a portable full-text replay package. The source text, original native records and reference occurrence evidence remain local. Reviewers can inspect scorer behavior, counts, the preregistered decision rule and reference-relative discrepancies; they cannot independently establish source-level semantic truth from this export alone.

The isolated review checkout passed all 11 focused tests and export/hash checks; see [review verification](review-evidence/review-verification.json).

Review questions:

1. Does the type-metric correction compute macro mean, matched-name-weighted accuracy and perfect-unit rate correctly, including empty denominators?
2. Is the common PLACE/TYPE/REL plus joint-place projection fair across legacy and split schemas? Are the legacy untyped-record semantics and shared atom semantics clearly distinguished?
3. Are the development/control/held-out tables and STOP/no-promotion decision supported by the provided counts? Do not substitute these fresh runs for historical C.
4. Does the small held-out reference, single draw, model reference, uncertain lexical boundary or citation-granularity mismatch invalidate any stronger claim?
5. Do any changes bypass native validation, alter production Profiles, or mix in unrelated development?

Report reproducible findings with priority, file/line, scenario, violated invariant and smallest correction. Separate blockers from non-blockers and distinguish evidence-backed bugs from hypotheses needing raw source. Give separate verdicts for merging the experimental/scoring artifacts and promoting a production Profile. Do not treat schema-valid outputs as semantically qualified facts.
