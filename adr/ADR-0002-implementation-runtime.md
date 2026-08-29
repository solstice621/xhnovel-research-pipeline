# ADR-0002 — Implementation runtime

status: accepted  
date: 2026-08-29

## Decision

v1 is implemented in Python 3.11+ with a single-process CLI and file-backed
CAS. Contracts remain language-neutral JSON Schema.

## Why

The legacy checker, HTML/PDF ecosystem and later LLM tooling are already
Python. No reverse evidence required a different language. This is a
selection gate, not a claim that other languages are forbidden for later
reimplementations of the same contracts.

## Consequences

`pip install -e .` plus `xhnovel-pipeline` is the developer entry. CI is
offline pytest + schema/fixture validation. No database or distributed queue
in v1.
