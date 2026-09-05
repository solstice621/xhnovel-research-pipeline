# 研究请求中的自动原文获取

本流程由宿主代理执行，供 `xhnovel-explore` 和 `xhnovel-observe` 共用。
用户提出完整研究需求后，宿主连续推进：需求冻结 → 作品发现 → 找源与获取 →
覆盖和质量核验 → 封存 → 对应原生 Handoff → 原生冻结和语义执行 → 报告。
用户明确只要规划、线索或源准备时，以该范围为终点。

宿主负责准备文件、运行命令和完成原生任务；不要求用户默认提供全文、编写配置
或在每阶段回复“继续”。这里的自动是宿主 Skill 的执行行为；产品 CLI 仍负责
确定性步骤，不会因调用一次 CLI 而启动搜索代理或后台下载服务。

## 1. 开始条件与文件归属

先完成所属研究 Skill 的中立需求冻结及预算，之后才开始搜索。保留真实的
作品身份、全部 Lead/WorkLead 及其未解决状态，不从章节猜测缩小全书范围。

读取 [工具使用说明](SOURCE_ACQUISITION_USAGE.md)，在当前研究目录下建立独立
来源配置、目录、依据和 source-run。宿主从实际资料生成这些文件；配置引用的
path/sha256 是本地文件摘要，核心 artifact ID 仍只能由原生工具产生。

新研究根目录缺少 standing attestation 时，从仓库
`attestations/operator-attestation.json` 原样复制。已有声明保持不变并经原生
验证；不要为此次运行重签。已存在的权限无需再次确认。声明缺失、无效或显式
权限冲突时保留原因，向用户提出具体缺失项；不能自行补上授权或扩大范围。

场景研究保存 Phase 0 审计记录。观察研究先通过
`observation-research attach` 保存有界来源输入，再记录 `SOURCE_STARTED`；
其 `source_input_artifact_id` 指向真实附加材料，不假造 SourceDeclaration。
获取、导入和恢复均在这个来源尝试之下。暂停后的同一配置保留原事件；
结束该尝试或开始不同来源版本时记录实际 disposition，遵守 campaign 预算。

## 2. 宿主找源、获取和审查

1. 检查当前研究已经绑定的完整本地材料、封存源和有效断点，避免重复取书。
   缺少本地文件时，宿主继续查找来源与可核对的目录，不立即转交给用户。
2. 从实际目录、书目和来源内容确定作品身份、阅读条目、逻辑章及补充材料。
   主站章数、页面 ID 连续或某个搜索摘要只能作为线索，不能直接证明全书完整。
   未解决项可保持 UNRESOLVED 并进行有界获取；不得用实际已下载文件反推完整目录。
3. 为选择的来源编写工具配置。已有 TXT/EPUB/章节目录走 C4；符合现有授权和
   工具范围的有限 URL 清单走 C1。调用 `inspect`，随后 `import-local` 或 `acquire`。
   工具负责真实请求节流、原始材料、逐章提交和状态恢复。它不搜索无限目录，
   不自动轮换来源；宿主也不能用新配置重置同一来源的限流和失败预算。
4. 根据退出码和状态继续。成功获取全部条目后仍需 `verify`；PARTIAL 时核对
   明确的缺口，在同一配置下按已落盘预算恢复。403、挑战或 `NEEDS_ACCESS` 不触发
   代理切换或隐式 C2。当前 C2 尚未实现；独立浏览器权限记录本身不能使其可用。
5. 宿主运行 `review-template` 并实际检查固定样本、异常及目录关系。需要时获取
   有界、独立的比较材料，使用显式标题对齐和 `compare`，保留歧义。质量审查由
   当前宿主完成，不默认要求用户逐章批准；没有依据的项保持 UNRESOLVED。
   不把模板批量填 PASS，不修补冻结正文或从 Lead 人名选择质量样本。
6. `verify --review` 全部通过后执行 `seal`，记录其返回的摘要命名目录 S。
   已封存源也须用当前兼容实现回放再接入。脚本版本不兼容时保留旧版本执行，
   或把原始材料作为明确新版本重新导入；不能修改旧 binding 来强行恢复。

缺口、无法证明的完整性或保真度不是“获取成功”。来源需要访问处理、当前
运行预算耗尽或材料无法核实时，保留可恢复状态和具体缺失事实。宿主继续研究
范围及剩余预算允许的其他作品；不同来源的选择必须有独立来源依据和准入判断，
不能用来规避已观察到的访问拒绝或限流。全部可行路径用尽后再报告阻塞及所需
用户动作，不循环重试、不静默放大预算。连载作品不能用当前已更新章节冒充全书。

