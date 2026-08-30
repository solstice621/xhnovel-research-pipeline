# Quickstart

```text
python3 -m pip install -e ".[dev]"
python3 tools/verify_migration_baseline.py
xhnovel-pipeline legacy-check
xhnovel-pipeline run local-slice fixtures/positive/minimal-local
```

The last command is offline. It writes a candidate EvidenceExport under
`.runtime/`. A successful exit verifies the local fixture contract; it does not
make the withdrawn release candidate release-ready.

Run only discovery, retrieval, parsing and CollectionSnapshot freeze:

```text
xhnovel-pipeline collect local fixtures/positive/minimal-local
```

This command deliberately stops before EvidenceBundle, ExtractionRun, Claim and
EvidenceExport creation. `collect wikipedia` uses the live provider and static
HTTP fetcher; it is not part of mandatory offline CI.

```text
xhnovel-pipeline qualify fixtures/positive/minimal-local
xhnovel-pipeline backup .runtime/.../export.json .runtime/.../objects /tmp/export-backup
```

Withdrawn release-candidate notes: `docs/RELEASE-v1.0.0.md`. The `qualify`
command replays frozen RUN-A/RUN-B and source-content injection fixtures for the
exact deterministic mock build. It is not production LLM qualification or a
G12 release decision. That mock emits claims only for the frozen local fixture
Artifact hashes; other content remains claim-free. Live Wikipedia (`run
wikipedia`) and LLM qualification are not part of default CI.
