# Research Pipeline 独立仓库实施总计划 v1.0

> 状态：v1.0.0 发布门已按 G0–G12 执行。数据合同冻结为 `0.1-draft-frozen`（export v1 兼容面）。  
> 工作仓名：`research-pipeline`（仅作计划代号，正式仓名不影响合同）  
> 原仓基线：`solstice621/xuanhuan-sandbox`  
> 当前 `main` 基线：`5a2b35e8d2c62ee4827b5df491e70322575e4404`  
> `legacy_contract_commit`：`ff8b8bb49685c411fd3b56bb61f9173e30680901`

---

## 0. 最终裁决

新仓不是旧 `research/` 的复制品，而是独立的证据生产系统。

### 新仓负责

```text
ResearchRequest
→ SearchCampaign / QuerySpec
→ SearchRun / DiscoveryHit
→ Source / Retrieval / Artifact
→ TriageAssessment / OriginAssessment
→ ParseRun / ParsedDocument / Segment
→ EvidenceBundle
→ ExtractionRun / Claim
→ Qualification / Assurance
→ EvidenceExport
```

### `xuanhuan-sandbox` 继续负责

```text
研究问题为何重要
当前项目状态与设计约束
把中性事实解释成 Gameplay Structure
COVERED / PARTIAL / ABSENT
NOT_A_GAP / REJECTED_BY_CONSTRAINT / UNKNOWN
design-map 挂接、收敛与机制晋升
最终用户裁决
```

### 必须冻结的核心不变量

1. **收集层永不产生 FactClaim。**
2. **机械解析永不产生 FactClaim。**
3. **只有隔离语义提取可以产生 Claim。**
4. Claim 必须引用精确 `segment_id`，不能只引用网页或 `source_id`。
5. `ExtractionRun` 必须绑定不可变 `bundle_hash`；增加或删除任何输入都创建新 bundle。
6. Run、Snapshot、Bundle、Export 均不可原地修改；重试与重跑创建新对象。
7. 搜索摘要始终是独立 `DiscoveryHit/Retrieval`，且证据等级为 Tier D。
8. 证据等级与版权/授权状态分离：是否为未授权转载不自动决定 Tier；保存与导出受独立 retention policy 约束。
9. 来源独立性不能只看平台名；`UNKNOWN` 独立性不能用于“双 Tier B 确认”。
10. 新仓输出项目中立证据，不输出 M-1、`current_holder`、机制缺口或晋升结论。
11. 原始 Artifact 不进入 Git；Git 只保存合同、策略、fixture、示例和精选 export。
12. `xuanhuan-sandbox` 只消费固定 export，不跟随新仓 `main` 漂移。

---

# 1. “最终完成”的定义

本项目到 v1.0 才算完成，需同时满足：

1. 能从 `xuanhuan-sandbox` 的固定 `ResearchRequest` 启动自动搜索。
2. 能记录声明窗口内 provider 返回的所有命中、排名、摘要、分页和筛选结果。
3. 能抓取选中页面，保存原始响应及必要派生表示，完成哈希、去重和持久化。
4. 在完全离线状态下，能从已存 Artifact 重新运行机械解析。
5. 能冻结精确 `EvidenceBundle`，任何成员、策略或 profile 变化都会改变 bundle hash。
6. 能在零 Project Context 的输入环境下运行语义提取。
7. 每条 Claim 都能追溯到精确 Segment、Artifact、Retrieval、SearchRun 和 ResearchRequest。
8. 至少有一个通过对抗夹具与可复现门的 `ExtractorBuild`。
9. 能生成带 producer build、policy hash、assurance level 和 artifact manifest 的不可变 `EvidenceExport`。
10. `xuanhuan-sandbox` 能校验并锁定该 export，再单独进行项目映射。
11. SCENE-001 已成为 legacy migration fixture；SCENE-002 已成为持续失败的负向 fixture 族。
12. 旧 `research/scripts` 和 active scene 工作流已退出主路径，历史仍可追溯。
13. Artifact 完整性扫描、恢复演练、导出验签、资格失效和 revocation 流程均通过。
14. 不依赖 UI、分布式调度、插件市场、通用数据库抽象或多消费者平台化。

---

# 2. 目标数据模型

## 2.1 请求与搜索控制面

### `ResearchRequest`

由消费者提出，描述“找什么”，不声明预期答案。

核心字段：

```yaml
request_id:
origin:
  repository:
  commit:
  external_question_id:
mode: EXPLORE | DEEPEN | CONFIRM | CHALLENGE
discovery_brief:
search_constraints:
extraction_profile:
budget:
created_at:
supersedes:
```

### `SearchCampaign`

表达一次完整自动研究循环，而不是单次 query。

```yaml
campaign_id:
request_id:
planner_build_id:
coverage_goals:
budget:
iterations:
stop_policy_hash:
status:
stop_reason:
created_at:
```

### `QuerySpec`

```yaml
query_id:
campaign_id:
query_text:
query_role: DISCOVER | PRIMARY_SOURCE | CONFIRM | CHALLENGE | CONFLICT
parent_query_id:
derived_from_hit_ids:
generated_by:
rationale:
locale:
```

### `SearchRun`

一次 provider + query + 参数的不可变执行记录。

```yaml
search_run_id:
query_id:
provider_id:
provider_build_id:
parameters:
started_at:
finished_at:
raw_response_artifact_id:
result_set_hash:
status:
retry_of:
```

### `DiscoveryHit`

```yaml
hit_id:
search_run_id:
rank:
url:
title:
snippet:
selection_status: SELECTED | REJECTED | DUPLICATE | UNREACHABLE
selection_reason:
```

