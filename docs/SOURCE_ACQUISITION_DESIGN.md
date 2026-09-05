# 小说原文获取与证据准入设计 v1

- 日期：2026-09-05。
- 状态：设计完成；实现与真实取书验收尚未执行。
- 适用范围：宿主侧、有界的整部作品获取、恢复、源核验与既有 Phase 0 对接。
- 代码核对基线：bee312300822d0d186f8e509028d542876a781fa；工作树中其他在途改动不属于本设计。
- 实施拆分与验收案例：[SOURCE_ACQUISITION_PLAN.md](SOURCE_ACQUISITION_PLAN.md)。

## 1. 目标与成功条件

把“能打开章节页面”推进到“一个来源明确、覆盖边界可说明、内容可核验、
可通过普通 Novel Spec 进入原生管线的完整源”。采集成功、结构核验通过、
原生源冻结、研究执行成功是不同结果，各自给出证据。

本设计解决原获取报告的 A–F，并补齐实际请求节流、虚假 COMPLETE、
文件顺序变化、已存文件损坏、上游改版、进程存活但无进度等缺口。
不承诺单本 1–2 小时或六本按周完成；完成时间由实测有效进度推算。

验收终点：

- 斗破：一个通过核验的完整来源，原生 Handoff 执行成功及新进程 validate all 通过。
- 其余已完结作品：先完成源准备；条件具备时单独做原生 ingestion 冻结，暂不提交语义任务。
- 报告列为连载中的逆天邪神：先核实作品状态；未能证明完整作品时不声明 COMPLETE。
- 18 条 Lead 始终保留其 LEAD_ONLY 身份；研究产物独立生成，未找到和未解决线索保留在分母。

## 2. 固定边界与本次决策

### 2.1 与现有管线的关系

遵循 [AGENTS.md](../AGENTS.md)、[PHASE0_INTERFACE.md](PHASE0_INTERFACE.md)、
[NOVEL_WORKFLOW.md](NOVEL_WORKFLOW.md) 和 [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md)。
以下设计记录只属于宿主运维，不是新 Catalog 类型或新的证据契约。

~~~mermaid
flowchart TD
    A[来源候选与取书配置] --> B[有界获取或导入本地文件]
    B --> C[原始响应与逐章持久化记录]
    C --> D[覆盖核验与保真度评估]
    D --> E[本地封存源 LOCAL_SOURCE_SEALED]
    E --> F[既有 prepare-handoff]
    F --> G[既有 planning-handoff 验证]
    G --> L[既有 ingest-novel 预冻结]
    L --> M[核对 native CAS 与本地封存集合]
    M --> H[既有 execute-handoff]
    H --> I[原生 ingestion / Bundle / SceneWindow]
    I --> J[原生 agent-files 答案与验证]
    J --> K[原生 merge / replay / validate all]
    M --> N[其余作品可在此等待研究排期]
~~~

宿主获取工具不生成 SceneWindow、提示词、SceneCandidate、合并结果或证据 span。
本地获取日志不能替代原生 CAS 和验证器。核心入口、错误时序、source-quality
classifier、FULL_WORK 及导出权利契约保持原样。

本设计不往 xhnovel 添加爬虫、搜索服务、队列、租约或工作者调度。
未来宿主工具只处理事先确定的有限来源清单；不递归发现网站、不根据 Lead 缩小范围。

### 2.2 通道决策

| 通道 | v1 决策 | 进入条件与终止条件 |
|---|---|---|
| C1 有界直接获取 | 主通道 | 明确来源与权限、允许访问的有限目录；每次实际请求受节流约束 |
| C2 浏览器自动化 | 条件扩展；默认关闭 | 必须有涵盖该站点及操作的既有单独授权记录；缺失时不启动该通道 |
| C3 对抗性规避 | 不纳入设计 | 不实现挑战破解、代理轮换、指纹伪装或限流规避 |
| C4 本地原文导入 | 完整支持 | 用户提供或正常导出的文件，同样经过身份、覆盖、质量和权限核验 |

