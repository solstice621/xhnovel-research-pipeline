# XHNovel Research Pipeline

> 面向玄幻小说研究的自动搜索、证据收集、机械解析与可审计事实提取管线。

**当前状态：v1.0.0。** 合同 `0.1-draft-frozen` 为 export v1 兼容面。G0–G12 有可重跑命令与 fixture。默认 CI 离线。真实 Wikipedia 不进入 PR 必跑。Wikipedia 全文不进 Git。

实现语言：Python 3.11+（ADR-0002）。默认 CI 离线。真实 Wikipedia 不进入 PR 必跑。

```text
python3 -m pip install -e ".[dev]"
python3 tools/verify_migration_baseline.py
xhnovel-pipeline run local-slice fixtures/positive/minimal-local
xhnovel-pipeline qualify fixtures/positive/minimal-local
```

迁移基线 `legacy_contract_commit`：`ff8b8bb49685c411fd3b56bb61f9173e30680901`。
SCENE-001 永不自动追认；SCENE-002 为 0 live FactClaim tombstone。

---

## 1. 这个仓库要解决什么

`xhnovel-research-pipeline` 从 `xuanhuan-sandbox` 原有 `research/` 工作流中独立出来，目标不是把旧目录原样搬家，而是建立一条真正可自动运行、可重放、可审计的研究生产线：

```text
ResearchRequest
→ 自动搜索与查询扩展
→ 完整记录搜索结果
→ 来源识别、抓取与去重
→ 内容寻址保存
→ 机械解析与稳定分段
→ 冻结 EvidenceBundle
→ 按需隔离语义提取
→ FactClaim 与证据分级
→ 资格与审计
→ 不可变 EvidenceExport
```

系统优先解决两个过去互相冲突的目标：

1. **收集侧要有吞吐和覆盖**：可以自动执行多轮搜索、抓取和去重；
2. **断言侧要有证据纪律**：任何正式事实断言都必须能追溯到精确输入、具体文本片段、提取器版本和当时生效的策略。

核心原则不是降低旧证据纪律，而是把纪律放到正确的责任域：

> **收集层不产生 FactClaim，因此不承担断言资格门；一旦语义提取开始产生 FactClaim，就必须进入完整的隔离、溯源、资格和审计体系。**

---

## 2. 与 `xuanhuan-sandbox` 的边界

这个仓库是**证据生产者**，不是游戏设计裁决器。

### 本仓负责

- 接收不可变 `ResearchRequest`；
- 搜索规划与 `SearchCampaign`；
- 保存完整 `SearchRun` 和有序 `DiscoveryHit`；
- 来源身份、访问尝试、抓取结果和 Artifact；
- 内容 hash、去重、保留状态与审计状态；
- HTML / PDF 等材料的机械解析；
- 稳定、可定位的 `Segment`；
- Retrieval 级来源分级；
- 来源独立性评估；
- 冻结 `CollectionSnapshot` / `EvidenceBundle`；
- 零项目上下文的语义提取；
- `FactClaim`、冲突与不确定性；
- Extractor build 资格；
- Bundle assurance；
- 不可变 `EvidenceExport`。

### `xuanhuan-sandbox` 继续负责

- 研究什么玩法问题；
- `RESEARCH-QUESTIONS`；
- 当前 GDD / DEC / M-1 / A1 等 Project Context；
- 把事实解释成 Gameplay Structure；
- `COVERED / PARTIAL / ABSENT`；
- `NOT_A_GAP / REJECTED_BY_CONSTRAINT / UNKNOWN`；
- `design-map` 挂接、冲突和收敛；
- 是否新增、修改或晋升游戏机制；
- 最终用户裁决。

因此，本仓的正式提取与导出**不得包含**类似下面的项目判断：

```text
M-1 是否已经支持
current_holder 是否够用
这是不是 NOT_A_GAP
是否应该新增底层机制
是否进入 design-map 收敛
```

这些判断只能在消费者仓库读取固定 EvidenceExport 后完成。

---

## 3. 顶层数据链

目标数据模型：

```text
ResearchRequest
└─ SearchCampaign
   ├─ QuerySpec
   │  └─ SearchRun
   │     └─ DiscoveryHit
   └─ CampaignReport

DiscoveryHit
└─ Source
   └─ Retrieval
      ├─ RetrievalArtifact
      │  └─ Artifact
      └─ TriageAssessment

Artifact
└─ ParseRun
   └─ ParsedDocument
      └─ Segment

Source / Retrieval
└─ OriginAssessment

SearchCampaign + Retrievals + Artifacts
└─ CollectionSnapshot

CollectionSnapshot + Segments + Assessments + SelectionManifest
└─ EvidenceBundle
   └─ ExtractionRun
      └─ FactClaim

ExtractorBuild
└─ BuildQualification

EvidenceBundle + ExtractionRun + BuildQualification
└─ BundleAssurance
   └─ EvidenceExport
```

