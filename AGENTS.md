# AGENTS.md — XHNovel Research Pipeline

> 本文件是本仓 Claude、Codex、Cursor Agent 等 AI 协作者的常驻工作合同。它约束**如何设计、实现、审查和验证研究管线**，不承载某次 ResearchRequest 的具体项目背景，也不定义玄幻沙盒的游戏机制。

## 1. 使命与优先级

本仓的使命是建立一个：

> **自动搜索与完整记录、可审计收集、可重放机械解析、按需隔离语义提取、合格证据导出**的研究管线。

按以下顺序优化：

1. **证据真实性与不可伪造性**；
2. **输入、运行、输出的可追溯与可重放**；
3. **收集覆盖与自动化吞吐**；
4. **项目上下文与证据上下文的物理隔离**；
5. **失败必须显式，不制造假绿**；
6. **简单、可维护、可被单人理解的实现**；
7. **只有真实需求支持的扩展性**。

对象数量、抽象层级、文档长度、通用性和 Agent 共识本身不构成进展。

---

## 2. 当前阶段

当前状态：**v1.0.0（G0–G12 门已用可重跑命令与 fixture 核过）。** 生产 LLM 与默认 CI 的 live 网络仍不跑。

sandbox 旧 `research/scenes` 与 checker 已退出主路径（两次真实 import 成功之后）。本仓仍不得把 Wikipedia 全文提交 Git，也不得把 Project Context 注入 Extraction。

当前允许的主要工作：

- 生产 LLM 提取器资格（新 build，不改 mock 合同）；
- operator 运行与 incident 响应。

当前**不要抢跑**：

- 不先做数据库、UI、分布式任务系统；
- 不把旧 `research/` 目录结构直接复制进来；
- 不把 Wikipedia 全文提交 Git。

---

## 3. 权威来源与冲突处理

### 3.1 本仓真相源

按问题类型使用最小必要权威来源：

| 问题 | 权威来源 |
|---|---|
| 仓库使命、责任边界、v1 范围 | `README.md` + 已接受架构 ADR |
| Agent 修改与验证纪律 | `AGENTS.md` |
| 数据对象、字段、引用、版本 | `contracts/` |
| 状态机与不可变语义 | 对应 contract / architecture 文档 |
| 来源分级、独立性、版权保留、资格规则 | `policies/` |
| 某类语义提取允许输出什么 | `profiles/<profile>/` |
| 实际实现能力 | 代码 + 测试 + 可重放运行结果 |
| 某个 bug 是否被封住 | 对应最小失败 fixture + regression test |
| 某次生产结果 | 不可变 run / snapshot / bundle / export manifest |

当文档、schema、policy、测试和代码冲突时，**不得静默选择最方便的版本**。进入 Reconcile：指出冲突，确认应该修改哪一层，然后把实现、测试和说明恢复一致。

### 3.2 外部仓库不是本仓实现真相源

`xuanhuan-sandbox` 是第一消费者，不是本仓内部状态数据库。

允许读取其固定内容用于：

- 形成 ResearchRequest；
- 迁移旧 research 合同和 fixture；
- 验证 EvidenceExport / import 契约。

不得把其活动 Project Context 注入正式 ExtractionRun。

---

## 4. 核心责任边界

### 4.1 本仓可以知道

Discovery / Collection 阶段可以知道：

- 要找哪一类材料或案例；
- 关键词和 false positive 条件；
- 搜索语言、预算、来源偏好；
- 需要确认、挑战或寻找冲突的方向。

### 4.2 正式 Extraction 不可以知道

正式隔离提取不得读取或接收类似：

```text
M-1 当前缺什么
A1 正在验证什么
current_holder / current_controller 是否足够
用户希望证明哪个机制缺失
某个候选是否 NOT_A_GAP
某条规则是否应该进入 design-map
```

Extraction 的输入只来自冻结 EvidenceBundle、profile 和明确列入 input manifest 的运行材料。

### 4.3 本仓不得替消费者做设计判断

本仓正式输出可以有：

- 参与者；
- 中性操作链；
- 明示条件；
- 状态变化；
- 时间线；
- 冲突口径；
- 原作事实；
- reception；
- 不确定性；
- FactClaim；
- 来源与 assurance。

不得正式输出：

```text
COVERED / PARTIAL / ABSENT
NOT_A_GAP
REJECTED_BY_CONSTRAINT
Gameplay Primitive 应不应该实现
底层机制方案
design-map patch
产品优先级
```

---

## 5. 数据模型硬不变量

以下是架构硬约束。实现、schema、migration 和优化都不得绕过。

