# Phase 0 试验报告：斗破苍穹

Run: `.runtime/exploration/run-002/`
Operator attestation: `OPA-89478E55445209B7F25B`（从 run-001 复制，未改签）
Brief: `XBR-8759B67E1C11A23A9AF5`（通用 discovery brief，不含作品名/人名/章节）

## 搜索范围

Phase 0 **允许并已经**在开放网络上扩大检索：百科、问答、正版书城、授权英译站、
馆藏/出版社信息，以及盗版聚合站的**定位记录**。

**未**把任何网页正文冻成证据源；**未**下载盗版全文；**未**把 Lead 写入 discovery brief。

起点 `https://www.qidian.com/book/1209977/` 返回 HTTP 202。
`https://wuxia.bookresource.qq.com/book/1209977/` 列出 1681 章，免费章有正文，VIP 章是占位。
微信读书有授权完本目录，但标「会员卡可读 / 试读结束」。
Wuxiaworld 有授权英译 1648 章，但限免/登录墙。
浙江文艺出版社 30 册精修典藏版存在于图书零售渠道。
搜索命中的聚合站（dushuhao / favddd 等）记为 `UNOFFICIAL_COPY` 候选并跳过。

HTTP 200 ≠ 权利；目录可达 ≠ 全文完整；搜到盗版站 ≠ 可以 ingest。

## WorkRef（密封声明）

- 题名：斗破苍穹
- 作者：天蚕土豆
- 语言：zh
- 外部身份记录：Qidian `1209977`（`external_ids`）
- 密封身份依据：`TITLE_AUTHOR`（准备输入未声明 `STABLE_EXTERNAL_ID`；builder 未把外部 ID 升格为身份依据）

## ResearchLeads（LEAD_ONLY，不是 SceneCandidate）

| id | 假设 | 位置提示 | 来源 |
|---|---|---|---|
| RLD-7A45C9C885C4E8DEB9B4 | 亡母遗戒被另一灵魂占用 | 第九章 药老！ | 维基 + 百度百科 |
| RLD-8ED2C9FEB96CCCC837A8 | 青莲地心火争夺中的持有转移 | 青莲地心火 | 维基 |
| RLD-2FD05A79E4F1ACDD0FCD | 骨炎戒作为通行/庇护凭据 | 骨炎戒 | 百度百科骨炎戒 |

## SourceRef

- adapter: `site`
- index: `https://wuxia.bookresource.qq.com/book/1209977/`
- `chapter_url_pattern`: `/chapter/1209977/[0-9]+/`
- edition_status: `OFFICIAL`
- textual_completeness: `PARTIAL`（付费墙；未把 VIP 占位 HTML 报成 COMPLETE）
- 声明：`SDL-0FF6122F153092316992`
- 构建请求：`HBR-52F6724D4EB6FA06AAC8`

## 权利

准备输入省略 `rights`。`prepare-handoff` 已从 standing attestation 填入
`FAIR_USE_RESEARCH`（`may_store_full_text=true`，`may_send_to_external_model=true`，
`may_export_excerpts=false`）并绑定 `operator_attestation_id=OPA-89478E55445209B7F25B`。

## Evidence Handoff

**未生成。** 无 `handoffs/EHO-*`。

`prepare-handoff` 拒绝：`E-HANDOFF-QUALITY`。
`OFFICIAL` + `PARTIAL` 对应源质量 **Tier D**；Evidence Handoff 要求 Tier A 或 B。

未将 completeness 改报为 `COMPLETE`：那会把 VIP 占位 HTML 冻成「全书」。
未用 Lead 章节提示把执行范围收窄成免费章：当前架构是 `FULL_WORK`。
未下载盗版镜像。

## execute-handoff

未执行。没有合格 Handoff。

## Lead 处置

| Lead | 处置 |
|---|---|
| 三枚密封 Lead | 已密封；权利已绑定；**源质量不合格**，不能宣称 READY_FOR_XHNOVEL |
| Evidence Compiler | 未启动 |

## 额外未密封 Lead 假设（第二轮搜索，仍是 LEAD_ONLY）

- 幽海纳戒：物理夺取后须抹除灵魂印记才获得使用权。
- 陀舍古帝玉：碎片钥匙/人质交换，持有与开启权限可能分离。

未并入已密封三枚 Lead：Handoff 仍会被质量门拒绝，重跑 prepare 不会改变结果。

## 结论

**不一定要走「官方连载站」。** 能进 Evidence Compiler 的是源质量 A/B，不是「必须起点 HTML」：

- `OFFICIAL + COMPLETE` → A
- `PUBLISHED_EDITION + COMPLETE` 或 `USER_VERIFIED_COPY + COMPLETE` → B
- `UNOFFICIAL_COPY`（即使看似全文）或任何 `PARTIAL` → D，只能当 Lead

网上已经尽力找到：正版电子书目录、纸质出版信息、授权英译站、百科/问答线索。
卡住的是**可冻的完整字节**，不是检索广度。盗版镜像不能当作源。

下一步最快的合格路径：操作者提供已购完本（微信读书/起点导出、EPUB、或纸质/精修版校对文本），
声明 `USER_VERIFIED_COPY` + `COMPLETE`（或出版社电子书 `PUBLISHED_EDITION` + `COMPLETE`）。
