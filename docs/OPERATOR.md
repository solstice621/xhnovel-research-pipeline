# Operator guide

Commands:

```text
xhnovel-pipeline validate collection <catalog.json>
xhnovel-pipeline validate evidence <catalog.json>
xhnovel-pipeline validate qualification <catalog.json>
xhnovel-pipeline validate export <catalog.json>
xhnovel-pipeline run local-slice <fixture-dir>
xhnovel-pipeline verify-export <export.json>
xhnovel-pipeline explain-claim <catalog.json> <claim_id>
xhnovel-pipeline trace-request <catalog.json> <request_id>
xhnovel-pipeline check-artifact <store-root> <artifact_id>
xhnovel-pipeline scan-artifacts <catalog.json> <store-root>
xhnovel-pipeline diff-bundle <bundle-a.json> <bundle-b.json>
```

CAS lives in `.runtime/objects`. Do not put production artifacts only in `/tmp`.

Interrupted SearchRun/ParseRun: create a new run with `retry_of`. Never rewrite the old record.

GC must not delete ids cited by snapshots, bundles or exports. Retention deletes write `RETENTION_DELETED` tombstones on replica status.
