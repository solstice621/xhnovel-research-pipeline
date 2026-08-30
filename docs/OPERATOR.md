# Operator guide

Commands:

```text
xhnovel-pipeline validate collection <catalog.json>
xhnovel-pipeline validate evidence <catalog.json>
xhnovel-pipeline validate qualification <catalog.json>
xhnovel-pipeline validate export <catalog.json>
xhnovel-pipeline run local-slice <fixture-dir>
xhnovel-pipeline run wikipedia <fixture-dir>
xhnovel-pipeline qualify <fixture-dir>
xhnovel-pipeline invalidate-build BLD-... --reason "..."
xhnovel-pipeline verify-export <export.json>
xhnovel-pipeline explain-claim <catalog.json> <claim_id>
xhnovel-pipeline trace-request <catalog.json> <request_id>
xhnovel-pipeline check-artifact <store-root> <artifact_id>
xhnovel-pipeline scan-artifacts <catalog.json> <store-root>
xhnovel-pipeline diff-bundle <bundle-a.json> <bundle-b.json>
xhnovel-pipeline parse-diff <segments-a.json> <segments-b.json>
xhnovel-pipeline freeze-bundle <catalog.json> <bundle_id>
xhnovel-pipeline backup <export.json> <store> <dest>
xhnovel-pipeline restore <backup-dir> <store>
xhnovel-pipeline gc <catalog.json> <store> [--apply]
xhnovel-pipeline revoke-export <export.json> --reason "..."
```

CAS lives in `.runtime/objects`. Do not put production artifacts only in `/tmp`.

Interrupted SearchRun/ParseRun: create a new run with `retry_of`. Never rewrite the old record.

GC must not delete ids cited by snapshots, bundles or exports. `--apply` only removes unreferenced CAS objects. Retention deletes write `RETENTION_DELETED` tombstones on replica status.

Revocation is a sidecar next to the export; export bytes stay immutable.