---

## 2.2 来源、访问与内容

### `Source`

逻辑资料身份，不代表一次访问。

```yaml
source_id:
canonical_url:
platform_id:
title:
author:
work:
document_location:
```

### `Retrieval`

一次具体访问。

```yaml
retrieval_id:
source_id:
discovery_hit_id:
requested_url:
final_url:
access_kind:
retrieved_at:
http_status:
content_type:
fetcher_build_id:
status:
triage_assessment_id:
retry_of:
```

### `RetrievalArtifact`

解决旧模型中 `Retrieval → Artifact` 一对一不足。

```yaml
retrieval_id:
artifact_id:
role: RAW_RESPONSE | RESPONSE_HEADERS | RENDERED_DOM | SCREENSHOT | PROVIDER_JSON
```

### `Artifact`

```yaml
artifact_id: sha256:<raw-bytes-hash>
media_type:
byte_length:
retention_policy:
durability_status:
created_at:
```

### `ArtifactReplicaStatus`

可变存储健康信息不得进入 Artifact 的不可变身份。

```yaml
artifact_id:
backend_id:
storage_uri:
integrity_status:
last_verified_at:
availability:
```

### `TriageAssessment`

证据等级属于 Retrieval 评估，不属于 Source 永久属性。

```yaml
assessment_id:
retrieval_id:
tier: A | B | C | D
access_legitimacy:
suspected_reprint:
allowed_uses:
selection_decision:
decision_reason:
assessor_build_id:
policy_hash:
assessed_at:
```

### `OriginAssessment`

```yaml
assessment_id:
source_a:
source_b:
relation: SAME_ORIGIN | LIKELY_SAME_ORIGIN | INDEPENDENT | UNKNOWN
confidence:
basis:
assessor_build_id:
policy_hash:
assessed_at:
```

确认规则：只有明确 `INDEPENDENT` 的两条 Tier B 才可共同支撑 ORIGINAL_FACT 的 CONFIRMED；`UNKNOWN` 不计。

---

## 2.3 机械解析

### `ParseRun`

```yaml
parse_run_id:
input_artifact_id:
parser_build_id:
parameters:
output_document_id:
output_hash:
status:
retry_of:
supersedes:
```

### `ParsedDocument`

```yaml
document_id:
input_artifact_id:
parser_build_id:
title:
language:
structure_hash:
```

### `Segment`

```yaml
segment_id:
document_id:
parent_segment_id:
ordinal:
segment_type:
normalized_text:
normalized_text_hash:
source_locator:
```

`source_locator` 可表达 HTML selector、字符范围、PDF 页码与坐标等。

---

## 2.4 冻结边界与语义提取

### `CollectionSnapshot`

冻结某次 Campaign 实际收集到的对象集合，证明“当时系统拥有了什么”。

```yaml
snapshot_id:
campaign_id:
search_run_ids:
hit_ids:
retrieval_ids:
artifact_ids:
triage_assessment_ids:
origin_assessment_ids:
snapshot_hash:
frozen_at:
supersedes:
```

### `EvidenceBundle`

冻结某次提取精确读到什么。

```yaml
bundle_id:
request_id:
collection_snapshot_ids:
document_ids:
segment_ids:
selection_manifest:
profile_id:
policy_bundle_hash:
bundle_hash:
frozen_at:
supersedes:
```

### `ExtractionRun`

```yaml
extraction_run_id:
bundle_id:
bundle_hash:
extractor_build_id:
trigger:
  type: USER | POLICY | SCHEDULE | RETRY | MIGRATION
  actor_id:
  reason:
input_manifest:
  segment_ids:
  system_prompt_hash:
  user_prompt_hash:
  tool_input_hashes:
  allowed_context_artifact_ids:
  forbidden_context_policy_hash:
execution_environment:
  executor_build_id:
  context_isolation_mode:
  model_snapshot:
status:
retry_of:
```

### `Claim`

核心模型保持通用，玄幻动作结构放入 profile payload。

```yaml
claim_id:
kind: ORIGINAL_FACT | RECEPTION
status: ACTIVE | SUPERSEDED | ARCHIVED
grade: CONFIRMED | SUPPORTED | INFERRED | UNKNOWN | CONFLICTING
statement:
profile_schema:
profile_payload:
support:
  - retrieval_id:
    artifact_id:
    segment_id:
    normalized_text_hash:
```

---

## 2.5 资格与导出

### `ExtractorBuild`

```yaml
extractor_build_id:
model:
prompt_template_hash:
parameters:
profile_version:
executor_build_id:
tool_policy_hash:
created_at:
```

### `QualificationRun`

```yaml
qualification_run_id:
extractor_build_id:
fixture_suite_hash:
run_a:
run_b:
adversarial_project_expectation:
source_content_injection:
reproducibility:
result:
qualified_at:
```

### `AssuranceRecord`

```yaml
subject_type: BUILD | BUNDLE | EXPORT
subject_id:
level: UNQUALIFIED | BUILD_QUALIFIED | BUNDLE_VERIFIED | HUMAN_AUDITED
policy_hash:
created_at:
```

### `EvidenceExport`

```yaml
schema_version:
export_id:
export_hash:
producer:
  repository_commit:
  collector_build_id:
  parser_build_id:
  extractor_build_id:
origin_request:
bundle:
claims:
scene_facts:
policies:
assurance:
artifact_manifest:
created_at:
```

---

# 3. 哈希、不可变性与状态机

## 3.1 哈希规则