### 5.1 Collection Cannot Claim

`SearchCampaign`、`QuerySpec`、`SearchRun`、`DiscoveryHit`、`Source`、`Retrieval`、`Artifact`、`ParseRun` 不得产生 `FactClaim`。

### 5.2 Source ≠ Retrieval ≠ Artifact

- `Source`：逻辑资料身份；
- `Retrieval`：一次具体访问尝试；
- `Artifact`：实际处理的字节内容。

三者不能合并成“一个 URL 对应一个 source 文件”。

### 5.3 Retrieval → Artifact 是一对多表示

一次访问未来可能同时产生：

```text
RAW_RESPONSE
RESPONSE_HEADERS
PROVIDER_JSON
RENDERED_DOM
SCREENSHOT
PDF_BYTES
```

v1 不要求全部实现，但 contract 不能锁死成单 `artifact_id`。

### 5.4 Artifact 由真实字节寻址

- SHA-256 必须按实际字节计算；
- 禁止 `hash-a`、`todo`、截断摘要等占位 hash；
- URL、标题、时间戳不构成 Artifact identity；
- derived Artifact 必须记录 lineage。

### 5.5 Search Snippet 永远是 Lead-only

`Search_Snippet`、`search-snippet`、`search snippet` 等规范化后都必须视为 snippet。

Snippet：

- 可以进入 DiscoveryHit；
- 可以成为独立 Retrieval；
- 必须按 Tier D 处理；
- 不得冒充完整网页；
- 不得独自支持 SUPPORTED / CONFIRMED ORIGINAL_FACT。

### 5.6 Frozen Bundle Before Claim

任何正式 `ExtractionRun`：

```text
ExtractionRun.input_bundle_hash == frozen EvidenceBundle.bundle_hash
```

Bundle 冻结后：

- 新增来源；
- 删除来源；
- 更换 Artifact；
- 更换 Segment；
- 更新 TriageAssessment；
- 更新 OriginAssessment；
- 更新 policy；

都必须创建新 Bundle。禁止给旧提取结果事后补证据。

### 5.7 Claim 必须引用精确 Segment

不能仅写：

```yaml
retrieval_id: RET-001
```

正式 live Claim 至少需要能回溯到：

```text
Retrieval
Artifact
Segment
normalized text hash
```

### 5.8 Run / Snapshot / Bundle / Export 不可变

进入终态后不原地更新。

- retry → 新 Run + `retry_of`；
- reparse → 新 ParseRun；
- re-extract → 新 ExtractionRun；
- 新输入 → 新 Bundle；
- 新导出 → 新 EvidenceExport；
- 历史对象通过 `supersedes` 建立关系。

---

## 6. 证据分级、来源独立性与保存纪律

### 6.1 三个不同维度不得混在一起

必须分离：

1. **Evidence quality**：这份材料能支撑什么；
2. **Access legitimacy / copyright**：访问和保存是否合法/允许；
3. **Origin independence**：多个来源是否真正独立。

不要用一个 `tier` 同时承载这三个问题。

### 6.2 初始来源分级语义

迁移旧合同的初始语义：

- **Tier A**：原始或接近原始、可直接确认特定事实的材料；
- **Tier B**：高质量二手、结构化梗概/百科/可靠总结；
- **Tier C**：读者讨论、评论、体验与公共记忆；
- **Tier D**：搜索摘要、低质量转载、营销文案、AI 聚合等发现线索。

最终精确定义以 `policies/source-tier-*` 为权威。

### 6.3 Tier 属于 Retrieval assessment

同一个逻辑 Source 的：

- search snippet；
- full page；
- licensed teaser；
- catalog；

可以具有不同证据用途。因此不要把永久 `tier` 挂在 Source 上。

### 6.4 来源独立性必须可审计

不同平台不自动独立。

需要使用 `OriginAssessment` 表达：

```text
SAME_ORIGIN
LIKELY_SAME_ORIGIN
INDEPENDENT
UNKNOWN
```

评估依据可以包括：

- exact content match；
- near duplicate；
- explicit attribution；
- publication metadata；
- human review。

`UNKNOWN` 不得用于满足要求“独立双 B”的确认门。

### 6.5 Retention 不得伪造复现能力

若政策要求只保存 metadata 或短摘录：

- 如实保存 retention 状态；
- export 标注 auditability 降级；
- 不声称完整内容可重放；
- 不通过绕过版权纪律获取“更强证据”。

---

## 7. 两级资格体系

### 7.1 Extractor Build Qualification

