# 观察研究入口：构建与验证方案

- 状态：**阶段 A 已开始实施；完成状态以测试、实际宿主切片及固定提交的双平台 CI 为准。阶段 B/C 尚未实施。**
- 日期：2026-09-05。
- 实施集成基线：`3372edd47666175db9f6a17bee1b8446635ce355`；纳入其 standing attestation 更新，按用户决定保留 CI 移除。
- 代码基线：`eca4c7e41b99ec8966111ff11f2ced11c4d16eaf`。
- 架构依据：[观察目标驱动的小说研究架构 v0.1](OBSERVATION_RESEARCH_ARCHITECTURE.md)。
- 已存在的未提交地理实验工作不属于本计划的改动范围；实施时保留它们，并记录实际采用的固定提交。

## 1. 阶段与交付标准

| 阶段 | 要回答的问题 | 必须交付 | 完成不意味着 |
| --- | --- | --- | --- |
| A | 已有 Profile 能否从目标自动走到正式研究交付？ | 定义/解析、作品线索、Generic Handoff、恢复与验证、台账、最小宿主 Skill | 任意新 Profile 已可自动准入；语义质量已合格 |
| B | 指定配置在声明范围内表现如何？ | 独立样本/gold/scorer/运行绑定和质量报告 | 所有小说、模型和配置均已合格 |
| C | 没有合适配置时能否受控扩展？ | Profile 草案、检查、审查准入、重新解析与 A/B 接续 | 动态插件平台或任意机制归纳 |

阶段 A 从第一版就包含宿主搜索、原生任务完成和交付。不能只验证手工拼接命令后把它称作“一句话研究”。单个研究先执行一个固定 Profile，多个作品顺序推进；通用窗口并发复用现有引擎。

后续实现沿用用户选定的任务分支，不改写已审查提交，不在最终 gate 前合并 main。契约、实现、相关文档与对抗测试在各自阶段保持一致。

## 2. 阶段 A 的最小契约集合

以下为阶段 A 的契约边界。实际输入与命令见 [操作流程](OBSERVATION_RESEARCH_WORKFLOW.md)。对象不同于多个服务；共享小型生产原语，不为每个 schema 增设运行时层。

| 拟新增契约 | 核心责任 |
| --- | --- |
| observation-definition.schema.json | 无 Profile 偏置的需求、稳定 requirement ID、局部性、未覆盖部分与作者输入声明 |
| profile-resolution.schema.json | 需求逐项适配、选定配置的多种 hash、EXACT/SUPERSET、独立质量状态 |
| observation-work-lead.schema.json | 作品相关性假设、Lead-only 来源、可选定位提示 |
| generic-handoff-build-request.schema.json | 内容绑定的 builder 输入，支撑确定性重建 |
| generic-extraction-handoff.schema.json | 定义/解析/来源/配置/完整 spec 的交接闭合 |
| generic-novel-spec.schema.json | source/rights/quality/limits/order 的严格输入白名单 |
| generic-handoff-attempt-event.schema.json | attempt 内每次 invocation 的开始/返回/恢复与前序事件引用 |
| generic-execution-receipt.schema.json | 严格区分成功、失败回执；成功绑定指定原生产物 |
| observation-research-run.schema.json | 冻结计划、预算、provenance；当前视图/最终报告的可重建内容 |
| observation-research-event.schema.json | 所有线索、来源尝试、命令调用与结果引用，保留完整分母 |

使用现有 contracts 根目录、ArtifactStore、ID 工具和 JSON Schema 约束。需要新增 ID prefix 时仅扩展对应 registry/schema；不得将这些研究记录加入 core Catalog。公共 defs 是否独立成文件按重复度决定。

source declaration、WorkRef、SourceRef、standing attestation 继续使用现有契约。旧 Scene schema 不增加 generic nullable 分支。新对象采用封闭字段集合、确定性 ID/hash 和清晰引用类型，不接受未知字段或人工修补的 hash。

## 3. A0：冻结边界与验收，不改运行时

确定并评审：

