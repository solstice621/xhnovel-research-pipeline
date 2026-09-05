# 小说原文获取：实施计划与验收矩阵

- 日期：2026-09-05。
- 状态：计划及设计完成；下列实现、故障注入和真实研究验收均待执行。
- 设计依据：[SOURCE_ACQUISITION_DESIGN.md](SOURCE_ACQUISITION_DESIGN.md)。

## 1. 执行顺序与交付边界

优先级为：保全旧数据 → 真正节流及提交恢复 → 完整性/质量核验 →
封存和 ordinary Handoff → 斗破原生全书验证。其余作品按源资格分批进入。

报告 A–F 对应关系：

| 原方案 | 实施位置 | 调整 |
|---|---|---|
| A 增量落盘/浏览器定位 | S1 + 条件 S5 | 通用持久化先做；C2 按单独授权条件执行 |
| B 保真度与标题映射 | S2 | 固定独立样本；标题多值映射；差异不冒充错误 |
| C 来源候选阶梯 | S0、S4 | 先有来源资格和目录；拒绝/限流不触发自动规避式切换 |
| D 节流 | S1 | 限制每次真实请求，包含探测、重试和重定向 |
| E 宿主取书工具 | S1–S3 | 一个有限清单处理脚本；无后台服务、队列或模型调用 |
| F 作品排期 | S4、S6 | 斗破优先端到端；其他完整作品先源层；连载单独处理 |

不预先指定缺乏依据的小时/周工期。每阶段按验收结果退出，实测后估计剩余工作。
本次只提交设计文件，不把既有采集进程状态改成新实现已经开始。

## 2. 阶段工作包

### S0：基线、旧数据迁移与来源资格

实现范围：宿主审计/导入入口；不改原 .runtime 脚本和已抓原文。

1. 记录实际代码版本与脏工作树，保存旧脚本、目录清单、文件摘要及相关日志的快照。
2. 若接管现存进程，核对 PID、启动时间、脚本路径及父子关系；
   在实施取书任务的授权范围内停止已确认的旧写入者，先等待其安全退出。
   不批量终止浏览器、Python 或其他任务；无法确认身份时使用新输出目录隔离。
3. 检查 standing attestation 的原生校验和四项权限，原样复用已有授权。
4. 导入既有章节时验证 UTF-8、标题、内容和摘要，标记 LOCAL_DERIVED_IMPORT；
   缺原始响应的历史事实保持缺失，不制造 HTTP 成功日志。
5. 固定每作品/来源的预期目录、作品身份和完成范围。
   候选域名与访问结果只作报告时点线索，实施时再记录当前观测。
6. 旧资料不全不妨碍保全；不确定目录与覆盖不能成为 COMPLETE 输入。

交付：旧数据只读快照、导入报告、逐来源配置与目录草稿、未解决事实清单。

退出门槛：旧数据可逐字节对账；不存在两个来源写入同一目录；权限和范围如实标记。

### S1：有界请求与逐章事务

拟实现：scripts/source_acquisition.py 的配置检查、传输边界、单写者互斥、
attempt/raw/derived/accepted 提交和 status 重建。

1. 单活动源、单请求；删除“全书先入队”的设计。
2. 所有请求从统一节流入口发出；跨运行保留失败预算和冷却时间。
3. 所有读写都有时间/长度上限；挑战和限流进入暂停/需访问处理。
4. 每章提交后立即可恢复；未提交产物不计入完成。
5. 支持从固定目录恢复、从已有 raw 重新派生、读取有效进度。
6. 配置改变、正文改变、文件损坏分别报明原因，不覆盖旧版本。

提议的宿主命令面，仅用于明确未来接口，目前不存在：

~~~text
python scripts/source_acquisition.py inspect <config>
python scripts/source_acquisition.py import-local <config> <input>
python scripts/source_acquisition.py acquire <config>
python scripts/source_acquisition.py status <source-run>
python scripts/source_acquisition.py verify <source-run>
python scripts/source_acquisition.py seal <source-run>
python scripts/source_acquisition.py prepare <sealed-source> <existing-planning-input>
~~~

