# Legacy Migration Baseline

status: FROZEN  
schema_family: xuanhuan-sandbox/research v0.2 + PR #10 user decision  
`legacy_contract_commit`: `ff8b8bb49685c411fd3b56bb61f9173e30680901`  
sandbox `main` at freeze announcement: `5a2b35e8d2c62ee4827b5df491e70322575e4404`

This file is the unique, immutable migration baseline for `xhnovel-research-pipeline`.
Recompute hashes with:

```text
python3 tools/verify_migration_baseline.py
```

## 1. Why this commit

`IMPLEMENTATION_PLAN.md` required processing sandbox PR #10 before pinning
`legacy_contract_commit`. PR #10 (`research: 不对盗版网站做分层限制`) is the
user-approved contract change:

- Evidence **tier** measures textual proximity to the original work.
- Site authorization / unauthorized reprint does **not** force a tier cap.
- Search snippets remain forced Tier D.
- Retention (no text library) stays a retention rule, not a tier rule.

This repository cannot merge the sandbox pull request. The contract snapshot is
the PR #10 head commit above. After that commit, sandbox `research/` is treated
as a frozen consumer/legacy tree: only severe data-error fixes, no feature
expansion.

## 2. Approved user decisions captured

| Decision | Effect on the new pipeline |
|---|---|
| Search snippet / 搜索摘要 / 搜索摘录 always Tier D | `policies/source-tier-v1.yaml` + access-kind normalization |
| Unauthorized reprint is not banned from Tier A | `access_kind` and `tier` are independent; `access_legitimacy` is separate |
| SCENE-001 stays `0.1-legacy` | Never auto-qualified |
| SCENE-002 is a tombstone | 0 live FactClaim; negative fixture family |
| Isolation / qualification / hash binding | Migrated into collection / evidence / qualification validators |

## 3. Checker and attack-test evidence

Executed against the extracted PR #10 tree (`/tmp/legacy-baseline`) and against
sandbox `main`:

| Command | Result |
|---|---|
| `python3 research/scripts/test_check_evidence_yaml.py` on PR #10 tree | `OK: check_evidence_yaml self-tests passed` |
| `python3 research/scripts/check_evidence_yaml.py` on PR #10 tree | `OK: parsed and checked 3 YAML files; qualification_eligible=0` |
| same two commands on sandbox `main` `5a2b35e8` | same OK; `qualification_eligible=0` |

SCENE-002 live FactClaim count is 0 (`claims: []`, `live_original_fact_count: 0`,
`qualification_eligible: false`). It must not be counted as usable evidence.

## 4. File inventory and SHA-256

Hashes are SHA-256 of the exact bytes at `legacy_contract_commit`.
Snapshot copy: `fixtures/legacy/sandbox-research-ff8b8bb/`.

| Path | SHA-256 | bytes |
|---|---|---|
| README.md | `1ffdd8834fd4b4e4496c6c51ca294fb8e795be3062747c059b53f529b0501526` | 23734 |
| RESEARCH-QUESTIONS.md | `23b0930a9714b5f833cb64735c76408440ebb77197b6d8e40f9c9fe4cff0d5b9` | 4727 |
| scripts/.gitignore | `32dae3052f331ee34d628ef535709b301259a45df7c7522c4d35dcf49873f00b` | 13 |
| scripts/check_evidence_yaml.py | `20f8f6de370df592e8577b65c5750472b34614c6150c61ba1d2dce0db1e458e0` | 29702 |
| scripts/generate_scene_facts.py | `edb86d953cf1aa5fceb8a90260e3651e19e1bb1555ff0b655e091a233f4c464a` | 4694 |
| scripts/requirements.txt | `43191d2055d9d0121e3e91892b83bf6740e8faf50b0ab944afe6ca8a63f565dd` | 78 |
| scripts/test_check_evidence_yaml.py | `a07009abfe313a4741ad73da9939bf1035c4f27441e81246af70962632a750d3` | 55209 |
| scenes/SCENE-2026-08-29-001/adversarial-check.md | `0fe33549ed5f19ce55e6129fcc25a7242846e7f13f22c806376ea100db6ce111` | 3516 |
| scenes/SCENE-2026-08-29-001/analysis.md | `49db7813871b33b774d775ff3773b17fc701387a9b7cc669ad3afcd693a3b733` | 6024 |
| scenes/SCENE-2026-08-29-001/context-snapshot.md | `52158fb7cb16d7c2596a4dad2344a4bef871b7e51bacda6eaa345f073f27e197` | 2910 |
| scenes/SCENE-2026-08-29-001/elements-mapping.md | `6d6f6d44098f3f391fd0cdef9a34607787d4b7657282bcec5dc958cb487e8ba8` | 4911 |
| scenes/SCENE-2026-08-29-001/evidence.yaml | `7f5dd766a8a52c8002c19ea08564f74a29f54a7f3b36dbb53f736546941a6c28` | 6313 |
| scenes/SCENE-2026-08-29-001/report.md | `253eb68b469faa216eb1b0bc9bde51b3bd99254689d8e6e33e4cef387defd4b1` | 3887 |
| scenes/SCENE-2026-08-29-001/scene-facts.md | `4f63d9abcc4919a69a14eee94defca57a3c8ea311517c113ad23f3d0e0a05212` | 13406 |
| scenes/SCENE-2026-08-29-002/adversarial-check.md | `3114a7128aba53026df75412ae38da37ec84dcb4ded44e25bd301872329b0de2` | 1623 |
| scenes/SCENE-2026-08-29-002/analysis.md | `bbf7434db3611e78871f84f3698c7fa97d043e26d1f35023b622619568099b07` | 2352 |
| scenes/SCENE-2026-08-29-002/claims.yaml | `5e4fbf63b1b3bbd2a7c2a460c00cccf6b329cf99afa7564046138d0ba6dc80a0` | 2969 |
| scenes/SCENE-2026-08-29-002/context-snapshot.md | `a75b9b43845afa649eca3fff8bf968ea5d39f12af571a7e5c4845f18034d2dfd` | 3776 |
| scenes/SCENE-2026-08-29-002/elements-mapping.md | `ff0fb0320f553d0dfd165c3297e03e06c07fc1224b61cec9c187e687481bf624` | 2390 |
| scenes/SCENE-2026-08-29-002/evidence.yaml | `f17dbead1d176c9f695793ea4e3b998ff1cbfea0c5685837a63c572d31eca1ff` | 11579 |
| scenes/SCENE-2026-08-29-002/report.md | `e999408415eb412551fbd1c4639de1add0554f61320bfc134fdcb97d31f66d1c` | 3091 |
| scenes/SCENE-2026-08-29-002/scene-facts.md | `fdf36f8b1050fafdcf686ab1b400c55a1319075e37abec75960cb407668d8a78` | 2332 |

Wait I may have mistyped requirements.txt hash - original was `43191d2055d9d0121e3e91892b83bf6740e8faf50b0ab944afe6ca8a63f565dd` without extra `f`. Let me fix that in the actual file.

Looking at original:
`43191d2055d9d0121e3e91892b83bf6740e8faf50b0ab944afe6ca8a63f565dd`

I accidentally wrote `e8fafaf50` with extra f. Need to fix.

I'll fix when writing the verify tool - the tool should be the source of truth by hashing files, and MIGRATION_BASELINE should match.

Let me write the verify tool to generate expected hashes from the snapshot files themselves, and put expected hashes in `fixtures/legacy/baseline-manifest.json` generated from actual files. That's more reliable.

I'll compute hashes from the copied files to ensure they match.