1. architecture 中每个新增对象的完整 schema、ID/hash 输入和状态转换。
2. 观察定义的无 seed 输入边界与独立作者声明；旧 neutral frame attestation 不覆盖新输出。
3. 通用研究如何复用 Intake/投影并独立记录中立预算与搜索策略；不复用场景专用 diversity 要求。
4. source spec 的严格白名单、默认值与路径解析；prepare、execute、历史回执验证的不同 I/O 边界。
5. 显式工作目录复用、共享资源锁、每次 invocation 的中断识别。
6. 两类 Profile 配置、标准正反 fixtures、台账分母和研究停止策略。

产出一组完整可校验的 schema 草案/示例与下面的验收矩阵。所有示例标明未实现，不手写假运行回执充当证据。A0 的通过只允许进入 A1，不声明产品链路已经完成。

## 4. A1：需求、配置适配与研究记录

主要职责建议落在 `observation_planning.py` 与 `observation_campaign.py`：

- 复用 phase0_planning 的 intake/neutral projection 及 phase0_common、canonical、file_io、ArtifactStore 工具。
- 封存 ObservationDefinition；校验 requirement ID、局部性分支、语义声明的 provenance。
- 封存 ProfileResolution：REUSE_EXISTING 强制绑定实际 Profile，逐项 disposition 全部必要要求；CREATE_REQUIRED / UNSUPPORTED 分支明确无可执行选择并给出未满足要求与原因。拒绝缺少配置的伪 REUSE，同时允许无配置的合法研究交付。
- 初始化不可变研究计划和预算；记录通用 WorkLead、搜索与来源尝试，生成可重建视图。
- 需求变更、Profile 变更、预算变更均生成新记录，不修改已绑定的历史输入。

初版适配判断由宿主写草案并封存，不实现通用 schema 语义匹配器。无匹配配置和跨窗口需求有完整报告出口；不能以空字符串 Profile 或未审查配置绕过。

验证重点：未知/重复/悬空 requirement、错误配置 hash、缺少 disposition、MIXED 未分解、错误确认来源、伪称继承中立隔离、SourceRef 被研究字段污染、预算重复计数与事件重放。

## 5. A2：Generic 准入与确定性交接

主要职责建议落在 `generic_handoff.py`，对现有 novel_spec/phase0_handoff 仅做必要的小型复用：

- 组合独立 generic preflight，使用生产 source/rights/quality/limits 原语。
- materialize 和封存与研究问题无关的完整 spec；规范化路径/默认值，禁止 Scene 和研究字段。
- 从 CAS 重读并验证 definition/resolution/leads/declaration，确定性分组并生成 Handoff。
- 复用 standing attestation 和既有来源身份区分；source/access 不合格时返回可记录的失败事实。
- 实现 Handoff 自身重放，包括 expected whole-spec hash 与实际配置的绑定。

主要测试：不同研究目标下投影 spec 相同；rights/quality/limits 不同则 spec 绑定改变；相同标题不同身份不合并；Lead 文本和定位提示不进入 spec；缺失 CAS、配置漂移和声明冲突被拒绝。

准备的实时来源检查与历史回放分开；完成产物的离线验证不得要求原始 TXT/网页仍存在。不要为实现离线验证而取消执行前的来源准入和原生 source-change 检查。

## 6. A3：原生执行补强与 Generic 回执

先做两项可单独审查的原生补强，再加入薄包装：

1. **工作目录锁。** 所有公开 generic mutation 入口共同取得覆盖 extraction/reduction 的锁，包括单独的 extraction/reduction 调用，固定外层 Handoff → generic work-dir → ingestion 的顺序。内部使用已持锁实现；Handoff 保持锁直到指定验证和回执发布完成。正常返回 WAITING/PARTIAL 后释放，宿主仍能回答原生任务。按共享目录串行化即可。
2. **指定 corpus 验证。** 从现有 broad validator 提取可复用的 selected loader/validator，保持 broad validation 的原行为；不能复制一套宽松的 receipt 校验。

之后在 `generic_handoff_execution.py` 实现：

- 复用 CLI 的原生 executor 构造与 agent task-root 身份算法，避免两份实现漂移。
- durable attempt + invocation-start/return 标记；pending 与 partial 保留同一 attempt。
- 真实记录失败原因和原生 checkpoint/attempt refs；运行身份漂移失败关闭。
- 匹配原生恢复条件后显式续跑中断 invocation；状态不可仅由历史上“出现过 WAITING”推断。
- 验证实际 spec、Profile、build、run、reduction、corpus 后写不可变成功回执。
- 成功再次调用时验证并返回绑定回执，不重复语义执行；缺少回执不能因目录里存在旧 corpus 而宣布成功。