AGENTS.md 的“Non-goals unless separately approved”包含
“browser automation for commercial novel sites”。真实 Chrome、AppleScript
或把脚本放在宿主目录都不自动构成例外；已有明确授权可直接引用，不重复索要。
此次“计划并完成设计”不被解释为批准新增浏览器采集或启动六本全量研究。
C2 未就绪不阻碍 C1/C4 和其余设计实施。

不再将 HTTP 000 称为服务器状态码；它记录为未取得 HTTP 响应的客户端结果。
连接超时记为观测事实，“IP 封锁”保留为待证实原因，不据此自动换代理或换域续冲。

### 2.3 权利与作品范围

使用当前工作根中已有 standing operator attestation，先调用现有验证函数。
新工作根如需副本，逐字节复制，保留相同 ID/hash；不重新签署、不扩大 scope。
草稿 SourceDeclaration 默认省略 rights，由原生 prepare-handoff 注入并绑定。
显式 rights 必须与 attestation 完全一致。

存储前确认非 UNKNOWN basis 和 may_store_full_text；材料进入宿主模型或
生成 agent-files 前另确认 may_send_to_external_model；导出单独遵守 may_export_excerpts。
operator 研究声明不是出版社授权证明；不改变 UNKNOWN / UNOFFICIAL_COPY 的诚实含义。

v1 不定义“截至某日的连载快照等于完整作品”。如作品尚未完结，材料可按已有权限
保存为不完整研究输入，但不进入 COMPLETE / FULL_WORK 证据研究。支持连载快照
需单独修改并验证核心契约，不通过 edition_label 或自造书名暗中绕过。

## 3. 实现落点与文件组织

拟新增一个宿主脚本 scripts/source_acquisition.py，首版保留在仓库源码中审查，
不打入 wheel、不增加产品 CLI 子命令。站点提取规则是每来源显式配置；
提取、请求和校验逻辑保持小而可测试，不建立插件运行时。
旧 .runtime 脚本仅作为迁移输入，不成为长期源码真相。

以下是拟议布局；目前未创建这些执行产物：

~~~text
.runtime/<campaign>/
  operator-attestation.json          # 既有声明，字节不变
  acquisitions/<work>/<source-run>/
    config.json                     # 本次固定配置
    expected-catalog.json           # 本来源预期条目、阅读序及覆盖依据
    catalog-evidence/                # 目录/完结依据，来源角色如实标记
    raw/<entry>/<attempt>.bin        # 获取到的响应实体字节或导入原文件
    attempts/<entry>/<attempt>.json  # 每次尝试的不可变结果
    chapters/<entry>.txt             # 单一来源的确定性正文派生物
    accepted/<entry>.json            # 最后写入的逐章持久化提交记录
    journal.jsonl                   # 可恢复的运维事件索引，不作完成真相
    status.json                     # 可重建摘要，不作完成真相
    quality/                        # 固定样本、对齐、差异和语义可用性检查
    coverage-report.json
    source-manifest.json
  prepared-sources/<manifest-digest>/
    chapters/                       # 仅放将交给 native adapter 的文本
    provenance/                     # 原始字节、派生说明及核验材料
    source-manifest.json
  phase0/<work>/                     # 原生 prepare-handoff 产物
  research/<work>/ingestion/         # 原生 ingest-novel 预冻结与后续自然恢复
  research/<work>/                   # 语义研究只由原生 execute-handoff 管理
~~~

每来源一次独立 source-run。不能把另一镜像的缺页补进同一来源，
也不共享不加来源限定的章节文件名。切换源创建新配置和新 manifest；
旧来源保留为失败、部分获取或未选用来源。

## 4. 宿主记录与哈希边界

以下字段是待实现的文件格式设计，尚不是产品 schema。
实现时拒绝未知字段、无版本记录和矛盾配置；不添加核心对象 ID 前缀。
正文、raw response、任务内容均按不可信数据处理。