1. `artifact_id`：原始字节 SHA-256。
2. 结构对象 hash：规范化 JSON 后 SHA-256，不直接 hash YAML 文本格式。
3. 自身 hash 字段不参与自身计算。
4. 存储位置、replica 健康和最近校验时间不进入 Artifact 身份。
5. `bundle_hash` 必须包含：
   - 精确 segment 集合；
   - source/retrieval/artifact 引用；
   - triage/origin assessment；
   - profile version；
   - policy hashes；
   - selection manifest。
6. `export_hash` 必须覆盖完整不可变 export payload。
7. 对列表的排序语义必须逐字段定义：有顺序的保留顺序，无顺序集合按稳定 ID 排序。

## 3.2 状态机

```text
SearchCampaign:
DRAFT → RUNNING → COMPLETED
                → EXHAUSTED
                → BUDGET_STOPPED
                → FAILED
                → CANCELLED

Retrieval:
QUEUED → FETCHING → FETCHED
                  → BLOCKED
                  → UNREACHABLE
                  → FAILED

ArtifactReplicaStatus:
AVAILABLE → MISSING | CORRUPT | RETENTION_DELETED

ParseRun:
QUEUED → RUNNING → SUCCEEDED | FAILED | INCONCLUSIVE

EvidenceBundle:
DRAFT → FROZEN → EXTRACTED → EXPORTED
               ↘ SUPERSEDED

ExtractionRun:
QUEUED → RUNNING → SUCCEEDED | FAILED | INCONCLUSIVE

ExtractorBuild:
UNQUALIFIED → QUALIFIED → INVALIDATED

EvidenceExport:
CREATED → VERIFIED → IMPORTED
        ↘ REVOKED
```

## 3.3 不可变规则

- 所有 Run 永远不可变。
- 重试使用 `retry_of`。
- 新 parser/model/prompt 重跑使用 `supersedes`。
- Bundle 冻结后不得增删任何成员。
- Build 资格失效不重写历史 Export；历史记录保留，但该 build 不得生成新合格 export。
- Artifact 丢失不修改 export bytes；通过独立状态记录将审计性标记为 DEGRADED 或 REVOKED。

---

# 4. 目标仓库结构

```text
research-pipeline/
├── README.md
├── ARCHITECTURE.md
├── IMPLEMENTATION_PLAN.md
├── DOMAIN_MODEL.md
├── STATE_MACHINES.md
├── SECURITY.md
├── adr/
│   ├── ADR-0001-repository-boundary.md
│   ├── ADR-0002-implementation-runtime.md
│   ├── ADR-0003-canonical-hashing.md
│   └── ADR-0004-artifact-durability.md
├── contracts/
│   ├── research-request.schema.json
│   ├── search-campaign.schema.json
│   ├── collection-snapshot.schema.json
│   ├── evidence-bundle.schema.json
│   ├── extraction-run.schema.json
│   ├── claim.schema.json
│   ├── qualification.schema.json
│   └── exports/
│       └── xuanhuan-evidence-v1.schema.json
├── policies/
│   ├── access-kind-v1.yaml
│   ├── source-tier-v1.yaml
│   ├── origin-independence-v1.yaml
│   ├── claim-grading-v1.yaml
│   ├── retention-v1.yaml
│   ├── isolation-v1.yaml
│   └── qualification-v1.yaml
├── profiles/
│   └── xuanhuan-gameplay-scene-v1/
│       ├── profile.schema.json
│       ├── extraction-spec.yaml
│       ├── neutral-prompt.md
│       └── renderer-spec.md
├── fixtures/
│   ├── positive/
│   ├── negative/
│   ├── adversarial/
│   └── legacy/
├── examples/
├── src/
├── tests/
└── tools/

.runtime/                 # gitignored
├── objects/sha256/
├── runs/
├── manifests/
└── exports/
```

生产高频 manifest 和原始对象默认不提交 Git；只提交 fixture、示例和经选择的 reference export。

---

# 5. 分阶段实施计划

## 阶段 0 — 固定旧仓迁移基线

### 目标

避免在旧合同仍变化时复制出第二份分叉真相源。

### 设计

- 明确 PR #10 的最终裁决。
- 将“证据等级”和“保存/授权策略”拆成两套 policy。
- 冻结 `legacy_contract_commit`。
- 明确当前两个 scene 的迁移角色：
  - SCENE-001：legacy migration fixture；
  - SCENE-002：negative/tombstone fixture。

### 实现

1. 审阅并处理 PR #10。
2. 重新运行旧 checker 与旧攻击测试。
3. 生成 `MIGRATION_BASELINE.md`，记录：
   - commit；
   - `research/README.md` hash；
   - 两个 scene 的文件清单与 hash；
   - checker/test 脚本 hash；
   - 已批准用户裁决；
   - 仍未通过的资格状态。
4. 生成旧对象到新对象的迁移映射草案。
5. 本阶段不创建新仓实现代码。

### 测试

- `check_evidence_yaml.py` 通过。
- `test_check_evidence_yaml.py` 全通过。
- 基线文件 hash 可重算。
- 不存在未决的 P0/P1 研究合同 PR。
- 确认 SCENE-002 仍为 0 live FactClaim，不被误计为有效证据。

### 完成门 G0

- 迁移基线 commit 唯一且不可变。
- 之后旧仓 research 合同只允许修复严重数据错误，不再扩写功能。

---

## 阶段 1 — 架构 RFC 与数据模型冻结

### 目标

在选技术栈、建搜索器之前，冻结对象、边界、状态和哈希语义。

### 设计

必须完成：

