# Quickstart

```text
python3 -m pip install -e ".[dev]"
python3 tools/verify_migration_baseline.py
xhnovel-pipeline legacy-check
xhnovel-pipeline run local-slice fixtures/positive/minimal-local
```

The last command is offline. It writes a verified EvidenceExport under `.runtime/`.

```text
xhnovel-pipeline qualify fixtures/positive/minimal-local
xhnovel-pipeline backup .runtime/.../export.json .runtime/.../objects /tmp/export-backup
```

Release notes: `docs/RELEASE-v1.0.0.md`. Live Wikipedia (`run wikipedia`) and LLM qualification are not part of default CI.
