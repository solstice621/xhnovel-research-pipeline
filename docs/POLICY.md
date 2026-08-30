# Policy guide

Authoritative files are `policies/*.yaml`. `policies/manifest.yaml` is hashed
into `policy_bundle_hash`. Changing any listed file changes every new bundle
and export hash.

| Policy | Answers |
|---|---|
| source-tier-v1 | what the material can support |
| access-kind-v1 | snippet aliases; no pirate-site tier cap |
| origin-independence-v1 | whether two sources are independent |
| retention-v1 | what may be stored |
| claim-grading-v1 | CONFIRMED / SUPPORTED rules |
| isolation-v1 | what extraction may read |
| qualification-v1 | build vs bundle assurance |

Do not encode copyright into tier.