| 文件 | 必需信息 |
|---|---|
| config | format_version；作品身份；source_run；候选来源及来源版本；通道；已有授权引用；attestation 引用；固定目录输入；提取器版本；节流/限制参数 |
| expected-catalog | 目录依据及摘要；有序 entry_key；预期 locator；原目录标题；entry→logical_chapter 的有序分页映射；分卷/分章关系；正文与附属材料的覆盖规则；完结依据与未解决项 |
| attempt | entry_key；attempt 序号；config/目录摘要；requested/final URL；实际通道；起止时间；HTTP 状态或 null；响应实体字节摘要/长度；超时、挑战或提取失败原因 |
| accepted | entry_key；阅读序；所用 attempt 引用；原题；原编号或 null；派生正文相对路径/长度/摘要；raw 摘要；提取器摘要；提交时间 |
| coverage-report | 绑定的配置、目录和有序章节 manifest 摘要；逐项 PASS/FAIL/UNRESOLVED；依据引用；覆盖限制；核验器版本 |
| quality report | 绑定的 manifest 摘要；抽样计划；对齐版本；已比对分母与未对齐数；结构差异、字符差异及语义可用性结论 |
| source-manifest | format_version；作品/来源身份；有序输入章节路径/字节长度/摘要；entry→输入章节映射；raw→derived 关系；覆盖/质量报告摘要；封存时间 |

哈希规则：

1. 文件摘要为 SHA-256(确切文件字节)，统一写为 sha256: 后接小写十六进制。
2. 对结构化子对象取摘要时复用 canonical_dumps；不复制核心 canonicalization 实现。
3. manifest 不含自己的 hash 字段；先固定内容，再对保存的完整文件字节求摘要。
4. coverage 与 quality 绑定“有序章节清单摘要”，最终 source-manifest 再引用报告，
   避免报告和最终 manifest 互相引用产生循环。
5. 时间是记录生成时的真实时间；不把全部产物回填为同一虚构 frozen_at。
6. 原生输入 spec hash 只由原生加载/验证路径计算；不能用宿主 manifest hash 替代。

只保留需要审计的响应元数据，不记录 Cookie、Authorization 或整个浏览器会话。
raw 指响应实体字节而非 TLS/HTTP 线上的完整传输字节；解压/解码步骤明确记录。
已有本地文本没有原始 HTTP 响应时，标记 LOCAL_DERIVED_IMPORT，不能伪造响应收据。

## 5. 请求节流、有限重试与退出

### 5.1 首版运行约束

一个宿主进程、一个活动来源、一个在途请求；不预先把全书任务塞入线程池。
不实现多书自动并行。每次请求都按 expected-catalog 显式取下一未提交条目。
原子发布复用现有 file_io 原语；本设计所需父目录同步由宿主显式补充并实测，
不能把现有原语未提供的平台崩溃保证写成已具备。
进程级单写者互斥只防并发写盘，不是调度器、租约或自动接管服务。

拟议默认值是保守工程起点，不是站点承诺的可接受速率：

| 参数 | 默认设计 |
|---|---|
| concurrency | 1 |
| min_gap_seconds | 5；从上次响应/失败结束到下一请求开始计算 |
| slow_start | 首 20 次请求 gap=10 秒；之后才允许使用配置的 5 秒下限 |
| request_timeout_seconds | 30 |
| max_response_bytes | 2,000,000，按流读取时限制；超限停止该条目 |
| max_attempts_per_entry | 3，跨重启累计，不因进程重启清零 |
| max_run_seconds | 1800，到期在持久化边界退出，可显式续跑 |
| consecutive_transport_failures | 连续 3 次则暂停当前来源 |
| no_commit_seconds | 300，无新提交时标记 STALLED 并退出当前运行 |

所有目录读取、探测、正文请求、重试和重定向跳转都经过同一节流入口。
停止时间从实际执行开始计算，不能只约束探测循环而放任子进程无期限运行。
截止时间即将到达时不启动无法在剩余时间内完成的请求。

### 5.2 请求状态处理