acquire 对同一配置重复调用即为断点恢复；不添加启动守护进程的命令。
inspect 只做本地配置/产物检查；网络探测是 acquire 内受限的显式记录步骤。
status 退出码 0 仅表示成功读取状态，不表示全书完成。
其他变更操作返回：0=该操作后置条件满足；2=输入/完整性失败；
4=存在缺口、暂停或预算耗尽；这些仅为宿主退出码。
原生 exit 3=WAITING_FOR_AGENT 的含义保持不变。

退出门槛：矩阵 T01–T10、T15、T18–T21 离线通过；任何崩溃点不会虚增完成数。

### S2：完整性、阅读序和保真度

实现范围：同一脚本的宿主核验功能及其离线测试。

1. 预期清单与实际提交严格对账；覆盖核验不依赖下载结果推导的 max(num)。
2. 原始来源标题/编号、逻辑章、来源阅读条目分别记录。
3. 支持标题多值映射；无法确认的一对多/多对多关系保留 UNRESOLVED。
4. 对所有章节做重复、截断/分页、乱码/UI 等异常扫描。
5. 固定独立于 Lead 的分层质量样本，双源对齐与差异统计，审查意义破坏。
6. 输出绑定同一章节清单摘要的 coverage/quality 报告。
7. 用真实 DirectoryNovelAdapter 验证最终文件名顺序及分类。

退出门槛：T11–T17、T22、T27–T29 通过；相同坏底本不能仅凭两源一致自动通过。

### S3：封存与 Phase 0 对接

实现范围：宿主 seal/prepare；不改 contracts 或核心 validators。

1. 从有效提交构建独立 sealed source，回读核验并固定 manifest。
2. 只有 coverage/quality 全 PASS 时生成普通 preparation input；
   调用前重验文件及报告，移除旧脚本无条件 COMPLETE 的使用路径。
3. 复用原生 attestation/Brief/Lead builder，不自行签 Handoff。
4. 运行 prepare-handoff、validate-handoff 与适用的 validate-planning-handoff。
5. 在 research-root/ingestion 调用 ingest-novel 预冻结，
   用实际产物路径验证 CAS 并与 sealed source 全量对账，之后才能执行语义阶段。
6. 记录真实 ingestion.status；只在未知编号警告已明确解释且无硬性问题时继续。

退出门槛：T23–T26、T30–T33 离线通过；
输入不存在宿主私有 Novel Spec 字段、Lead 污染或 hash 替代。

### S4：斗破源验收与其余作品准备

运行工作，单独保存运行记录，不在实现提交中夹带全书原文。

1. 在已通过 S0–S3 的宿主路径进行小规模来源验证，再按固定全书目录恢复获取。
2. 独立样本质量无法通过时先换合格来源；不花语义成本验证严重损坏的全书。
3. 满足覆盖与质量条件后封存斗破，准备并验证 Handoff。
4. 为其余作品保留候选和逐作品状态；依完整源可得性进入源准备/预冻结。
5. 逆天邪神若仍连载且无当前契约可接受的完整作品源，保持范围未解决。

退出门槛：斗破至少一份源的覆盖和质量报告通过；
18 条 Lead 的归属及未解决状态均未被删减。

### S5：C2 条件扩展

只在已有独立站点/操作授权可引用时实施；C1/C4 正常工作不依赖本阶段。

1. 读取批准范围，使用已允许的浏览器控制工具；不为绕过权限改走别的控制通道。
2. 以精确来源/作品定位单一标签，取消 tab index 硬编码。
3. 限制每次实际页面内请求，提取结果按章回传并使用 S1 的同一提交路径。
4. 标签丢失、重复标签、挑战、URL 改变、回传截断立即退出并记录。
5. 仅在批准范围内允许有限重开；不依赖 Blob 下载或页面全书内存。

退出门槛：T34–T37 通过；小批获取逐章有落盘证据；没有全书吞吐保证。

### S6：斗破原生全书研究与实测排期

1. 从既有 Handoff 执行 agent-files，生成完整作品的原生任务。
2. 记录真实总任务数及首批最多 24 个任务的耗时/拒绝/返工。
3. 按同一原生协议完成余下任务，恢复始终用同一命令和运行目录。
4. 不写空答案占位、不预判“没有候选”、不改变窗口/Brief/执行器来追赶排期。
5. 收集 SUCCEEDED receipt、规划与 spec hash 闭包、新进程 validate all。
6. 报告 accepted/rejected/重试窗口、原文覆盖限制、语义未验证状态和实际吞吐。
7. 基于实测决定其余作品全书研究的日历安排；不是在设计阶段预报“按周计”。

