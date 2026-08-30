# Build registry

Extractor and parser builds are identified here, not by a model nickname.

- `extractors/registry.json` is the ExtractorBuild registry.
- Changing repository commit, executable source hash, model, prompt, profile, executor, tool policy or parameters requires a new build and `xhnovel-pipeline qualify`.
- Invalidation does not rewrite historical EvidenceExport bytes.