- 成功响应仍须验证 final URL、媒体类型、章节身份与正文，不能把 200 当作有效正文。
- 网络失败、超时、5xx：第 k 次重试前等待 min(30×2^(k−1),120) 秒；
  默认总共最多 3 次尝试，即最多两次重试；每次重试仍遵守 gap；
  若失败后最近成功响应延迟持续升高，只加大 gap，不在本轮自动加速。
- 429 或明确限流提示：结束活动取书，持久化 Retry-After 和暂停原因。
  后续运行最早时间必须晚于该值；缺失或非法时默认至少冷却 15 分钟。
- 401/403/挑战页：记为 NEEDS_ACCESS，不在循环里反复试探、不自动启用 C2。
- 404/410：记为 MISSING；有限核实后保持缺口，不无限重试、不自动删除目录条目。
- 3xx：只允许已声明来源边界内的跳转，有限跳数；跳转仍计入请求预算。
  指向其他来源或改变作品身份则停止并重新解析来源声明。
- 提取失败、标题错配、乱码、空正文：隔离响应并停止该条目；
  不以返回长度大于 200 字作为唯一通过条件。

暂停状态和 retry_not_before 跨重启保留。重启只能重置进程资源，
不能抹掉重试预算、冷却或拒绝访问结果。
跨域可能共享限流/出口，故不假设“不同域互不影响”。

### 5.3 传输复用边界

优先消费正常取得的完整本地 TXT/EPUB，或使用已有 bounded static-site 能力可表达的源。
宿主 C1 补充只用于当前明确目录上的有界取书；复用已有 URL/来源校验与哈希原语。
现有 HttpFetcher 接口不暴露 Retry-After，且内部可能直接跟随重定向：
不能声称简单包一层 sleep 已覆盖所有请求。实现阶段必须用宿主传输边界取得每跳
状态/必要响应头，关闭隐式重试及重定向，再逐跳调用受限入口。
不顺带修改共享 HttpFetcher 的返回类型、错误码或网络代理行为。

## 6. 增量落盘与恢复

以 accepted/<entry>.json 的有效提交闭包计数，不以 PID、页面变量、
普通文件存在或日志行数计数。状态摘要每次都可从已提交记录重建。

逐章提交顺序：

1. 在同一来源工作目录内写 raw 临时文件；流式长度检查，flush/fsync 后原子改名。
2. 将本次 attempt 记录原子持久化；失败响应也保留，不冒充 accepted。
3. 用固定提取器派生正文；写临时文件，验证编码、身份和摘要后原子改名。
4. 最后原子写入 accepted 记录；只有其引用的所有文件和摘要都能回读验证才算完成。
5. 更新可重建 journal/status。日志写失败不撤销已有有效逐章提交。

所有临时文件与目标位于同一文件系统；平台支持时同步父目录。
一个来源只有一个写入者；C2 与 C1 不能同时往同一 source-run 写。
运行进程持有操作系统文件锁，进程结束自动释放；PID 文件只用于诊断。
不根据旧 PID 单独判断进程身份或自动发送 kill。

恢复规则：

- accepted 存在且闭包有效：跳过该条目。
- 只有临时文件：忽略并隔离，不计完成；保留能读出的失败信息。
- attempt/raw 有效、accepted 缺失：可从本地 raw 重新派生；重新生成 accepted。
- accepted 引用缺失/损坏：报 INTEGRITY_ERROR，保留旧记录，显式修复后才能续跑。
- 日志尾部损坏：隔离尾部，依 accepted 重建摘要，不跳过真正的缺章。
- 同 entry 新响应摘要不同：记 SOURCE_CHANGED；保留两个版本，不覆盖已接受正文。
- 配置/提取器/目录摘要变化：新 source-run；可以导入原 raw 重算，不隐式改写旧来源。
- 已封存目录不再作为抓取输出目录，采集进程不持有其写入路径。

C2 如有单独授权，按精确来源/作品 URL 和会话标识定位标签；多个匹配标签时报歧义。
定位失效或挑战时立即停止；允许范围内的自动重开必须有限次数并重新确认身份。
每章通过结构化 JSON 回传给宿主后立即提交，页面只缓存当前章。
40 章仅可作为调度批大小，不再作为唯一落盘单位，也不依赖 Blob 自动下载。
持久化完成上限损失为当前一个未提交获取条目（通常是一章，分页源为一页）；
不得把未确认回传记为完成。