1. `ARCHITECTURE.md`
2. `DOMAIN_MODEL.md`
3. `STATE_MACHINES.md`
4. `HASHING_AND_CANONICALIZATION.md`
5. `SECURITY.md`
6. `EXPORT_CONTRACT.md`
7. 四份核心 ADR：
   - 仓库边界；
   - 实现语言选择门；
   - 内容寻址；
   - Artifact 持久性。

需要逐对象回答：

- 谁创建；
- 是否不可变；
- ID 如何生成；
- hash 覆盖什么；
- 能否 retry；
- 能否 supersede；
- 删除后如何审计；
- 哪个 validator 负责。

### 实现

- 编写 language-neutral JSON Schema 草案。
- 为每个对象提供最小合法样例与最小非法样例。
- 建立 ID 前缀规范，例如 `REQ-`、`CAM-`、`QRY-`、`RUN-`、`RET-`、`BND-`、`CLM-`、`EXP-`。
- 冻结 `CollectionSnapshot` 与 `EvidenceBundle` 的区别。
- 冻结 source tier、origin independence、retention、claim grading 的责任边界。
- 冻结 historical export 在 build invalidation 后的语义。

### 测试

- 从 ResearchRequest 到 Export 的引用链能在纸面完整走通。
- 任一 Claim 均能回溯到 Segment → Artifact → Retrieval → SearchRun → Request。
- 任一对象都不存在“修改旧对象还是新建对象”的歧义。
- 任一可变运行状态都不进入内容身份 hash。
- 用 SCENE-002 的历史问题逐项攻击模型：
  - 材料丢失；
  - 事后补来源；
  - 占位 hash；
  - snippet 冒充页面；
  - 平台别名假独立；
  - zero claims 却 eligible。
- 独立审查者不能提出未回答的 P0/P1 模型问题。

### 完成门 G1

- 数据模型版本标记 `0.1-draft-frozen`。
- 技术栈仍可未定，但合同不依赖具体语言。
- 此后 schema 破坏性变化必须走 ADR。

---

## 阶段 2 — 新仓初始化与合同验证骨架

### 目标

建立可持续开发骨架，但仍不访问网络、不调用 LLM。

### 设计

- 确定 schema versioning。
- 确定错误码规范。
- 确定统一验证入口。
- 在本阶段通过 ADR-0002 选择实现技术栈；若无反向证据，优先选择能最快复用现有 Python 校验经验、HTML/PDF 生态和 LLM 工具链的方案，但合同继续保持语言无关。

### 实现

1. 创建独立仓库。
2. 落盘目录结构、ADR、contracts、policies、profiles。
3. 建立统一命令入口，逻辑上至少支持：
   - `validate collection`
   - `validate evidence`
   - `validate qualification`
   - `validate export`
4. 建立 CI：
   - schema；
   - unit；
   - fixture；
   - lint/type；
   - dependency lock。
5. 未知字段策略明确：
   - 合同对象默认拒绝未知字段；
   - profile payload 由 profile schema 管理。
6. 增加 canonical JSON 与 hash 库。

### 测试

- 所有合法样例通过。
- 每个 schema 至少有一个非法 fixture。
- 字段遗漏、类型错误、未知枚举、悬空引用均失败。
- 同一逻辑对象不同 YAML 排版得到同一 canonical hash。
- 实质字段变化导致 hash 变化。
- CI 完全离线可运行。

### 完成门 G2

- 仓库从空 clone 可一次命令完成验证。
- 尚无任何真实搜索、真实网络或真实 LLM 依赖。

---

## 阶段 3 — 全本地端到端纵切

### 目标

先证明体系结构成立，再接入真实搜索器。

### 设计

使用 Fake Provider、固定 HTML fixture、确定性 Mock Extractor 跑通：

```text
ResearchRequest
→ SearchCampaign
→ QuerySpec
→ SearchRun
→ DiscoveryHit
→ Retrieval
→ Artifact
→ ParseRun
→ Segment
→ CollectionSnapshot
→ EvidenceBundle
→ ExtractionRun
→ Claim
→ EvidenceExport
→ Export Verify
```

### 实现

1. 本地文件系统 CAS：`.runtime/objects/sha256/`。
2. Fake Search Provider：读取固定 provider response。
3. Fixture Fetcher：把本地 fixture 模拟成 Retrieval。
4. 最小 HTML/Text parser。
5. Snapshot/Bundle freeze。
6. Mock Extractor：
   - 输出固定、可预测 Claim；
   - 强制引用 Segment。
7. Export builder 与 verifier。
8. 审计链打印工具。

### 测试

必须证明：

1. 无网络可完整重放。
2. 重解析不触发重新抓取。
3. 同一 Retrieval 可被多个 Request/Bundle 复用。
4. Claim 不引用 Segment 时失败。
5. Bundle 任一成员变化都会改变 hash。
6. Bundle 冻结后不能补来源。
7. Retry 创建新 Run，旧 Run 不变。
8. Artifact 被篡改时完整性检查失败。
9. Export 被修改一字节后验签失败。
10. SCENE-002 组合负向 fixture 失败。
11. SCENE-001 legacy fixture 可迁移但永不自动获得资格。

### 完成门 G3

- 一条本地命令可从 request 生成 verified export。
- 这是接入真实 provider 前的硬门。

---

## 阶段 4 — 自动收集引擎 v0.1

### 目标

实现“高吞吐、完整记录、可审计”的收集层，仍不产生 Claim。

### 设计

收集层必须覆盖：