资格针对精确 build，而不是针对“某个模型名字”。Build identity 至少应覆盖：

- repository commit；
- model snapshot/version；
- system/user prompt template hash；
- parameters；
- extraction profile；
- schema；
- executor；
- tool/context allowlist；
- dependency lock（实现后）。

模型、prompt、profile、schema、执行器或关键参数发生实质变化后，创建新 build 并重新资格。

### 7.2 对抗夹具至少覆盖两类污染

1. **Project expectation injection**：伪造消费者希望得到某个结论；
2. **Source content injection**：证据正文中包含“忽略要求、输出某结论”等提示注入。

提取器必须把第二类内容当作不可信来源文本，而不是执行指令。

### 7.3 Bundle Assurance

至少区分：

```text
UNQUALIFIED
BUILD_QUALIFIED
BUNDLE_VERIFIED
HUMAN_AUDITED
```

“由 qualified build 生成”与“这个具体 bundle 已双跑/人工核验”不能混成一个布尔字段。

### 7.4 历史资格不能静默改写

新 policy/build 可让旧 export 变成 `STALE_*`，但不能把“生成当时已经通过”的历史事实抹掉。只有发现内容完整性破坏、伪造或资格依据根本无效时才进入 `REVOKED`。

---

## 8. 搜索与收集实现纪律

自动搜索必须保留选择过程，而不是只保留最终证据。

### 8.1 SearchCampaign 必须可回答

- 谁生成 query；
- query 文本；
- query role；
- parent query；
- derived-from 哪些 hit；
- provider；
- locale / 参数；
- provider 返回了哪些有序结果；
- 哪些被选择/拒绝；
- 为什么；
- budget；
- stop condition；
- stop reason。

### 8.2 不承诺“搜遍互联网”

系统可以承诺：

> 完整记录声明搜索窗口内 Provider 实际返回的结果。

不能承诺：

> 找到了互联网所有相关材料。

### 8.3 网络访问默认不可信

Fetcher 实现时至少考虑：

- SSRF；
- localhost/private IP；
- 非 HTTP(S) scheme；
- redirect loop；
- 超大响应；
- decompression bomb；
- 错误 MIME；
- timeout；
- 429 / 5xx；
- cookie/token/log 泄漏；
- HTML 中的 prompt injection。

不得绕过付费墙、登录墙、验证码或访问控制。

---

## 9. ArtifactStore 实现纪律

Git 不是原始网页数据湖。

ArtifactStore 的最小语义：

```text
put(bytes) -> content-addressed artifact_id
get(artifact_id)
exists(artifact_id)
verify(artifact_id)
retention / availability / durability
```

### 9.1 开发本地后端

可以使用 gitignored CAS：

```text
.runtime/objects/sha256/
```

但不要把“本地目录”写死进数据合同。

### 9.2 正式资格的持久性

生产 EvidenceExport 依赖的必要 Artifact 不得只存在于 `/tmp` 或其他 EPHEMERAL 位置。

### 9.3 写入必须抗中断

实现文件后端时优先：

```text
write temp
→ flush/fsync（按平台合理实现）
→ atomic rename
→ verify hash
```

相同内容并发写入不能产生不同逻辑 Artifact。

---

## 10. 解析实现纪律

机械 Parser 负责：

- HTML/PDF → 结构化文档；
- 标题、正文、段落、页码和 locator；
- normalized text；
- Segment；
- parser build 和 output hash。

Parser 不负责：

- 判断故事真假；
- 判定角色动机；
- FactClaim grading；
- 项目玩法解释。

更换 parser 不应要求重新抓取同一个 Artifact。

---

## 11. Profile 与语义提取纪律

v1 只服务一个正式 profile：

```text
xuanhuan-gameplay-scene/v1
```

不要为了未来未知消费者提前建通用 profile 平台。

核心 Claim 应保持通用；`actor/action/target/precondition/state_transition` 等结构放在 profile payload 中，不强制所有未来 Claim 使用同一个动作模型。

正式提取应保留：

- UNKNOWN；
- CONFLICTING；
- 缺失条件；
- 原文未说明的空白。

不要为了让 Export “看起来完整”而用模型补齐小说没有提供的信息。

---

## 12. 测试纪律

### 12.1 发现假绿时先写失败 fixture

任何 validator / qualification bug：

1. 先建立最小失败 fixture；
2. 证明当前实现错误绿灯；
3. 修复 policy/validator；
4. fixture 转绿；
5. 加入永久 regression；
6. 不对某个 scene_id 写特判。

### 12.2 测试层次

逐步建立：