## 7. 文本派生与稳定阅读序

优先保存原始 HTML/导入文件，另存明确标注的正文派生物。
提取器绑定源码摘要，使用确定性 DOM 解析，不以贪婪正则截到最后一个 div。
选择正文、去除明确的 UI 节点、实体解码和换行处理都必须有固定规则与样例。
不修正专名、不补词、不跨镜像拼正文、不排序正文段落来“猜测正确原文”。
若站点依赖脚本还原阅读顺序，原始 HTML 文本顺序未经核验就不能通过质量检查。

源冻结对象将是明确声明的派生文本；其 span 证明针对该文本的引用，
raw→derived 关系由宿主 provenance 保留，不能宣称核心 span 直接指向远端 HTML。

最终目录文件名形式：

~~~text
000001__原始章节标题.txt
000002__原始章节标题.txt
000003__原始附属材料标题.txt
~~~

一章多页先依据已核实的来源分页关系按页序派生为一个输入章节；
所有页都提交后才可组装，固定分隔换行并记录每页的派生边界和重复页眉移除规则。
任何一页缺失都不输出完整章；从本地 raw 重做组装不能触发未授权网络访问。
不同镜像之间不做此组装；来源真正的分章/合章只按其明确编排保留。

前缀是导出的输入章节阅读序，绝不伪装成原作章节号。保留原题于正文及 manifest；
文件名做最小跨平台字符清理并限制字节长度，发生冲突时用稳定 entry 摘要后缀。
禁止绝对路径、路径穿越、符号链接逃逸、大小写折叠冲突和平台保留文件名。
不在下载目录就地批量 rename，避免中断后断点身份失效。

DirectoryNovelAdapter 按文件名自然排序，并从 stem 提取标题/章号；
固定宽度阅读序保证附属材料不会因标题排序漂移。非编号标题带 __ 前缀，
避免被纯数字文件名误识别为正文第 N 章。
导出后用实际 DirectoryNovelAdapter.discover 核对每个 ordinal、title、
declared_number 和 chapter_kind，不另写一套原生章号分类器。

## 8. 完整性与来源覆盖核验

“抓到了全部预期 URL”只是传输覆盖；“这些条目构成整部作品”还需要覆盖依据。
不以站点页面 ID 连续、目录尾号、百科章数、结局标题或已下载 max(num) 单独证明。
百科和开放网页只作目录核查线索；它们不自动成为核心章节事实证据。

### 8.1 固定预期目录

获取全书前固定 expected-catalog。每条包含稳定 entry_key、阅读序、locator、
原题、正文/分卷/附属材料关联，以及该预期信息来自哪里。
只有首尾预览和 ID 猜测时，目录状态为 UNRESOLVED；可以有界探测，
但“1..1646 全有文件”不能让目录状态自动通过。

正文、公告、番外、感言和一章多页必须逐项解释：
一个正文逻辑章节可映射多个阅读条目，标题映射是多值关系而不是标题字典覆盖。
对重名、拆章、合章、缺号、重复号和分卷重置记录具体依据；
无法解释的差异是 UNRESOLVED，不通过重编号掩盖。
被声明为整部作品组成部分的材料保留；不能为消除 native 警告删除。

### 8.2 必过检查

| 核验项 | 通过条件 | 典型拒绝 |
|---|---|---|
| 作品身份 | 目录、文本与既有作品身份一致 | 同名异书、跳转到其他书 |
| 作品范围 | 完结依据与组成范围明确 | 连载快照冒充全书 |
| 目录覆盖 | 预期条目集与 accepted 集完全相同，无未解释条目 | 凭最终文件总数通过 |
| 单章闭包 | 每条 raw/derived/attempt 摘要和长度一致 | 空文件、损坏正文、缺收据 |
| 章节关系 | 首章、末章、分卷/拆合关系及中间覆盖均有依据 | 只从已见最大章号推算完整性 |
| 内容重复 | 逐章正文重复/近似重复均已核实 | 1646 文件复制同一章 |
| 文本截断 | 无未解决分页、未闭合正文、末页截断或挑战污染 | 结局标题正确但正文不全 |
| 阅读序 | 导出后原生适配器发现顺序与预期完全一致 | rename 后按标题重排 |
| 质量检查 | 第 9 节无阻断项 | 人物/行为关键内容严重缺损 |