- Campaign 预算；
- query 角色与血缘；
- provider 参数；
- 分页；
- 全部命中；
- 选择/拒绝原因；
- URL 规范化；
- Source 身份；
- Retrieval 状态；
- 原始 provider response；
- 页面 Artifact；
- exact dedup；
- near-duplicate 评估；
- TriageAssessment；
- OriginAssessment；
- Stop reason；
- CollectionSnapshot。

### 实现

1. 先实现“人工提供 QuerySpec + 自动执行”，不先做 LLM Query Planner。
2. 接入一个真实搜索 provider。
3. provider 原始 JSON 作为 Artifact 保存。
4. 实现分页、重试、配额与 rate-limit 处理。
5. 实现静态 HTTP fetcher：
   - redirect；
   - content type；
   - timeout；
   - max size；
   - compression limit；
   - SSRF 防护；
   - 禁止 `file://`、localhost、私网地址。
6. 实现 access kind 规范化。
7. 实现 URL canonicalization 和 content hash 去重。
8. 实现 `TriageAssessment`：
   - snippet 强制 D；
   - unauthorized_reprint 不因授权状态强制降级；
   - retention 独立裁决。
9. 实现 Campaign stop policy：
   - coverage reached；
   - budget exhausted；
   - no-new-source；
   - manual stop；
   - provider exhausted。
10. 冻结 CollectionSnapshot。

### 测试

- provider contract test 使用录制响应，不依赖实时网络。
- 分页结果全部记录，不能只存 selected hits。
- rank、query、locale、provider 参数完整。
- snippet 无论大小写、连字符和别名均判 D。
- 同一 URL 的 snippet 与 full page 是不同 Retrieval。
- redirect 前后 URL 均保留。
- raw response hash 可重算。
- 429/5xx/timeout 正确重试并保留旧 Run。
- exact duplicate 合并 Artifact，但不抹除 Retrieval 历史。
- near duplicate 只形成评估，不静默合并 Source。
- `UNKNOWN` origin 不得算独立。
- 动态页、登录墙和不可达页必须显式记录状态。
- SearchCampaign 无论成功或失败都有 stop_reason。

### 完成门 G4

- 能针对一个真实 ResearchRequest 自动产生 CollectionSnapshot。
- Snapshot 中仍不存在 Claim。

---

## 阶段 5 — 机械解析与稳定 Segment

### 目标

将抓取字节转换为可重复、可定位的结构化文本。

### 设计

- 机械解析不做事实判断。
- 派生 Artifact 必须记录父 Artifact 与 transformation build。
- v0.1 优先静态 HTML；PDF 作为同阶段次级能力或紧随其后的兼容子阶段。
- 动态浏览器渲染不进入首版，遇到时记录 `NEEDS_RENDERER`。

### 实现

1. HTML 主体抽取。
2. 标题、作者、时间、正文块提取。
3. Unicode 与空白规范化规则。
4. Segment 层级：
   - document；
   - section；
   - paragraph；
   - sentence/quote（按 profile 需要）。
5. source locator：
   - HTML selector/offset；
   - PDF page/offset。
6. parser build registry。
7. ParseRun 重跑与 supersede。
8. 输出差异工具：比较 parser build 版本造成的 Segment 变化。

### 测试

- Golden HTML：导航、广告、正文混合。
- 编码、中文标点、空白、重复段落。
- 同一 Artifact + 同一 parser build 输出完全一致。
- parser build 变化产生新 ParseRun，不覆盖旧结果。
- 离线重解析不触发 Retrieval。
- Segment hash 和 locator 可定位回原文。
- PDF fixture 覆盖页码、换行、页眉页脚。
- 解析失败不得生成半合法 ParsedDocument。
- 页面内容中的 prompt 指令只作为文本保留，不执行。

### 完成门 G5

- 至少一个真实收集页面可稳定解析为 Segment。
- Parser 的输出可被人工抽查定位回原 Artifact。

---

## 阶段 6 — EvidenceBundle 冻结与选择工具

### 目标

建立“收集结束”和“语义提取开始”之间真正不可变的边界。

### 设计

Bundle 必须包含：

- Snapshot 引用；
- 精确 Retrieval/Artifact/Document/Segment；
- 选择与拒绝理由；
- Triage/Origin assessment；
- profile version；
- policy bundle hash；
- bundle hash；
- supersedes。

### 实现

1. Bundle builder。
2. Bundle freeze command。
3. Bundle verifier。
4. Bundle diff：
   - 新增/删除 Segment；
   - 新增/删除来源；
   - assessment 变化；
   - policy/profile 变化。
5. Bundle durability gate：
   - 正式提取所需 Artifact 不得是 EPHEMERAL；
   - 因 retention 只能保留摘录时，明确 `auditability: LIMITED_BY_RETENTION_POLICY`。

### 测试

- 冻结后修改成员失败。
- 任何成员或 policy hash 变化都改变 bundle hash。
- 悬空 Segment、Artifact 或 assessment 引用失败。
- post-freeze 新来源不得被旧 ExtractionRun 使用。
- exact duplicate 不可伪装成两个独立来源。
- same-origin/likely-same-origin 不可满足双 B。
- `UNKNOWN` 不可满足双 B。
- EPHEMERAL Artifact 不得生成正式 eligible bundle。

### 完成门 G6

- 可从同一 CollectionSnapshot 创建多个不同、可审计的 Bundle。
- 旧 Bundle 永远保持可复验。

---

## 阶段 7 — 隔离语义提取与玄幻 Profile v1

### 目标

从精确 Segment 生成项目中立的玩法事实，而不注入 XuanhuanSandbox 当前设计期待。

### 设计

