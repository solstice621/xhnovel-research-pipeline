# Live campaign reports

These JSON files are audit summaries of real Wikipedia OpenSearch campaigns.
They record query, ordered hits, selection, stop_reason and export hash.
They do **not** store article bytes (those stay in local CAS).

Both first live requests were recorded as `NO_QUALIFYING_CASE_FOUND`. They are
historical release-candidate records, not valid G10 evidence: the producer
qualification and import validation basis was later invalidated.

The table below records the candidate bytes only. It does not assert the
current state of any consumer repository.

| request | export_id | export_hash | claims |
|---|---|---|---|
| REQ-LIVE-RQ002 | EXP-LIVE-RQ002 | sha256:e8c4ca5fe0a7e65f942a38187d3255a0de57f78053cbf0c79c3e699606680ae3 | 0 |
| REQ-LIVE-RQ003 | EXP-LIVE-RQ003 | sha256:11c3b3a0be328925d3e7a9e16779b190405cbdf9ca294cb88d1585f305cde065 | 0 |

Human audits: `human-audit-rq-002.md`, `human-audit-rq-003.md`.
