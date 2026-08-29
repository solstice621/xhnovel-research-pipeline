Live Wikipedia OpenSearch campaigns. Default CI does not run them.

```text
xhnovel-pipeline run wikipedia fixtures/live/rq-002 --work-dir .runtime/live-rq-002
xhnovel-pipeline run wikipedia fixtures/live/rq-003 --work-dir .runtime/live-rq-003
```

A missing qualifying case is a valid terminal result (`NO_QUALIFYING_CASE_FOUND`).
