# Build registry

Extractor and parser builds are identified here, not by a model nickname.

- `extractors/registry.json` is the ExtractorBuild registry.
- Changing prompt, profile, executor or parameters requires a new build and `xhnovel-pipeline qualify`.
- Invalidation does not rewrite historical EvidenceExport bytes.