短章、相同段落和重复标题首先是异常信号，不是一刀切的失败依据；
需可引用的解释，不能自动以“长度阈值以下”判为残缺。

coverage-report 每项为 PASS / FAIL / UNRESOLVED。只有全部 PASS 才允许宿主
生成 COMPLETE 声明；FAIL/UNRESOLVED 不生成可执行 preparation-input。
该检查约束本宿主工具，核心目前验证的是声明，并不独立证明整书完整；
它不是新增的普遍强制核心 gate，也不能声称其他人直接调用 CLI 必然受此检查。

## 9. 保真度、对齐与研究可用性

### 9.1 固定抽样

抽样函数只接收已固定来源目录，不接收 Lead 定位信息，即使宿主此前已见过 Lead，
也按相同确定性规则生成质量样本，不据此声称宿主拥有全新的盲审上下文：
默认取开头、中部、结尾各一章，再按全书逻辑章节阅读序五个等长区间各取两个不同章节，
合计最多 13 个不同正文章节；小作品全取。
区间内以 SHA-256(canonical_dumps([目录摘要, 区间号, logical_chapter_key])) 排序选取，
去重后按同一排序补足各区间名额，章节不足时全取；计划持久化后不可改。

线索锚点可另列为定点排查样本，但不进入随机/分层样本统计，
不作为召回 gold，不改变 source selection 主比较分母，不回流到 Scout 输入。
新发现普遍异常时新建追加样本计划，保留初始结果及扩样原因。

### 9.2 对齐和度量

标题归一化仅用于匹配建议，原题不改。相同标题保留所有候选；
确认映射需邻章、阅读序、文本首尾等依据。拆/合章用显式有序多对多映射。
无法唯一对齐的章节记 UNALIGNED，不强制匹配。

对已确认同一章的双镜像，以固定规则仅统一 Unicode NFC、行尾和空白用于比较；
不做繁简转换、同义替换或专名纠错。原始文本始终不改。
计算字符编辑距离 d 和 d/max(len(a),len(b))；记录长度、分母、
对齐失败数和比较器版本。该值称“差异率”，不是“错误率”。
实体变体只有在独立参照能确定对应专名时才计已知变体比例；
无可靠参照时记无法判定，不从 Lead 人名建立纠错字典。

所有章节另做轻量全量异常扫描：解码替代符、异常 UI/挑战残留、
大面积占位符、正文长度突变及重复正文。扫描只是触发排查，不改正文。
两站可能共享同一坏底本，文本一致不能单独证明正确。

### 9.3 准入与选源

质量结果与官方/非官方 tier 分开记录：

- PASS：固定样本和全量异常检查未见未解决的意义破坏；注明抽样限制。
- FAIL：确认乱序、缺段或扰动已改变人物、行为、否定、关系等研究所需信息。
- UNRESOLVED：差异无法判明、同章对齐失败或缺可靠比对依据，不能先宣称合格。

所有未解决的样本异常都需检查，不用任意差异率阈值自动批准。
同一来源的新样本暴露系统性破坏时撤销该版本的“可用于研究”状态，
保留旧核验报告并重新选源，不修补已冻结文本。

候选选择顺序：身份和完整性通过 → 质量无阻断 → 来源可追溯程度 →
可持续获取情况。速度最后考虑。无法区分时如实记录并固定选定来源；
每个被研究来源独立声明、独立全书执行。镜像比对不生成新混合版本。
edition_label 放简短限制说明和宿主报告标识，完整指标放宿主审计文件；
原生 validators 不承担验证 edition_label 中的统计结论。