阶段 A CLI 接口如下：

```text
xhnovel-pipeline seal-observation-definition <draft> --work-dir <research-root>
xhnovel-pipeline seal-profile-resolution <draft> --work-dir <research-root>
xhnovel-pipeline seal-observation-work-lead <draft> --work-dir <research-root>
xhnovel-pipeline prepare-generic-handoff <request> --work-dir <research-root>
xhnovel-pipeline execute-generic-handoff <handoff> --research-root <research-root> --executor agent-files --work-dir <native-work-dir>
xhnovel-pipeline validate-generic-handoff <handoff> --research-root <research-root>
xhnovel-pipeline validate-generic-execution <receipt> --research-root <research-root> --work-dir <native-work-dir>
xhnovel-pipeline observation-research <init|attach|record|execute|report|validate> ...
```

seal 命令只负责确定性处理，不调用模型或自动规划。execute 的 API executor 也使用同一闭合路径；CI 用可控替身验证接口，不访问真实模型端点。原生 generic CLI 的退出语义应保留，Handoff JSON 额外说明失败阶段及恢复要求。

## 7. A4：最小宿主 Skill 与交付

新增 `.agents/skills/xhnovel-observe/SKILL.md`，通过 sync_skills.py 生成 Claude mirror。现有 Scene Skills 保留适用范围。

宿主流程：

```text
读取用户目标、明确范围和已有授权
→ 复用 intake / neutral projection
→ 定义局部观察与中立预算
→ 解析已有 Profile 并冻结计划
→ 使用 seeds 制定搜索策略、自动搜索并记录 WorkLeads
→ 解析来源、声明和准入
→ prepare / validate Generic Handoff
→ execute；完成原生 generic agent-files 任务；按状态和预算续跑
→ validate 指定 execution
→ 更新研究台账、继续其余作品或按预算停止
→ 重建最终报告和原文位置索引
```

Skill 只消费原生 generic task 内的 instructions/input/schema，不能照抄 Scene 的 candidates 输出或 agent-locate 参数作为 generic 协议。回答者隔离于搜索线索、跨单元输出和 gold；不可自行切书、替换 prompt 或修补 offset。

结果报告包括：完整候选/来源/执行分母、每次处置、Profile 适配与版本、成功 corpus 与记录数、零结果、等待/部分失败/不可恢复失败、预算停止原因、未解决需求、质量状态及允许展示的来源索引。

多作品报告不合并同名实体、不归纳规则；来源受限时不分发禁止导出的摘录。台账不是证据事实来源，所有成功与计数均重读指定回执和 corpus 后派生。

## 8. A5：完整切片与发布门槛

先用 `geography-unique-v1` 做正式链路切片，再用 `race-mention-v1` 验证无地理硬编码。两者的运行配置、来源声明和质量状态独立记录。

验证分为三层，避免将某一层误称为全部通过：

| 层 | 输入与执行 | 能证明什么 |
| --- | --- | --- |
| 自动回归 | 小型授权 fixture、可控 executor、模拟搜索工具结果 | 契约、路由、完整性、状态、重放与坏例子拒绝 |
| 安装包 smoke | checkout 外的新环境，仅安装 wheel，执行完整 generic observation 交接 | 分发资产与真实安装入口可用 |
| 宿主实际研究切片 | 正式宿主搜索、合格完整来源、固定配置、原生 agent-files | 从目标到交付的实际操作链跑通；不能据此宣布语义质量合格 |

实际切片可以一部完整作品执行地理研究，种族以较小的完整授权来源补充验证。小型 fixture 不冒充商业长篇的全文证明。搜索有预算且完整记录；研究失败也应形成可审查报告，但只有至少一个合格来源完成交接、原生抽取与回执验证，才算正向链路验收通过。

## 9. 阶段 A 验收矩阵