`Scene` 不再作为整个系统的根对象。一个 ResearchRequest 可以复用已有 Retrieval / Artifact；同一 Artifact 可以被不同 parser 或 extractor 重跑；同一材料也可以服务多个 EvidenceBundle。

---

## 4. 必须保持的核心不变量

### 4.1 Collection Cannot Claim

`SearchRun`、`DiscoveryHit`、`Retrieval`、`Artifact`、`ParseRun` 都不得产生事实断言。

### 4.2 Search Snippet Is Lead-only

搜索摘要、搜索摘录及其大小写/连字符/空格变体全部按 `search_snippet` 类处理，只能作为发现线索，不能伪装成完整页面证据。

### 4.3 Retrieval Is an Attempt

`Source` 是逻辑资料身份；`Retrieval` 是一次具体访问。同一 URL 的搜索摘要、完整页面、授权预览、目录页、PDF 或不同时间版本必须可分别表示。

### 4.4 Artifact Is Content-addressed

实际处理的内容按真实字节 SHA-256 寻址。URL 不是内容身份，声明 hash 不能使用占位值。

### 4.5 Frozen Inputs Before Claims

正式 `ExtractionRun` 必须绑定已经冻结的 `EvidenceBundle.bundle_hash`。Bundle 冻结后增删任何 Retrieval、Artifact、Segment 或 Assessment，都必须创建新 Bundle 和新 ExtractionRun。

### 4.6 Claim Binds Exact Segments

每条 live `FactClaim` 必须引用具体 `segment_id`，并保留足以回到原 Artifact 的定位与 hash。

### 4.7 Isolation Is Proven by Manifest

“没有注入项目背景”不能只靠一个布尔字段声明。Extraction 必须保存完整输入 allowlist、prompt hash、tool input hash、模型/执行器版本和 context isolation mode。

### 4.8 Evidence Quality ≠ Copyright / Access Legitimacy

证据等级回答“材料离原始事实有多近、能支撑什么”；版权、授权和保存策略回答“是否允许访问、保存和再分发”。两者必须分离，不能因为来源未授权就偷偷改变证据语义，也不能因为证据等级高就自动允许持久化全文。

### 4.9 Independence Is Assessed, Not Assumed

“不同网站”不自动等于独立来源。转载、共同原始出处、近重复文本和明确引用链都必须通过可审计的 `OriginAssessment` 表达；`UNKNOWN` 不能被当作独立来源用于强确认。

### 4.10 History Is Immutable

进入终态的 Run、Snapshot、Bundle、Qualification 和 Export 不原地修改。重试、重解析、重新提取创建新对象，并通过 `retry_of` / `supersedes` 建立关系。

---

## 5. 证据纪律

初始目标保留旧研究工作流中已经证明有价值的纪律，同时重构其承载方式：

- 原始/接近原始来源优先；
- Search snippet 永远只作线索；
- `SUPPORTED` / `CONFIRMED` 必须满足明确来源规则；
- 双二手来源确认必须经过独立性检查；
- 冲突事实并列，不由提取器静默调和；
- 提取器只能读取冻结输入；
- 事后新增来源不能绑定到旧 ExtractionRun；
- 对抗夹具校准 extractor build，而不是给每个搜索结果征税；
- 高风险和抽样 Bundle 再做 bundle 级双跑/人工审计；
- 模型、prompt、profile、schema、执行器或关键参数变化后，旧 build 资格不得静默继承。

目标 assurance 层级：

```text
UNQUALIFIED
BUILD_QUALIFIED
BUNDLE_VERIFIED
HUMAN_AUDITED
```

---

## 6. Artifact 与保存策略

Git 只保存：

- 合同与 schema；
- policy；
- profile；
- fixture；
- qualification 记录；
- manifest；
- 精选 EvidenceExport；
- 架构与操作文档。

Git **不作为网页正文数据湖**。

原始材料进入内容寻址 ArtifactStore。开发第一版可以使用 gitignored 的本地 CAS，例如：

```text
.runtime/objects/sha256/
```

但正式 EvidenceExport 所依赖的必要 Artifact 不得只存在于临时目录。因版权或保留策略只能保存 metadata / excerpt 时，必须显式降低 auditability，而不是伪装成完整可重放。

---

## 7. 当前实施顺序