## 10. 封存、准备与原生执行

### 10.1 本地封存

在独立 staging 目录复制有效章节、raw 和 provenance，逐字节回读验证后
固定 manifest，再发布到 prepared-sources/<manifest-digest>，目录名仅使用摘要的
64 位十六进制部分，不含 sha256: 前缀。
不使用指向活动下载目录的符号链接或可共享修改的硬链接。
文件在准备 Handoff 前及原生 ingestion 前再次核验；文件改变就新建版本。
源目录只含输入章节，报告和说明文件放目录之外，避免被 adapter 当作章节。

LOCAL_SOURCE_SEALED 仅表示本地版本闭包，不表示已进入核心 CAS。
prepare-handoff 预检不获取或冻结全部源字节，也不证明 Lead 命中。

### 10.2 生成 ordinary preparation input

宿主生成器只能从已验证 manifest、coverage/quality 报告及已封存路径生成草稿，
不接受自由传入 COMPLETE 开关。每次生成前重验全部文件与报告绑定。
输入只含既有 brief、leads、source_declaration、requested_at 四项。
不向 Novel Spec 添加宿主采集字段。

沿用已封存中立 Brief；Lead、人物、锚点和质量对照笔记均不进入 discovery_brief。
已有 standing attestation 复制到目标 Phase 0 工作根后，由 builder 验证。
按 builder 产物取得 Handoff/Novel Spec 路径，不猜测文件名或自行计算核心 ID。

所有以下命令均为当前已有 CLI；尖括号表示取自原生产物的具体路径：

~~~bash
xhnovel-pipeline prepare-handoff <preparation-input.json> --work-dir <phase0-root>
xhnovel-pipeline validate-handoff <handoff.json> --phase0-root <phase0-root>
xhnovel-pipeline validate-planning-handoff <planning-compilation-receipt.json> <handoff.json> --planning-root <planning-root> --phase0-root <phase0-root>
~~~

### 10.3 原生预冻结与源层停止点

所有准备进行研究的作品先使用 builder 生成的同一 Novel Spec，
在后续 research-root 的 ingestion 子目录运行原生入口：

~~~bash
xhnovel-pipeline ingest-novel <generated-novel-spec.json> --work-dir <research-root>/ingestion
xhnovel-pipeline validate novel <emitted-ingestion-catalog.json> --store <research-root>/ingestion/objects
~~~

不改写 spec，不创建 Scene Scout 任务；这不是 Handoff 执行成功。
记录 ingestion 的真实 ID、spec hash、适配器发现清单和 CAS 验证结果。
在任何语义任务生成前，将 native CAS 的输入文件集合/摘要与宿主封存集合全量对账。
其余作品可停在此处；斗破继续 execute-handoff，使用同一 research-root 和同一 spec。
run_novel_research 本来就在该 ingestion 子目录调用原生 ingestion，
由其自行恢复、验证 checkpoint 与当前源，不拷贝/伪造 checkpoint 或手塞 Catalog。
这利用现有 CLI 与原生恢复契约，不新增 Handoff 执行状态或冻结内部对象的写入入口。
源变化、spec 或代码变化导致原生恢复拒绝时停止；重新准备版本，而不删掉错误继续。
单独在其他目录做过的预冻结不能当作此处检查已完成。

这样核验与执行之间的源变更仍由原生 resume 检测，不能仅靠“执行前检查过一次文件”。
ingest-novel 在 PARTIAL 时可能退出 0；必须读 NovelIngestionRun 和 order_validation。
只存在未知编号、且无缺号/逆序/重复、所有条目都与宿主覆盖依据一致时，
可记“原生冻结完成，ingestion=PARTIAL，未知编号警告已解释”，不能改写为 SUCCEEDED。
出现硬性排序问题、丢失章节或重复正文则不可进入本设计的研究阶段。
不为了消除可解释番外警告伪造章号。

### 10.4 斗破端到端

~~~bash
xhnovel-pipeline execute-handoff <handoff.json> --executor agent-files --work-dir <research-root>
~~~

