# Phase 0 exploration report — run-004（从已密封 Phase -1 进入）

Host-audited. Not evidence. `.runtime/` stays out of source control.

## What this run is

Operator said they would start from Phase -1. Phase -1 was already sealed in
`.runtime/planning/run-004/` against the verbatim goal
「帮我收集各种玄幻小说里的“奇遇”」. This run **consumed** that Plan/Brief; it did
not re-author planning, and it did not reuse 青岚令 or run-003's ownership brief
as a stand-in for 奇遇.

```text
ResearchIntake RIN-DC982B940C8ED4D2703B
  -> ExplorationPlan XPL-8DE3A91D103721566E42
  -> ExplorationBrief XBR-43C121A4ED04DCADE2F5
  -> Phase 0 Leads (LEAD_ONLY)
  -> EvidenceHandoff EHO-D265EDFA714EA2C36829  (斗破 only)
  -> execute-handoff WAITING_FOR_AGENT (677 windows)
```

Formal Brief (only prose allowed into `request.discovery_brief`):

> 在已冻结的玄幻源文中，寻找角色因非预先谋划的意外机缘而当场获得传承、宝物、功法、体质、指引，或因此处境骤变的场景。可观察标志包括：误入隐秘之地或遗存、危机中触发未知机缘、拾得非常规之物、遭遇残存意志或无名施助，并立即改变能力、资源或后续行动空间。须与事先约定的交易、任务报酬及常规修炼收获相区分；只采当场可见的机缘发生过程，不采事后转述或目录式罗列。

## Host strategy adherence (not proved by the planning receipt)

Plan diversity: `min_works=5`, `min_interaction_families=4`,
`max_initial_leads_per_work=2`, budget `target_leads=16`.

| Check | Host result |
|---|---|
| works in explicit Lead set | 8 |
| initial Leads | 15 (under the 16 cap; not padded) |
| families covered | all four planner-derived families |
| max per work | 2 |
| `scope.avoid` | empty; no hard genre drop. 遮天 retained with 仙侠/玄幻 boundary note |

Seeds steered search only. They are not in the Brief, native tasks, or initial
candidates.

Drafts for the full set: `input/leads-all.json`.
Search locators: `search-log.md`.

## Lead dispositions

| Work | Initial Leads | Source | Disposition |
|---|---|---|---|
| 斗破苍穹 / 天蚕土豆 | 2（遗戒 / 药老苏醒） | local directory reused from run-003, `edition_status=UNKNOWN`, COMPLETE | sealed into Handoff; `UNVERIFIED_LEAD` |
| 盘龙 / 我吃西红柿 | 2 | none COMPLETE | **UNRESOLVED** |
| 武动乾坤 / 天蚕土豆 | 2 | none COMPLETE | **UNRESOLVED** |
| 完美世界 / 辰东 | 2 | none COMPLETE | **UNRESOLVED** |
| 神墓 / 辰东 | 2 | none COMPLETE | **UNRESOLVED** |
| 遮天 / 辰东 | 2 | none COMPLETE; genre-boundary | **UNRESOLVED** |
| 大主宰 / 天蚕土豆 | 1（九幽雀入体；灵路试炼未作主 Lead） | none COMPLETE | **UNRESOLVED** |
| 星辰变 / 我吃西红柿 | 2 | none COMPLETE | **UNRESOLVED** |

Official Qidian/bookresource remain VIP/`PARTIAL`. No new aggregator crawl.
`UNOFFICIAL_COPY` was not declared merely because official status is unproven.

Unresolved Leads stay in the denominator. They are not SceneCandidates.

## Doupo Handoff (first executable group)

Preparation used the **sealed** `exploration-brief.json` (with `brief_id`), not a
rewritten draft. Only the two Doupo Leads went into `prepare-handoff` because
only this work currently has a COMPLETE declared source.

| Object | ID |
|---|---|
| Brief | `XBR-43C121A4ED04DCADE2F5` |
| Leads | `RLD-886021B732A340DF0CA7`, `RLD-D136E73F9B2AEEF508D9` |
| SourceDeclaration | `SDL-ABC2A8AE30F8AF66B431` |
| EvidenceHandoff | `EHO-D265EDFA714EA2C36829` |
| Readiness | `READY_FOR_XHNOVEL`, source_quality_tier **B** |
| Planning receipt replay | `PCR-BF640547AC7578C6EB26` PASS |
| contains_evidence | false |

Hash closure after ingest:

```text
EvidenceHandoff.novel_spec.expected_input_spec_hash
  == NovelIngestionRun.input_spec_hash
  == sha256:9ff536b77c4fbe246955c110bc09c54710cf2b4ad68b558775de6bc3712326b4
```

Ingestion `NING-082F0DE349DC0FA7ED34` **SUCCEEDED** (1663 chapters, 0 duplicate,
0 ignored). Work `NWK-10E97C977B9FB8F57A09`.

Native task `discovery_brief` matches the sealed Brief; 0/677 task briefs contain
Lead names/chapter guesses/URLs.

This is a **new** work-dir (`.runtime/novel-research/run-004`). It does not
resume run-003's 677 ownership windows.

## Semantic execution status

```text
STARTED  HAT-E57184CA333191E0AD7A
WAITING  HEV-7CB0CB32E37729C79A06   pending_count=677
```

Exit 3 is `WAITING_FOR_AGENT`, not failure. FULL_WORK Scene Scout is **not**
complete. There are **no** SceneCandidates, KNOWN observations, or 奇遇 evidence
exports from this run.

To continue later, answer only the native files under
`.runtime/novel-research/run-004/scene-scout/agent-files/`, then rerun:

```bash
PYTHONPATH=src python3 -m xhnovel_pipeline execute-handoff \
  .runtime/exploration/run-004/handoffs/EHO-D265EDFA714EA2C36829/handoff.json \
  --executor agent-files \
  --work-dir .runtime/novel-research/run-004
```

Do not `--retry` unless this attempt is FAILED/INTERRUPTED. Do not inject Lead
hints into answers.

## What this is not

- Not a finished collection of 奇遇 scenes.
- Not proof that any encyclopedia plot happened.
- Not a narrowing of execution to 「第九章 药老」; scope remains FULL_WORK.
- Not a substitute for answering 677 windows.

Attestation reused: `OPA-89478E55445209B7F25B` (`FAIR_USE_RESEARCH`).