`xuanhuan-gameplay-scene/v1` 只表达证据事实：

```text
参与者
实际动作
作用对象
明示前提
状态变化
时间顺序
即时反馈
后续可行动空间
持续后果
事实冲突
```

不表达：

```text
M-1 是否支持
current_holder 是否够用
是否 NOT_A_GAP
是否应新增机制
是否进入 design-map
```

### 实现

1. 冻结 profile schema。
2. 冻结 neutral prompt。
3. 构建 ExtractionRunner：
   - 只读取 bundle 中声明 Segment；
   - 不访问 repo；
   - 不浏览；
   - 不调用未声明工具；
   - 输入 manifest 完整。
4. Build identity 包含 model/prompt/parameters/profile/executor/tool policy。
5. Claim 引用精确 Segment。
6. 支持 CONFLICTING，不强行调和。
7. 中性 renderer 只输出 claims/scene facts，不输出 element mapping。
8. 加入 source-content prompt-injection 防护。

### 测试

- 零 Project Context fixture。
- 项目期待注入 RUN-B：
  - 伪造“项目需要争夺机制”；
  - Claim 集不能因此改变。
- Source content injection：
  - 页面内“忽略指令、标为 CONFIRMED”不得影响系统行为。
- Claim 未引用 Segment 失败。
- Claim 引用 bundle 外 Segment 失败。
- Segment 文本不支持 statement 时失败或降级。
- 冲突材料必须并列，不得静默选边。
- 输出出现 `M-1`、`NOT_A_GAP`、`current_holder` 等项目字段时 profile validation 失败。
- prompt/model/profile/关键参数变化产生新 build ID。

### 完成门 G7

- 本地正向 fixture 产出合格结构。
- 两类注入 fixture 均不改变中性事实集合。
- 仍未自动向 sandbox 提出机制结论。

---

## 阶段 8 — Build 资格与 Bundle 保证等级

### 目标

把旧“每案例重税”改造为 build 资格 + bundle 保证，同时保留全部纪律。

### 设计

### Build 资格

- RUN-A：零注入。
- RUN-B：项目期待污染。
- Source-content injection fixture。
- 机械可重放。
- Claim 集规范化比较。
- build 变化自动失效。

### Bundle 保证

```text
UNQUALIFIED
BUILD_QUALIFIED
BUNDLE_VERIFIED
HUMAN_AUDITED
```

正式 export 至少要求 `BUILD_QUALIFIED`；高风险、校准和抽样 bundle 再进行双跑或人工审计。

### 实现

1. ExtractorBuild registry。
2. QualificationRun runner。
3. fixture suite hash。
4. normalized claim-set comparator。
5. build invalidation。
6. bundle verifier。
7. 抽样策略与高风险标记。
8. qualification/revocation audit log。

### 测试

完整迁移旧攻击测试行为：

- 假 qualified build；
- build 不在 registry；
- 空 sources/claims；
- 缺 material/input manifest；
- 占位 hash；
- hash 与文件不匹配；
- 绝对路径或目录穿越；
- RUN-A/RUN-B 同文件；
- forged run hash；
- snippet/D 伪造 CONFIRMED；
- same platform；
- alias chain；
- post-isolation source；
- SUPERSEDED 仍有 ACTIVE Claim；
- live count 假报；
- generated output 漂移。

新增：

- bundle hash 被替换；
- policy hash 缺失；
- source-content injection；
- model/prompt/profile 变化后资格失效；
- historical export 不被重写，但旧 build 不得生成新合格 export。

### 完成门 G8

- 至少一个真实 ExtractorBuild 通过完整资格套件。
- 资格结论可独立重放、验证和撤销。

---

## 阶段 9 — EvidenceExport 与 sandbox Import

### 目标

完成两个 repo 之间唯一受支持的集成路径。

### 设计

新仓输出：

```text
EvidenceExport
+ export hash
+ producer commit/builds
+ request origin
+ bundle hash
+ claims
+ neutral scene facts
+ policy hashes
+ assurance
+ artifact manifest
```

sandbox 保存：

```text
research/
├── RESEARCH-QUESTIONS.md
├── requests/
├── imports/
│   └── EXP-.../
│       ├── export.yaml
│       └── import.lock.yaml
├── mappings/
└── decisions/
```

### 实现

新仓：

1. export builder；
2. export verifier；
3. schema compatibility；
4. artifact availability report；
5. revocation record。

sandbox：

1. `ResearchRequest` exporter；
2. EvidenceExport importer；
3. `import.lock.yaml`；
4. hash/commit/schema validator；
5. mapping template；
6. design-map 链接改为稳定 export claim ID。

### 测试

- export 一字节篡改失败。
- producer commit/build 缺失失败。
- policy hash 缺失失败。
- bundle hash 不匹配失败。
- import lock 不匹配失败。
- 相同 export 重复 import 幂等。
- 新仓 export 不包含 project mapping。
- sandbox mapping 不能修改 import 原文。
- Artifact 不可用时必须显式降级 auditability。
- 旧 schema 在兼容窗口内可读，未知破坏性版本拒绝。

### 完成门 G9

- 本地 reference export 成功导入 sandbox。
- sandbox 对该 export 完成一次独立 mapping，但不反向污染新仓。

---

## 阶段 10 — 第一轮真实 Pilot

### 目标

证明系统面对真实研究问题时不会强迫产出结论。

### 建议验收请求

使用 RQ-002 的严格条件作为第一轮压力测试：

- 对象是真实物品；
- 正被具身角色握持或携带；
- 双方对同一次未结算转移施加相反控制；
- 不是拿完以后再夺回；
- 有可观察结算；
- 能记录最终状态。