## 3. 场景研究分支

宿主创建使用说明中的 planning-input：引用已冻结 Brief、完整兼容 Lead 列表，
以及适用的 Phase -1 receipt/root。不要根据来源重新编写 discovery_brief。
设 P 为该来源的 Phase 0 准备目录，W 为后续原生研究目录：

```bash
python scripts/source_acquisition.py prepare S scene-planning-input.json --phase0-root P
python scripts/source_acquisition.py freeze S HANDOFF --phase0-root P --research-root W
xhnovel-pipeline execute-handoff HANDOFF --executor agent-files --work-dir W
```

HANDOFF 使用 `prepare` 的真实返回路径；同一准备输入恢复时保留原时间。
prepare 调用原生 builder、Handoff 回放和适用的 planning closure，freeze 调用
原生 ingestion 并对账 CAS。无需再手填 COMPLETE 或运行另一套 preparation input。

宿主在收到 WAITING_FOR_AGENT 后读取原生任务、写答案、按同一命令继续，最终验证
receipt/spec hash/`validate all`。FAILED 后遵守原生 `--retry` 规则；保留拒绝答案。
来源冻结和 task materialization 都不是研究终点。

## 4. 观察研究分支

保留已有 Definition、ProfileResolution、全部对应 WorkLead 和 campaign；
不把观察需求转为 Scene discovery_brief。R 是 campaign/CAS 根目录，W 是此来源与
Profile 的原生执行目录。宿主准备如下文件，ID 均来自 R 的真实原生产物：

```json
{
  "format_version": "source-acquisition-v1",
  "definition_artifact_id": "<returned-definition-artifact-id>",
  "resolution_artifact_id": "<returned-resolution-artifact-id>",
  "work_lead_artifact_ids": ["<returned-work-lead-artifact-id>"],
  "requested_at": "<actual-frozen-UTC-timestamp>"
}
```

占位符由宿主替换。请求时间首次确定后保留，同一来源/输入可幂等恢复。
不接受自填 source、rights、COMPLETE、Profile、章节范围或语义指令。

```bash
python scripts/source_acquisition.py prepare-generic S observation-planning-input.json --research-root R
python scripts/source_acquisition.py freeze-generic S HANDOFF --research-root R --work-dir W
```

prepare-generic 回放封存来源，原样复用 attestation，并调用已有
`prepare_generic_handoff_from_input` 与 `resolve_generic_handoff`；核心输出仍是
普通 GenericExtractionHandoff 和严格 source-only Novel Spec。它不建立新 campaign。
freeze-generic 在 W/ingestion 调用与场景路径相同的原生 ingestion/CAS 对账，
保留真实原生状态；没有额外 generic 任务生成器。

两个命令成功后，以返回的 `handoff_artifact_id` 为当前 SOURCE_STARTED 记录
`SOURCE_FINISHED(status=ELIGIBLE)`，然后继续已有 campaign：

```bash
xhnovel-pipeline observation-research execute RUN HANDOFF --research-root R --work-dir W --executor agent-files
```

若准备或冻结失败，记录对应来源失败状态、错误及材料，Handoff 引用保持 null，
不可仅因之前曾产生 Handoff 就启动语义执行。仍有缺口通常是 UNRESOLVED，
确认的质量失败是 INELIGIBLE_QUALITY，权限阻断是 BLOCKED_BY_RIGHTS；保留具体原因。

随后按观察 Skill 完成 generic 原生任务。WAITING_FOR_AGENT / PARTIAL_RETRYABLE
后使用相同命令和配置继续；中断使用适用的 `--resume`，新失败尝试才用 `--retry`，
它们分别消耗既有 campaign 预算。结果、source dispositions、STOP 和 report 都由
现有 campaign 记录，不新增队列或执行状态系统。

## 5. 完成与用户交付

宿主只有在用户要求的研究范围已完成、用户要求的有限阶段已完成，或存在实际
阻塞/预算停止条件时结束。无需因“工具已准备好”“文件已下载”或“任务已生成”
向用户再次询问是否执行下一步。

最终报告分别列出获取、覆盖/质量、源冻结、原生研究和语义保证状态，保留未完成
作品与失败来源。自动推进不证明分析正确；Scene 输出仍是 DRAFT/UNVERIFIED，
观察 Stage A 仍是 UNQUALIFIED/coverage UNMEASURED。
