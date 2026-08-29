# Sandbox consumer layout (producer side)

This tree is the contract the consumer repo should mirror. It is not a live import.

```text
examples/sandbox-consumer/
  import.lock.yaml          # placeholder; replaced after a verified slice
```

Consumer rules:

1. Import only a verified EvidenceExport.
2. `import.lock` pins export_id + export_hash + producer commit.
3. Mapping lives outside `imports/` and must not rewrite export bytes.
4. Sandbox does not generate FactClaim.