### 实现

1. 从 sandbox 固定 commit 导出 ResearchRequest。
2. 自动运行 SearchCampaign。
3. 冻结 CollectionSnapshot。
4. 人工只负责选择是否建立 Bundle，不直接写事实。
5. 运行 qualified extractor。
6. 生成 export。
7. sandbox import + mapping。
8. 记录收集成本、拒绝原因、无结果原因和人工抽查。

### 测试

系统允许两种都合格的结果：

#### A. 找到合格案例

- Claim 全链可追溯；
- 来源等级和独立性满足规则；
- Bundle 无事后补证据；
- export 通过验证。

#### B. 未找到合格案例

- 输出 `NO_QUALIFYING_CASE_FOUND` CampaignReport；
- 完整保留 query、hits、拒绝理由和 stop_reason；
- 不生成伪 Claim；
- 不把邻接案例包装成命中。

额外进行：

- 人工抽查 provider 返回前若干结果；
- 重放机械解析；
- 高风险 bundle 双跑；
- 比较自动选择与人工审计差异。

### 完成门 G10

- 一个真实 ResearchRequest 完成全链。
- “没有证据”也能成为稳定、可审计的结果。
- 完成第二次独立真实 request 后，才进入旧系统切换。

---

## 阶段 11 — Legacy 迁移与旧系统退役

### 目标

保留历史，不让旧工作流继续成为并行真相源。

### 设计

- 迁移行为约束，不机械拆分旧 691 行 checker。
- 旧脚本只是参考，新的 validator 按对象责任重写。
- 历史 scene 不自动升级为新资格。

### 实现

1. SCENE-001：
   - 放入 `fixtures/legacy/scene-001`；
   - 保留原始文件与 migration report；
   - 标记永不自动追认。
2. SCENE-002：
   - 保留完整 tombstone fixture；
   - 拆成多个最小负向 fixture：
     - missing exact pack；
     - post-isolation source；
     - placeholder hash；
     - snippet mis-tier；
     - same-platform alias；
     - zero claims eligible；
     - canonical pollution；
     - forged/absolute path。
3. 迁移旧测试语义到：
   - collection validator；
   - evidence validator；
   - qualification validator；
   - export validator。
4. `generate_scene_facts` 的新 renderer 移除 `element_mapping`。
5. sandbox 的 `research/` 改为 consumer 目录。
6. 删除 active `research/scenes`、旧 checker 和 generator 主路径；Git 历史与迁移文档保留。
7. 更新 README、AGENTS、CURRENT_STATE 导航，不改变产品关卡。

### 测试

- 所有旧攻击行为在新仓都有对应测试。
- 旧有效失败不会在新系统假绿。
- design-map 历史链接有迁移映射。
- sandbox 不再直接生成 FactClaim。
- 新仓不能读取 sandbox Project Context。
- 连续两个真实 import 成功后才允许删除旧主路径。

### 完成门 G11

- 新仓成为唯一证据生产者。
- sandbox 只产生 Request、Import、Mapping、Decision。

---

## 阶段 12 — 运行硬化与 v1.0 发布

### 目标

使系统可长期使用，而不是只完成一次演示。

### 实现

1. Artifact 完整性定期扫描。
2. 正式 export 所需 Artifact 至少满足非 EPHEMERAL durability。
3. 备份/恢复流程。
4. CAS 垃圾回收：
   - 任何 Snapshot/Bundle/Export 引用对象不得删除；
   - retention 删除形成 tombstone。
5. 凭证与日志脱敏。
6. provider drift 监控。
7. parser/extractor build registry。
8. 失败重试、幂等和中断恢复。
9. 审计命令：
   - explain claim；
   - trace request；
   - verify export；
   - check artifact；
   - diff bundle。
10. 文档：
    - Quickstart；
    - Operator Guide；
    - Policy Guide；
    - Migration Guide；
    - Incident Guide。
11. 发布 `v1.0.0`，冻结 export v1 兼容承诺。

### 测试

- 从空环境恢复一份 reference export。
- 随机删除 replica 后能检测缺失。
- Artifact 篡改被发现。
- 中断 SearchRun/ParseRun 后重试不覆盖旧记录。
- provider 返回结构变化触发明确失败。
- 大批 DiscoveryHit 不破坏稳定 ID 和 hash。
- export 依赖对象均能审计。
- 全部离线 mandatory CI 通过。
- 网络 smoke test 与昂贵 qualification test 分离，不污染 PR CI 稳定性。
- 安全测试覆盖 SSRF、路径穿越、压缩炸弹、超大页面、恶意 MIME 和网页 prompt injection。
- 两个 repo 的兼容测试通过。

### 完成门 G12 / v1.0

- 全部 G0–G11 已完成。
- 至少两个真实 ResearchRequest 走完全链。
- 至少一个 qualified extractor build。
- 至少一个 EvidenceExport 被 sandbox 导入并完成项目映射。
- 旧系统退役。
- 恢复、审计、资格失效、revocation 均演练通过。
- 无未解决 P0/P1。
- 不需要 UI、分布式系统或通用平台化能力。

---

# 6. Validator 拆分

## `validate_collection`

负责：

- SearchCampaign / QuerySpec / SearchRun；
- provider raw response；
- DiscoveryHit 完整性；
- Source / Retrieval / Artifact；
- access kind；
- snippet Tier D；
- hash；
- storage；
- dedup；
- triage/origin；
- snapshot。

## `validate_evidence`

负责：