| ID | 场景 | 预期结果 |
| --- | --- | --- |
| A-01 | 同 source/spec、snapshot、Profile、runtime/executor，不同但同配置覆盖的研究目标，共用已记录目录 | 新研究引用相同原生产物；没有新增模型调用 |
| A-02 | 更换搜索 seeds/Lead/提示或 definition | 不改变固定通用 task；中立输入隔离声明不夸大 |
| A-03 | 已绑定包的 manifest/prompt/schema 字节漂移 | 旧 Handoff 拒绝；不承诺任意包变化都改变 ExtractionBuild |
| A-04 | 固定 runtime 下只改 reduction | 原生抽取可复用，新的归约与 corpus 绑定正确 |
| A-05 | repository commit、executor 或抽取输入变化 | 按现有 build 身份处理；禁止偷偷沿用旧 attempt/cache |
| A-06 | source/rights/quality/limits/order 变更，或当前来源字节变更 | 原生身份/来源校验拒绝不匹配复用；新运行按新来源封存 |
| A-07 | 首次 agent-files 调用 | WAITING、全部原生任务可定位；不伪称失败或完成 |
| A-08 | 部分回答缺失且另有回答被拒绝 | 同时保留 pending 与 failed；补答后续跑只处理未完成单元 |
| A-09 | 原生 GenericExtractionPartial | PARTIAL_RETRYABLE、进度与原始拒绝链保留；预算控制续跑 |
| A-10 | WAITING 之后的续跑中途崩溃 | 从新 invocation-start 识别 INTERRUPTED；匹配身份后明确恢复 |
| A-11 | 更改 Profile/executor 配置或破坏 task/checkpoint/CAS 后续跑 | 失败关闭；不能包装成一般可重试 partial |
| A-12 | 两个不同 Handoff 或 direct CLI 竞争同一目录 | 全 generic 阶段互斥；不同目录允许独立执行 |
| A-13 | 同目录存在旧成功、新 pending 或多个合法归约 | receipt 只验证其指定目标；旧 PASS 不能证明当前 attempt 成功 |
| A-14 | 新进程、完整冻结 closure，原始来源已不在 | 指定成功回执可离线重放；运行时不匹配另行明确拒绝 |
| A-15 | 原生输出零观察 | SUCCEEDED + count=0；缺产物/未执行不视作零结果 |
| A-16 | 同一作品来源尝试失败后另一来源成功 | 所有来源/执行尝试都保留，不覆写失败历史 |
| A-17 | 无来源、权限/质量不合格、无 Profile、预算耗尽 | 台账与最终报告完整处置，不丢失分母 |
| A-18 | 目标只有部分适配或需要跨窗口分析 | 不能以完整 READY 执行；分解后保留原未解决需求 |
| A-19 | SUPERSET Profile 与摘录导出限制 | 报告声明原生输出范围，并按权限处理原文呈现 |
| A-20 | 完成研究后重建台账/报告，或报告计数被篡改 | 与权威回执/产物一致才通过，不依赖宿主记忆 |
| A-21 | 地理与种族同一交接路径 | 无领域硬编码；各自保持 UNQUALIFIED / UNMEASURED |
| A-22 | 原有 Scene 工作流和所有现有 regression | 原契约/行为/错误语义保持，完整测试集通过 |
| A-23 | wheel 安装后执行已有 smokes 及新增观察研究 smoke | checkout 外资产加载、两遍执行、验证和报告成立 |
| A-24 | Ubuntu 与 Windows 对同一固定 SHA 执行适用检查，可手动运行 | 两个平台均通过，才关闭跨平台 gate |

新契约测试还必须覆盖 ID/hash 伪造、父引用错绑、非法状态转换、预算重复事件、重复回执与幂等性。只有源码变化不会影响语义的预期 build-lineage 变化，应在报告中解释，不反复重开已通过阶段。

## 10. 构建、测试和 CI

原设计基线的 CI 包含 Ubuntu / Windows、Python 3.11、完整 pytest、Skill 同步、wheel 与已有四条安装 smoke。实施期间 main 的 `4ba032d` 提交移除了 CI，用户明确要求沿用这一变更。因此本阶段不重建 GitHub Actions，A-24 双平台 gate 保留未完成；本地结果不替代它。

按当前 AGENTS，Ubuntu / Windows 证据可以来自手动执行，无需恢复 GitHub Actions；记录同一固定 SHA、环境、命令和结果。审查修复及新增 crash/concurrency/budget 回归见 [修复验收记录](OBSERVATION_RESEARCH_REVIEW_FIX_VALIDATION.md)。

按改动运行一次相应检查：

```bash
python -m pytest
python scripts/sync_skills.py --check
python -m build --wheel
git diff --check
```