第一次 exit 3 / WAITING_FOR_AGENT 只表示原生任务已生成。
按照既有 agent-files Skill 回答任务，再重复同一命令；
只在已有 FAILED / INTERRUPTED 终态后按原生要求使用 --retry，保留原尝试。
不新增模型调用、自动换 executor 或宿主工作者调度。

最终读取原生输出路径并执行：

~~~bash
xhnovel-pipeline validate all <emitted-research-catalog.json> --store <native-objects-dir>
~~~

研究完成必须同时具备：SUCCEEDED Handoff receipt、规划闭包（适用时）、
全书原生窗口全部接受、新进程 validate all 通过，以及完整的源/产物审计链。
核对 Handoff.expected_input_spec_hash 等于对应 ingestion.input_spec_hash；
全量核对原生 adapter 冻结的输入文件集合/摘要与本地封存集合，不能只比较 spec hash。
原生 core CAS 不自动包含宿主 raw/provenance，后者另行保留并明确证据边界。
SceneCandidate 仍为 DRAFT/UNVERIFIED；执行成功不代表语义解读已独立判真。

## 11. 进度、排期与停止规则

每作品独立报告以下字段，不能压成一个 COMPLETE：

| 维度 | 记录内容 |
|---|---|
| acquisition | 预期条目数、有效提交数、缺口、最近有效提交时间、失败和暂停原因 |
| coverage | PASS / FAIL / UNRESOLVED 及报告摘要 |
| fidelity | PASS / FAIL / UNRESOLVED、样本分母、限制 |
| local_seal | 是否封存及 manifest 摘要 |
| handoff | 未准备 / READY_FOR_XHNOVEL / 预检失败 |
| native_freeze | ingestion ID、真实 status、排序警告和验证结果 |
| research | 未执行 / WAITING_FOR_AGENT / 进行中 / 失败 / 原生执行成功 |
| semantic_assurance | DRAFT/UNVERIFIED；是否有独立复核，不能由 validate all 推断 |

斗破优先端到端；其他已完结作品先到源准备/原生预冻结。
逆天邪神先处理作品完成范围，不与已确认完整作品共用可执行资格。
某来源暂停后可以处理另一作品的本地核验；不自动跨域绕开暂停原因继续请求。

采集估时使用固定记录的最近窗口内“有效提交数/实际墙钟时间”，包含失败与冷却。
样本不足、长期无提交或源被暂停时 ETA=null；单列纯传输理论下限。

语义估时取原生全书窗口生成后的真实总数 N。
先按原生阅读序完成首批最多 24 个任务；N<24 时全取。
此批只估执行成本，不是召回/精度样本，不改变全书任务范围。
记录首试耗时、返工、拒绝原因、原生验证耗时、实际活动并发和宿主中断时间。
首批有代表性限制；完成约 10% 后更新估计，不把两个阶段窗口吞吐当作独立重复试验。
工作时间估计为剩余窗口/实测有效吞吐，日历排期另计可投入时段；未知 token/cost 保持 null。

协议、源、Brief、profile 或构建变化时新建研究运行，不混合不同版本的吞吐或质量结论。
失败、限流、完整性/质量 UNRESOLVED、预算到期均写明可恢复动作；
不以静默无限守望或后台进程存活代替状态报告。

## 12. 设计完成标准与剩余工作

本设计已确定状态语义、请求/恢复算法、记录和哈希边界、完整性与质量门槛、
原生对接方式、六本先后顺序及分阶段验收。条件分支都有确定行为。
当前来源是否完整、C2 是否已有站点级授权、连载状态和实际吞吐是运行时待核实事实，
不是靠设计文档填成 PASS 的事项。

实施前不必重新讨论 A–F 优先级；按配套计划的 S0→S4 推进核心路径，
S5 条件通道与 S6 全量研究分别记录实际完成情况。
若实施发现现有核心契约无法表达真实来源，保持原输入与失败证据，
另提最小合同变更，不能以宿主兼容后门掩盖。