- ParseRun / Segment；
- Bundle freeze；
- Claim 引用；
- source grade；
- independent origin；
- CONFLICTING；
- live/superseded；
- profile schema。

## `validate_qualification`

负责：

- ExtractorBuild registry；
- RUN-A/RUN-B；
- input manifest；
- isolation；
- prompt injection fixture；
- reproducibility；
- invalidation；
- assurance level。

## `validate_export`

负责：

- export schema；
- export hash；
- producer commit/build；
- policy hashes；
- assurance；
- artifact manifest；
- revocation；
- sandbox lock compatibility。

---

# 7. 测试体系

## 必须进入每个 PR 的离线测试

- schema validation；
- unit tests；
- golden fixtures；
- negative fixtures；
- hash/canonicalization；
- reference integrity；
- export verify。

## 不进入普通 PR 必跑的测试

- 实时网络 provider smoke；
- 真实 LLM qualification；
- 高成本 bundle dual-run；
- 大规模性能测试。

这些应由显式 workflow 运行，并保存执行 manifest。

## Fixture 族

1. `positive/minimal-local`
2. `positive/two-independent-tier-b`
3. `negative/scene-002-full`
4. `negative/missing-artifact`
5. `negative/post-freeze-source`
6. `negative/snippet-as-b`
7. `negative/same-origin-two-platforms`
8. `negative/unknown-origin`
9. `negative/forged-hash`
10. `negative/absolute-path`
11. `adversarial/project-expectation`
12. `adversarial/source-content-prompt-injection`
13. `legacy/scene-001`
14. `legacy/scene-002-tombstone`

---

# 8. PR 与依赖顺序

## `xuanhuan-sandbox`

- **XS-01**：处理 PR #10，固定迁移基线。
- **XS-02**：新增 Request/Import 合同，但暂不删旧系统。
- **XS-03**：接入 reference export。
- **XS-04**：两次真实 import 后退役旧 active research。

## 新仓

- **NR-01**：Architecture/Data Model/State Machine/ADR，仅文档与 schema。
- **NR-02**：validators、canonical hash、fixture 骨架。
- **NR-03**：全本地端到端纵切。
- **NR-04**：ArtifactStore、CollectionSnapshot。
- **NR-05**：真实 provider 与静态 fetcher。
- **NR-06**：parser/segment。
- **NR-07**：EvidenceBundle freeze。
- **NR-08**：Xuanhuan extraction profile。
- **NR-09**：qualification/assurance。
- **NR-10**：export/verify。
- **NR-11**：legacy fixture migration。
- **NR-12**：运行硬化与 v1.0。

硬依赖：

```text
XS-01
  ↓
NR-01 → NR-02 → NR-03
                  ├→ NR-04 → NR-05 → NR-06 → NR-07
                  └→ NR-08 → NR-09
NR-07 + NR-09 → NR-10
NR-10 → XS-02/XS-03
两个真实成功 import → NR-11 + XS-04
最后 → NR-12 / v1.0
```

---

# 9. 防止项目膨胀的停止规则

v1.0 明确不做：

- UI；
- 分布式任务队列；
- 多租户；
- 插件市场；
- 通用知识图谱；
- 向量数据库；
- 多 provider 统一商业平台；
- 自动修改 design-map；
- 自动机制晋升；
- 动态浏览器集群；
- 任意领域通用 Claim 强类型；
- 把所有网页提交 Git；
- 用 LLM 处理每一个 DiscoveryHit。

任何新增基础设施必须回答：

1. 当前 Xuanhuan research 的哪个已发生失败无法由现有结构解决？
2. 能否先用本地文件、单进程和一个 provider 验证？
3. 是否改变玩家研究价值，还是只让平台更漂亮？
4. 是否会延迟第一条真实 EvidenceExport？

不能回答则延期。

---

# 10. 主要风险与对应控制

| 风险 | 控制 |
|---|---|
| 自动搜索自证循环 | 保存全部 hits、query 血缘、拒绝理由；支持 CHALLENGE/CONFLICT query role |
| 网页内容 prompt injection | parser 不执行；extractor tool-less；source injection fixture |
| 事后补来源污染旧 Claim | immutable Bundle + bundle hash |
| 来源假独立 | OriginAssessment；UNKNOWN 不计确认 |
| Artifact 丢失 | durability gate、replica status、完整性扫描、恢复演练 |
| 版权状态混入证据等级 | source tier 与 retention/access legitimacy 分离 |
| LLM 非确定性 | build identity、规范化 claim-set 比较、双跑与人工抽样 |
| provider 漂移 | 保存 raw provider response、provider build、contract smoke test |
| 旧系统双真相源 | 固定迁移基线；两次真实 import 后切换；旧系统只读退役 |
| 过度通用化 | 单 profile、单 provider、单本地 ArtifactStore、无 UI |
| sandbox 反向污染 | 只传 immutable Request；extractor 输入 manifest 禁止 Project Context |
| policy 后改导致旧结论漂移 | Export 固定全部 policy hashes |

---

# 11. 现在应立即执行的前三步

1. **先处理 PR #10 并固定 `legacy_contract_commit`。**
2. **只产出 NR-01：Architecture/Data Model/State Machine/Hash/Export RFC，不写搜索器。**
3. **RFC 通过后创建新仓，第一条实现必须是全本地可重放纵切；真实 provider 排在其后。**

最重要的执行纪律是：

> 不从旧 checker 拆代码开始，不从搜索 API 开始，也不从 LLM prompt 开始；先冻结对象和不可变边界，再用本地 fixture 证明整条链，最后才接真实网络与语义模型。