退出门槛：T38–T40 通过；斗破端到端产物完整；
其余作品分别报告源层和研究层状态。

## 3. 验收矩阵

以下是未来实现验收要求，并非本次已运行测试。
使用合成小书、固定 HTML/本地文件、假时钟和模拟 HTTP；
不要拿真实商业站压力测试重试算法。

| ID | 场景 | 必须观察到的结果 |
|---|---|---|
| T01 | 单次请求阻塞后积压 | 不存在预入队堆积；下一真实请求距上次结束不少于 gap |
| T02 | 目录、探测、重试和多跳重定向 | 都计入同一节流和请求预算；无隐式未记录请求 |
| T03 | 429，含数字/日期/非法 Retry-After | 有效值遵守；非法值用保守默认；重启不提前 |
| T04 | 200 挑战页或 403 | 无 accepted；NEEDS_ACCESS；不自动切通道 |
| T05 | 404/410、连续超时/5xx | 有限尝试、暂停/缺口可见；返回后置条件真实 |
| T06 | 总预算到期、5 分钟无提交 | STALLED/预算原因落盘；无无限子进程 |
| T07 | raw 写到一半进程中断 | 临时文件不计完成；恢复不读取其为完整章 |
| T08 | raw 已提交但正文/accepted 未提交 | 从有效 raw 恢复；不重复网络抓取有效 raw |
| T09 | accepted 后、日志前中断 | 从提交记录恢复准确进度，不重复抓取 |
| T10 | accepted 引用文件缺失或内容改变 | INTEGRITY_ERROR，不能凭文件名跳过 |
| T11 | 1646 份同一章、换标题同一正文 | 正文重复与逻辑覆盖检查阻断 |
| T12 | 文件数正确但遗漏首章/末章/中间章 | 与独立预期覆盖不符，拒绝 COMPLETE |
| T13 | 目录只展示首尾，ID 可连续猜测 | 目录覆盖保持 UNRESOLVED |
| T14 | 公告/番外插入、合法短章、拆章/合章 | 原文保留；关系逐项解释；不制造章号 |
| T15 | 旧 PID 被其他进程复用、第二写入者 | 不误杀；文件锁阻止同时写；不自动接管 |
| T16 | 标题相同或编号跨卷重置 | 多值映射；歧义不被字典覆盖 |
| T17 | 正文末页截断、缺分页、HTML 乱序 | 质量/覆盖失败或未解决，不按标题放行 |
| T18 | 同 URL 续取响应变化 | SOURCE_CHANGED；两个响应均可审计 |
| T19 | 日志末行截断或摘要丢失 | 从 accepted 重建；没有虚假进度 |
| T20 | 修改配置/提取器后原目录续跑 | 配置不匹配；新版本必须独立 source-run |
| T21 | 网络读无限流、超长内容、跨来源跳转 | 超时/大小/范围边界终止并保留原因 |
| T22 | 文件重命名影响公告位置 | 实际 DirectoryNovelAdapter 顺序与 manifest 一致 |
| T23 | 部分章节但调用 prepare | 无普通 preparation input；未声明 COMPLETE |
| T24 | seal 后任一文本或质量报告改变 | 摘要复核失败；旧 PASS 不可复用 |
| T25 | 把 manifest hash 当 spec hash | 拒绝替代；原生计算全量 loaded spec hash |
| T26 | 原生 ingest 退出 0 但 status=PARTIAL | 记录真实状态；解释未知号警告；硬性缺口不放行 |
| T27 | 变化 Lead 人名/锚点/章节猜测 | 独立质量样本计划不变；Scout brief/input 不含这些新增内容 |
| T28 | 双镜像差异明显但无可信底本 | 只报差异率；不制造错误率或正确专名 |
| T29 | 两镜像共同缺段/占位符完全相同 | 不以一致性自动 PASS；全量异常/覆盖核验仍执行 |
| T30 | 权利声明缺失/被篡改/显式权限冲突 | 原生验证失败；不重签或扩大 standing attestation |
| T31 | 符号链接逃逸、大小写冲突、保留文件名 | 不发布封存目录；文件名合法且稳定 |
| T32 | Lead hint 混入 preparation request | 中立 Brief 等值校验失败，不进入语义阶段 |
| T33 | 预冻结后进入研究或修改源 | 同一 research-root 下原生恢复；源改变须拒绝；没有手改 native checkpoint |
| T34 | 浏览器标签关闭/改变窗口顺序 | 有效章节保留；当前未提交章可重做 |
| T35 | 多个同域标签/跳到其他作品 | 报定位歧义/身份不符，不任选 tab |
| T36 | 浏览器回传 JSON 截断/下载被阻止 | 无虚假 accepted；不依赖 Blob 下载成功 |
| T37 | C2 授权缺失或请求数耗尽 | 不启动/及时停止；C1/C4 仍可按各自条件执行 |
| T38 | 原生 exit 3 | 只报告 WAITING_FOR_AGENT；不报告研究完成 |
| T39 | 拒绝答案与后续纠正 | 原生 retry_of 和原始答案保留；无旁路修复 |
| T40 | 全书接受但 CAS/闭包损坏 | validate all 拒绝；不报告端到端完成 |