仓库当前处于 **Phase 0–1**。不要跳到搜索 API 或 LLM prompt。

### Phase 0 — Migration Baseline

- 固定 `xuanhuan-sandbox` 旧 `research/` 的最终迁移基线；
- 梳理旧 checker / fixture 的行为约束；
- 明确 SCENE-001 legacy 与 SCENE-002 negative fixture 的迁移角色。

### Phase 1 — Architecture & Contracts

- 冻结实体、关系和状态机；
- 定义 canonicalization 与 hash domain；
- 定义 `ResearchRequest` / `EvidenceBundle` / `EvidenceExport`；
- 定义 source tier、independence、retention、qualification policy。

### Phase 2 — Stack & Repository Bootstrap

- 通过 ADR 选择实现语言和依赖；
- 建立 schema validator、test runner、CI；
- 不访问真实网络。

### Phase 3 — Offline Vertical Slice

用完全本地 fixture 跑通：

```text
Request → Search → Retrieval → Artifact → Parse → Segment
→ Frozen Bundle → Extraction → Claim → Qualification → Export
```

### Phase 4+ — Real Pipeline

本地纵切通过后才依次接入：

1. 一个真实搜索 Provider；
2. 静态 HTTP/PDF 收集；
3. 机械解析；
4. EvidenceBundle freeze；
5. `xuanhuan-gameplay-scene/v1`；
6. Extractor qualification；
7. Sandbox export/import；
8. 真实 Pilot；
9. Legacy cutover；
10. v1.0 hardening。

---

## 8. v1.0 明确不做

为了避免把它膨胀成通用研究平台，v1.0 暂不做：

- UI；
- 分布式任务调度；
- 多租户；
- 插件市场；
- 多领域 profile 平台；
- 通用知识图谱；
- 向量数据库作为架构前提；
- 浏览器自动化集群；
- 绕过登录墙、付费墙、验证码或访问控制；
- 自动修改 `xuanhuan-sandbox` 的 design-map；
- 自动生成或晋升游戏机制；
- 把全部抓取正文提交进 Git。

任何新增基础设施都应先回答：

> 它解决了哪个已经真实发生的研究失败，或者哪个已经冻结的 v1.0 验收门？

答不出来就不进入当前范围。

---

## 9. 预期仓库结构

当前目录会随着 Phase 1–3 逐步建立，目标结构为：

```text
xhnovel-research-pipeline/
├── README.md
├── AGENTS.md
├── ARCHITECTURE.md
├── IMPLEMENTATION_PLAN.md
├── adr/
├── contracts/
├── policies/
├── profiles/
│   └── xuanhuan-gameplay-scene-v1/
├── fixtures/
│   ├── positive/
│   ├── negative/
│   └── legacy/
├── examples/
├── qualifications/
├── exports/
├── src/
├── tests/
├── tools/
└── .runtime/             # gitignored runtime data / CAS
```

不要为了与这棵树完全一致而提前创建空目录；目录只有在获得明确执行职责时才建立。

---

## 10. 初始消费者契约

第一消费者是：

```text
solstice621/xuanhuan-sandbox
```

消费者提交固定 `ResearchRequest`；本仓返回固定 `EvidenceExport`。双方通过 commit、schema version、bundle hash 和 export hash 锁定，不依赖对方 `main` 的最新状态，也不使用 Git submodule 共享活动工作树。

---

## 11. 完成标准

本项目不是“文档写完整”就算完成。v1.0 至少需要证明：

- 自动多轮搜索可审计；
- 声明搜索窗口内 provider 返回的结果被完整记录；
- 抓取字节可由 Artifact hash 追踪；
- HTML/PDF 可以离线重新解析；
- Bundle 冻结后不能事后补证据；
- 每条 live Claim 能追到具体 Segment；
- 项目上下文和网页内 prompt injection 都不能污染提取；
- 至少一个真实 ExtractorBuild 通过资格；
- 至少一个真实 Bundle 达到 `BUNDLE_VERIFIED`；
- 证据不足或冲突的案例能够正确保持 `UNKNOWN / CONFLICTING`；
- EvidenceExport 能被 `xuanhuan-sandbox` 校验和锁定导入；
- SCENE-001 不被自动追认；
- SCENE-002 的历史假绿路径持续由负向 fixture 阻断；
- 旧 `xuanhuan-sandbox/research` 不再承担活动证据生产职责。

---

## 12. 协作规则

面向 AI Agent 的详细实现、证据、测试和修改纪律见 [`AGENTS.md`](./AGENTS.md)。

当前最重要的下一步是生产 LLM 提取器资格（新 build），而不是再扩写收集合同。
