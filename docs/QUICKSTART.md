# Quickstart

```text
python3 -m pip install -e ".[dev]"
python3 tools/verify_migration_baseline.py
xhnovel-pipeline legacy-check
xhnovel-pipeline run local-slice fixtures/positive/minimal-local
```

The last command is offline. It writes a verified EvidenceExport under `.runtime/`.

Live provider and LLM qualification are not part of default CI.