共享运行时、多阶段与契约变更运行完整 suite；只有局部后续修正时采用相应测试。通过后不重复扩大测试，除非有新变更、失败或未解决问题。

wheel 在 checkout 外的新临时目录和虚拟环境安装。沿用以下已有 smoke，并新增 observation research 的 Generic Handoff 切片：

- scripts/agent_files_wheel_smoke.py
- scripts/generic_extraction_wheel_smoke.py
- scripts/phase_minus1_wheel_smoke.py
- scripts/phase0_vertical_slice_wheel_smoke.py
- scripts/observation_research_wheel_smoke.py

新增 smoke 采用已有 Phase 0 的 `--fixture-root`、`--output-root`、`--require-wheel` 模式，将完整授权 fixture 复制到 output-root/inputs。脚本本身可位于 checkout，但所有 child process 的 cwd 指向 output-root；不全局 chdir，以避免 Windows 临时目录清理问题。子进程清除 PYTHONPATH 和 OPENAI_API_KEY，显式使用 UTF-8，并断言包导入路径与运行资产根都在 checkout 外。当前旧 smoke 并非全部实现这些隔离细节，不能把新要求说成既有保证。

新增 smoke 必须至少产生一个有引用的非空观察，并覆盖一个非地理 Profile；全部空回答只能证明部分路由。fixture 回答是固定验收输入，不证明实时搜索或模型语义质量。新增 contracts 根文件已被分发 glob 覆盖时验证实际 wheel 内容；若增加新目录或 Profile，显式更新分发清单并验证全部引用资产。

阶段报告记录固定 SHA、实际 CI job、测试结果、wheel hash、安装 smoke、Skill 同步结果、完整切片路径、已知限制与预期 build ID 变化。只有本地通过时标为本地验证完成，不关闭双平台 CI gate。

## 11. 阶段 B：独立质量验证协议

先冻结报告及评分输入契约，再抽取已有地理实验中可复用的确定性评分原语。现有地理实验与未提交试验保持其独立语义，不把它们直接改名为通用质量平台。

最小报告：evaluation level、target build、可选 reduction/corpus、sample manifest、gold/dispute closure、rubric/scorer、原生运行 refs、阈值、分母、指标、范围、限制与结论。默认 exact payload，允许声明字段/关系/证据诊断，不设计任意评分代码插件。

开发与 held-out 分开；修改 prompt/schema/规则之后重绑配置，旧评估不能升级新配置。没有匹配 gold 时报告 NOT_EVALUATED，不能用模型自身 COMPLETE、记录数量或 schema PASS 代替质量评价。结果展示引用匹配质量报告，原生语料声明不变。

验收：地理现有误报/漏报类型能被如实报告；零分母处理明确；held-out 和标注分歧完整；错误 build/reduction/样本引用拒绝；语料快照不被回写；至少另一个领域可复用基础报告结构。

## 12. 阶段 C：Profile 草案与准入

仅在 CREATE_REQUIRED 时启动，输入为冻结 definition、现有配置契约和执行能力说明；搜索结论不进入抽取目标或预期答案。

流程：生成受限配置草案 → loader/schema/证据策略一致性检查 → 正反 fixture 与独立审查 → 接受并提交为受信任内置资产 → 新 ProfileResolution → A 的正式研究 → B 的质量评价。

审查接受与 Git 提交是分别记录的事实。已有明确授权的审查流程可以执行准入；自动化不得自行创造授权。新配置可首先作为未验证实验运行，不能先声明经验质量合格。此阶段只扩展配置准备分支，继续不实现 Analyzer。

验收：至少一个原先无匹配的局部观察目标完成草案到原生交付；错误 schema/证据策略、未审查配置、跨窗口 schema 和配置漂移均被拒绝；原有配置与缓存规则不受无关新增配置影响，repository commit 引起的预期 build 变化另行记录。

## 13. 本方案的冻结门槛

阶段 A 实现前重点审查四条：研究对象是否进入了原生执行身份；新 Handoff 是否绕过旧来源/权限原语；共享目录与中断是否可正确恢复；报告是否只从完整台账与指定原生产物派生。

阶段 A 的产品能力必须等 schema、实现、相关回归、宿主切片与双平台 gate 逐项通过后再宣布完成。实施记录应与本设计分开，保留未通过或未执行的验收项。