## 4. 验证与提交纪律

每阶段独立提交，明确实现范围和固定 SHA；不重写已有审阅历史，不自动合并 main。
本设计文档不改变源代码、schema、Skill 或安装资产。

- S1–S3：运行新增宿主工具的相关离线测试和对接回归；每个阶段通过后不无故重跑。
- S3 原生入口使用合成完整作品验证，完整链路使用原生生成任务/答案协议。
- 若改动共享运行时、多个核心阶段或 contracts，升级为全量测试；
  若改 packaging/installed-runtime，增加 wheel 与 checkout 外 smoke。
- 首版宿主工具以当前 macOS 运维环境为目标；不声称已完成 Windows/Ubuntu 验证。
  如后续阶段明确要求跨平台完成，两平台相关测试必须分别通过并记录 SHA。
- 修改 Skill 时才运行 skill 同步与一致性检查；不得顺带改变权利声明流程。
- 每次编辑运行 git diff --check；生成原文、日志、hash 证据保留在 .runtime，排除出源码提交。

文档本次核对只验证：当前原生 API/CLI 的存在、哈希/排序边界、设计内部一致性、
本地链接及文档格式。不能据此把 T01–T40 或真实取书阶段标为通过。

## 5. 每作品交接记录

未来运行时逐作品填写，未获取的值保持 null 或明确未执行：

~~~text
work_identity:
source_run / config_digest:
standing_attestation_id:
expected_entries / accepted_entries / unresolved_entries:
last_accepted_at / pause_reason:
coverage_result / report_digest:
fidelity_result / sample_denominator / report_digest:
sealed_source_manifest_digest:
handoff_id / planning_closure:
native_ingestion_id / actual_status / order_warnings:
research_attempt / executor / native_window_count:
accepted_windows / rejected_attempts / retry_attempts:
receipt_status / validate_all_result:
source_scope_limits / semantic_assurance:
next_action:
~~~

源码实现完成、真实来源通过、原生研究完成分别有独立验收记录。
只达到其中一层时，后两层保持待执行。

## 6. 本次设计验证

本次使用合成三条目作品核对现有原生行为：阅读序文件名保留 1/null/2 的真实章号，
未编号附属材料导致 PARTIAL 时 CLI 仍退出 0；产物可通过 validate novel；
同目录原生恢复保留 ingestion ID，随后改变源文本会被恢复校验拒绝。
这支持“先预冻结并核对 CAS，再在同一 ingestion 目录原生恢复”的接口设计。

同时检查本文档中的实际 CLI 命令可被当前 argparse 解析、既有 standing attestation
有效、本地文档链接存在及新增文件 diff 格式。脚本与结果保存在
.runtime/source-acquisition-design-20260905/verify_design.py 和 validation.json，
结果绑定设计文件和相关原生代码文件的实际摘要。
这些是设计依据验证，不是 S0–S6 实现验收，也不表示 T01–T40 已执行。
