# Phase 0 试验报告：斗破苍穹 fetch trial（run-003）

Run: `.runtime/exploration/run-003/`
Operator attestation: `OPA-89478E55445209B7F25B`（从 run-001 复制，未改签）
Brief: `XBR-8759B67E1C11A23A9AF5`（通用 discovery brief，不含作品名/人名/章节）

本轮问题是：**新质量规则下，能否抓到可冻字节并做成 Handoff。**

网页、百科、目录、搜索命中仍是 `LEAD_ONLY`，不是 SceneCandidate。

## 搜索与源选择（技术可达 ≠ 权利）

详见 `search-log.md`。摘要：

| 候选 | 结果 | 声明 |
|---|---|---|
| `qidian.com/book/1209977/` | HTTP 202 人机验证 | 未用 |
| `wuxia.bookresource.qq.com` | UA 200；后部 VIP 占位 | 只能诚实报 `PARTIAL` |
| `senquge.com` | 正文在，但目录 `javascript:;` | site adapter 0 章 |
| `www.bqg.info/book/164/` | 普通 href 约 1651 章；pipeline UA HTTP 403 | 未改 UA 欺骗 |
| `www.shubaobiquge.com/169709/` | pipeline UA HTTP 200；1663 个章节 href；无 VIP 文案 | 证不了正版 → `UNKNOWN` |

站点 adapter 只冻目录上的第 1 页，跟不了章内「下一页」。Host 用 pipeline `HttpFetcher`（冻结 UA）跟分页，写入本地 `kind: directory`。这不是产品爬虫，也不是第二条 Scene Scout。

`edition_status=UNKNOWN`：官方/授权地位未证明，**不是**已判决盗版。未标 `UNOFFICIAL_COPY`（那一档只用于已明确声明的未授权/侵权副本）。

## WorkRef（密封声明）

- 题名：斗破苍穹
- 作者：天蚕土豆
- 语言：zh
- 身份：`STABLE_EXTERNAL_ID` / qidian `1209977`
- `work_ref_id`: `WREF-7717D203AC840E109907`

## ResearchLeads（LEAD_ONLY，不是 SceneCandidate）

与 run-002 同一组假设；未写入 `discovery_brief` / Novel Spec / Scene Scout。

| id | 假设 | 位置提示 |
|---|---|---|
| RLD-7A45C9C885C4E8DEB9B4 | 亡母遗戒被另一灵魂占用 | 第九章 药老！ |
| RLD-8ED2C9FEB96CCCC837A8 | 青莲地心火争夺中的持有转移 | 青莲地心火 |
| RLD-2FD05A79E4F1ACDD0FCD | 骨炎戒作为通行/庇护凭据 | 骨炎戒 |

本地抽查：`input/chapters/0021.txt` 标题为「第九章 药老!」，正文含药老/戒指占用叙述。这只是 host 对抓取完整性的核对，**不是**把 Lead 升成 KNOWN。

## SourceRef

- adapter: `directory`
- locator: `file:///workspace/.runtime/exploration/run-003/input/chapters`
- `source_ref_id`: `SREF-D06EF4993EBD14574063`
- 文件：1663 个 `.txt`，合计 15 283 428 bytes；无空文件；无 VIP 占位文案
- 目录顺序保留 HTML catalog：开头有手游传记/感言/最新章重复列出，然后从「第一章 陨落的天才」顺排到约第一千六百二十章，末尾有作者感言。章内分页已拼进同一文件。
- `edition_status`: `UNKNOWN`
- `textual_completeness`: `COMPLETE`（相对该 HTML 目录的 1663 条 href，不是出版社 licence 证明）
- 声明路径：`source-declarations/`（prepare 密封）

## 权利

准备输入省略 `rights`。`prepare-handoff` 从 standing attestation 填入
`FAIR_USE_RESEARCH`（store+model true，`may_export_excerpts=false`）
并绑定 `operator_attestation_id=OPA-89478E55445209B7F25B`。

## Evidence Handoff

已生成：`handoffs/EHO-E6C3528C9908B783DFAB/handoff.json`

- `source_quality_tier`: **B**（COMPLETE + UNKNOWN）
- `status`: `READY_FOR_XHNOVEL`
- `execution_scope`: `FULL_WORK`
- `contains_evidence`: false
- `content_binding`: `DEFERRED_TO_INGESTION`
- `expected_input_spec_hash`: `sha256:099b81baa4fa5ffe64b8110adc2ba5acab16d9f36db9a9eccb0f600c6140e93d`
- Novel Spec `request.discovery_brief` 仍是通用句，不含斗破/萧炎/章节号
- Handoff 校验收据：`DETERMINISTIC_REPLAY` / `PASS`

## execute-handoff

已跑：`.runtime/novel-research/run-003`，`--executor agent-files`。

- 尝试：`HAT-512882ED2ADD27E0CC09`
- Ingest：`NING-0621409EE5ADE1292F9A`，status `SUCCEEDED`，1663/1663 章
- Hash closure：`expected_input_spec_hash == input_spec_hash == sha256:099b81baa4fa5ffe64b8110adc2ba5acab16d9f36db9a9eccb0f600c6140e93d`
- 退出码 **3** = `WAITING_FOR_AGENT`（不是失败）
- 待答 Scene Scout 任务：**677** 个 native agent-files 窗口
- Native task 的 `discovery_brief` 仍是通用句；窗口 `untrusted_text` 含小说正文（含人名），那是源文本，不是 Lead 注入

本会话**不**完成 677 个窗口。抓取验收到此为止：本地 directory 可冻、Handoff Tier B、ingest 字节已进 CAS。

## Lead 处置

| Lead | 处置 |
|---|---|
| 三枚密封 Lead | 已密封；权利已绑定；源质量 Tier B；Handoff 已生成 |
| 网页搜索材料 | 仍是 LEAD_ONLY |
| Evidence Compiler | ingest 已 SUCCEEDED；scout 停在 WAITING_FOR_AGENT（677 窗）；未把 Lead 注入窗口 |

## 未做 / 禁止项

- 未改 pipeline User-Agent 去绕过 403
- 未把 VIP 占位报 COMPLETE
- 未把百科/搜索升成 SceneCandidate
- 未用 Lead 章节收窄执行范围
- 未提交 `.runtime/` 或 host `fetch_source.py`