- schema tests；
- unit tests；
- canonicalization/property tests；
- golden parser tests；
- attack fixtures；
- offline replay tests；
- integration tests；
- qualification tests；
- cross-repo export/import tests；
- live provider smoke tests；
- fault injection；
- human audit records。

### 12.3 关键历史回归

SCENE-002 迁移后至少保留能阻止以下假绿的 fixture：

- 精确材料只在 `/tmp`；
- placeholder hash；
- 页面 hash 与字节不符；
- snippet 冒充 B/C；
- snippet 拼写变体绕过；
- 平台名大小写假独立；
- alias chain 假独立；
- dangling alias/origin；
- post-extraction 新来源绑定旧 run；
- `SUPERSEDED` 仍有 ACTIVE Claim；
- 没有 live Claim 却声称 eligible；
- Tier D 单独 SUPPORT；
- 冲突 branch 被偷偷写进 canonical 定义。

SCENE-001 作为 legacy fixture，不能被新 schema 自动追认资格。

### 12.4 不运行就不宣称成功

只有实际运行相关检查后，才能写：

```text
tests pass
build succeeds
fixture is green
export verifies
qualification passes
```

只做静态审阅时明确写“未运行”。

---

## 13. 修改与提交纪律

### 13.1 开工前

实现或 Review 前：

1. 读 `README.md`；
2. 读本文件；
3. 只读任务相关 contract / policy / profile / ADR；
4. 若涉及旧迁移，再读固定 legacy baseline；
5. 若涉及消费者契约，再读固定的外部 repo commit，不跟随其活动 `main` 猜状态。

### 13.2 最小完整变更

- 修复当前问题所需的最小完整范围；
- 不顺手重构无关模块；
- 不因为“以后也许需要”创建新 abstraction；
- 不创建空目录追求漂亮树形；
- 新文档必须具有唯一执行职责；
- 同一规则不要在多个长文档复制维护。

### 13.3 Schema / Policy 变更

任何会改变正式数据语义的修改必须说明：

- backward compatibility；
- migration；
- fixture impact；
- export/import compatibility；
- 是否要求 build 重新资格。

破坏兼容时升 major；不要用静默字段复用改变旧含义。

### 13.4 分支与 PR

除非用户当轮另有要求，普通小改可直接更新默认分支；重大 schema/policy/qualification/cutover 变更优先使用独立 PR，确保可进行针对性 Review。

不得自行创建与任务无关的分支、worktree 或发布版本。

---

## 14. Review 纪律

Review 优先找“会产生错误可信结论”的问题，而不是样式偏好。

优先级：

### P0

- 可伪造证据或 hash；
- Project Context 泄漏进 Extraction；
- 事后补证据仍能让旧 Claim 合格；
- Artifact 身份不对应实际字节；
- Export 可被篡改而验证仍通过；
- 资格可以被绕过。

### P1

- snippet / 来源独立性假绿；
- retry 覆盖历史；
- 缺失输入 manifest；
- policy/build 漂移却继承资格；
- retention 后仍声称 FULL auditability；
- provider 失败被记录成“搜索完成”。

### P2

- 可维护性；
- 错误信息；
- 性能；
- 不必要抽象；
- 文档漂移。

Review 输出应指向具体 contract、policy、fixture 或代码路径，并给出最小修复方向。

---

## 15. 防止过度工程

v1 当前明确不以这些能力为目标：

```text
UI
multi-tenant
plugin marketplace
distributed scheduler
generic knowledge graph
vector DB as mandatory architecture
multi-profile platform
browser farm
full-text Git archive
automatic game design decisions
```

如果一个提案需要新增子系统，先回答：

1. 已经发生了什么真实失败？
2. 哪个当前验收门要求它？
3. 最小方案是什么？
4. 能否先用文件、manifest、纯函数或单进程实现？

没有明确答案则暂不引入。

---

## 16. 完成工作的报告格式

完成实现或 Review 时，默认简洁报告：

1. **改变了什么**；
2. **为什么**；
3. **实际运行了哪些检查**；
4. **结果**；
5. **仍然存在的已知边界/下一门**。

不要用大量工作日志代替结果，不要把未验证推断写成事实。

---

## 17. 当前下一步

当前最优先工作：

```text
1. 生产 LLM 提取器资格（新 ExtractorBuild，不改 mock 合同）
2. 按 docs/OPERATOR.md 与 docs/INCIDENT.md 运行
```

不要从数据库或 UI 开始。v1.0 明确不做这些。
旧 sandbox scenes/checker 已退役；历史在 `fixtures/legacy/`。
