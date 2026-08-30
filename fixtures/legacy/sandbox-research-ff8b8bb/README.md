# research/ — 玄幻桥段玩法逆向工程工作流合同 v0.2

status: 合同 v0.2（2026-08-29 晚，依据用户当日第二次裁决把分析器从叙事因果转向玩法因果；同日第三次裁决：不对盗版网站做任何分层限制。v0.1 的其余证据侧纪律与断言资格保留）
authority: 本目录只是 WEB_SOURCED 案例的**证据后端**；案例登记、断言挂接、收敛扫描与晋升唯一以 `design-map/` 为账本。本合同不修改 GDD/DEC，不阻塞也不推进 M-1；由它产生的设计候选默认停在 CANDIDATE，晋升只由用户裁决。
workflow_status: **PILOT**（Evidence workflow 尚未通过 §7 资格门；当前所有产物一律标 PILOT，不计收敛阈值，资格通过后不自动追认）

## 1. 定位（v0.2 转向）

一个由 XuanhuanSandbox 设计目标驱动的**玩法逆向工程**闭环：研究玄幻小说桥段，但主要挖玩家动词、世界状态变化与组合空间，而不是复述故事或建模人物心理。

```text
玄幻桥段 → 玩家目的 → 玩家操作链 → 世界状态变化 → 新的行动机会
→ 可重组玩法结构 → 底层机制候选 →（经 design-map 收敛）设计裁决 → 下一轮研究
```

第一目标函数是**复刻玩家亲自"做事情"的体验**：玩家想做什么、能通过哪些具体手段作用于世界、世界状态如何变化、基于变化还能继续做什么。有限认知、NPC 反应与社会后果仍在研究范围内，但作为辅助维度（见 §4）。

它是设计研究基础设施，不是游戏运行时系统；"正式游戏运行时不依赖 LLM"的方向不受影响。映射结论承认"现有规则已足够"是合格产出，不是失败。

## 2. 核心分析模型

每个桥段首先拆成这条链，而不是拆成"故事发生了什么"：

```text
Intent → Action → Target → Preconditions → State Transition
→ Feedback → New Possibilities → Persistence
```

即：玩家目的 → 可采取的操作 → 操作对象 → 成立条件 → 世界状态变化 → 即时反馈 → 新的可用操作 → 长期后果。

关键纪律：小说把桥段总结成"杀人夺宝""扮猪吃虎"这类四字标签，这些标签对本项目几乎没有信息量。要逆向的是标签底下实际发生的操作序列、每步的成立条件与状态变化。分析器观察的是桥段里**实际出现了哪些玩家式操作结构**，不是预先设计好的"标准解法"。

## 3. 优先级原则

分析中遇到任何要素，先问：

> 它是否改变玩家能够采取的行动、行动的条件、行动的结果，或此后的机会？

答案为否则优先级降低。示例：`NPC 内心非常震惊` 若只是描写，玩法价值低；若震惊导致 NPC 暂停攻击、玩家获得逃跑窗口、NPC 主动提出交易或不敢继续阻挡，它才进入玩法分析。

## 4. NPC 在本工作流中的地位

NPC 是玩家交互空间的一部分，不是桥段分析的中心。研究其四种地位：

1. **玩家操作的对象**：攻击、治疗、欺骗、交易、跟踪、推开、束缚、下毒、偷取、给予、威胁；
2. **世界中的主动障碍**：抵抗、阻止、逃跑、反击、拒绝、重新夺回；
3. **信息与资源来源**：询问、交易、偷听、观察、合作；
4. **玩家行为的反馈源**：例如偷窃被发现后前来阻止。

只研究到"这会怎样改变玩家下一步能做什么"。不分析人格数值如何从愤怒 0.63 更新到 0.82，除非该差异确实改变玩法。

**边界（不得混淆）**：本节只调整**研究优先级**，不修改 AGENTS.md §4 的项目不变量——"NPC 是拥有独立目标、认知、承诺和生活的社会主体，可以拒绝、还价、欺骗、离开、反制或报复"仍然有效。地位 2（抵抗、反制、重新夺回）正是该不变量的玩法表达；分析中不得因为"NPC 不重要"而把 NPC 写成无自主的机关或环境常量。

## 5. 两套上下文与隔离（硬约束，v0.1 保留）

- **Evidence Context**：回答"资料实际说了什么"。只允许来源材料（原文、官方资料、百科、剧情梗概、书评、读者讨论）。
- **Project Context**：回答"这为什么对项目有意义"。来自 CURRENT_STATE、GDD/DEC、design-map、开放问题。

Project Context **可以**影响：选题、搜索方向、案例价值评估、分析问题、规则映射、反例选择。
Project Context **不得**影响：原作事件提取、人物真实行为、原作因果关系、原作中的信息状态。

执行要求：

- SceneFacts 提取必须由**独立子 Agent** 完成，其调用上下文中物理上不含任何项目背景（零注入；"尽量少看"不合规）；
- 提取阶段不回应任何项目问题、不作跨案例综合判断（v0.1 夹具遗留改进项，v0.2 起强制）；提取的提问方向可以是"每个角色实际做了什么动作、作用于什么对象、材料是否说明成立条件、发生了什么状态变化"——这属于中性事实重建，不构成项目上下文注入；
- 每次研究先按需派生 `context-snapshot.md`（小上下文包，标注派生时间与来源文件），不整仓注入；
- 顺序不可倒置：先有 SceneFacts，Project Context 才允许进入分析。

## 6. 流水线（四步，下一轮由人工触发）

0. **选题（Research Selection）**：从 `RESEARCH-QUESTIONS.md` 取一个问题，声明研究模式：EXPLORE（未覆盖的新玩家操作空间）／DEEPEN（已有单例，找结构不同的案例）／CONFIRM（已有两个异质案例，找第三个）／CHALLENGE（专找反例，防自证循环）。
1. **搜证（Web Discovery → Source Triage → Scene Discovery）**：渐进收敛搜索；按 §8 分级；确认存在一个**具体桥段**并写 scene 卡（作品／章节定位／参与者／局面／关键操作／结果）。
2. **隔离提取（Evidence）**：建 `evidence.yaml`，由隔离子 Agent 产出 `scene-facts.md`，重点是**操作链与状态变化的事实重建**。
3. **分析映射（Analysis）**：注入 context-snapshot，按 §7 模板产出 `analysis.md`、`elements-mapping.md`（按 §10 格式）、`report.md`；并在 `design-map/inbox.md` 追加 WEB_SOURCED 案例条目。

跨案例聚合在 v0.2 仍不自动化：沿用 design-map 收敛扫描，由用户加保真抽查驱动；Aggregator 类判断只能作为提案写入报告。

## 7. 桥段分析主视图（十段模板）

`analysis.md` 按此结构组织：

1. **玩家想干什么**——一句话；
2. **玩家实际上做了什么**——按时间顺序列操作；
3. **每一步作用于什么对象**——人物／身体／物品／环境／关系／信息／活动；
4. **为什么这个操作能够成立**——硬条件；
5. **它改变了什么**——具体状态变化；
6. **改变以后新出现了什么选择**——极重要；
7. **哪些步骤只是小说作者安排**——例如敌人恰好没有防备、宝物恰好无人看守、救兵恰好出现；
8. **如果换一种做法呢**——反事实；
9. **哪些操作能与其他桥段重组**——Gameplay Primitive Candidate；
10. **XuanhuanSandbox 当前支持多少**——COVERED / PARTIAL / ABSENT。

## 8. 来源分级（v0.1 保留；2026-08-29 取消盗版站分层限制）

| Tier | 定义 | 允许用途 |
|---|---|---|
| A | 原始或接近原始：原作章节正文或与之实质等同的全文、官方连载页、官方设定资料。**不因网站是否获得授权而降级**；未授权转载站上的原作正文可以是 A | 事件、行为、台词、真实因果的直接确认 |
| B | 高质量二手：官方/结构化 Wiki、可靠剧情梗概、高质量章节总结 | 定位与辅助验证；相互独立的 Tier B 交叉一致可确认原作事件 |
| C | 读者分析：书评、论坛、知乎、贴吧、Reddit、博客 | 公共记忆、体验接受度；不得单独决定原作事实 |
| D | 搜索摘要、营销号、短视频文案、AI 生成拆书、低保真转述 | 仅作发现线索，不作任何结构断言的唯一证据 |

`access_kind: search_snippet`（搜索摘录／搜索摘要）的 retrieval **一律 Tier D**，不得因为平台是百科、Wiki 或问答而升为 B/C。同一 URL 若既有全文抓取又有搜索摘录，必须拆成独立 `retrieval_id`，分别记录 `access_kind` / `tier` / `hash`；FactClaim 必须引用具体 retrieval，不得只写合并后的 `source_id`。

**不对盗版网站做任何分层限制**（2026-08-29 用户裁决）。分级看文本与原作的接近程度和页面类型，不看站点授权。`unauthorized_reprint` 不得因未授权而被强制为 D，也不得禁止为 A。校验器只对 `search_snippet` / `search_excerpt` 强制 Tier D；`unauthorized_reprint` 与 `catalog_page` 没有 kind 级分层上限。目录页按内容本身分级（官方目录常为 B，垃圾目录常为 D），不是盗版门。

同一百科平台的不同词条不构成完全独立来源，跨平台一致才算交叉。

现实预期：中文玄幻正版全文常在付费墙内；未授权全文站只要检索到的是原作正文，就可以按 A 使用。授权译本的**公开预览句**可以对那几句本身计 A，但不能把未读的付费全文算进 CONFIRMED，也不得在无新隔离提取时写成 FactClaim。仓库仍只存 §13 规定的短摘录与转述，不把 `research/` 做成文本库。

## 9. 断言分类与收敛资格（转写自 2026-08-29 第一次用户裁决，v0.2 增加玩法侧分层）

### 9.1 事实断言（FactClaim，由隔离提取产出）

类型：**ORIGINAL_FACT**（原作事件、角色行为、操作、状态变化、因果、信息状态）／**RECEPTION**（公共记忆、读者体验、被视为经典的原因）。

分级：**CONFIRMED**（ORIGINAL_FACT 需 Tier A 直接支撑，或 ≥2 个相互独立的 Tier B 交叉一致；RECEPTION 可由多个独立 Tier C 一致支撑）／**SUPPORTED**（单一 Tier B；RECEPTION 单一 Tier C）／**INFERRED**（由已确认事实推断，或仅 Tier C/D 转述）／**UNKNOWN**／**CONFLICTING**（如实并列，不调和）。合法的 `ACTIVE` + `ORIGINAL_FACT` + `CONFIRMED` 不被全局禁止。校验器必须验证：`retrieval_id` 存在于本 bundle；`access_kind` 先规范化再对照枚举（`Search_Snippet` / `search-snippet` 等 snippet 别名一律 D）；snippet/D 不能单独 CONFIRMED；`unauthorized_reprint` / `catalog_page` 无 kind 级分层上限，原作正文的未授权转载可以单独以 Tier A 支撑 CONFIRMED；两个 Tier B 须不同 `source_id` 且属于不同实际平台等价类（平台名大小写不敏感；`same_platform_as` 必须指向存在的 source 并按连通分量传递，不算独立）；RECEPTION 的独立 Tier C 同样使用该等价类；`claims.yaml` 的 `scene_id` 与 evidence / 目录名一致。`isolation_status: SUPERSEDED` 不得有 ACTIVE 行；合法重跑后改为 `CURRENT` 即可，不按 scene_id 永久锁死。

`live_original_fact_count` 只计 `ACTIVE` + `ORIGINAL_FACT`，不含 `RECEPTION`。

FactClaim 必须引用 `retrieval_id`，且该 retrieval 必须是隔离 RUN 实际消费的材料。纠偏阶段后加、未进入该次隔离 RUN 的来源，只能写成 Researcher / Analysis notes，禁止贴 ORIGINAL_FACT 或 CONFIRMED。不得在精确材料包丢失后，把 `source_id` 级历史提取手工改写成 PAGE/SNIPPET 再当作 `ACTIVE` FactClaim。

`claims.yaml` 是断言登记表，字段含 `effective_status` / `supersedes` / `grade` / `retrieval_ids`。

| effective_status | 含义 |
|---|---|
| `ACTIVE` | 当前有效 FactClaim。可按上表规则标 CONFIRMED。必须能回溯到隔离时实际读取的 retrieval。 |
| `LEGACY_UNRESOLVED` | 历史隔离产物：只引用过 `source_id`，精确包已丢失，或 retrieval 绑定是事后补写/已错绑。不是 live FactClaim，不得 CONFIRMED，不计资格。 |
| `ARCHIVED` / `SUPERSEDED` | 被取代，不进入有效表。 |

被取代的 CONFIRMED **标签**交给 Git 历史，不得继续作为工作树里的有效格式。`schema_version: 0.2` 的 scene 必须有 `claims.yaml`；`0.1-legacy` 的 SCENE-001 没有 live 表，也不得冒充已迁移。

计入 design-map 收敛阈值的断言必须同时满足：由已通过 §11 资格门的 Evidence workflow 生成（PILOT 产物不自动追认）；可回溯到具体 **ACTIVE** FactClaim 与 retrieval_id；ORIGINAL_FACT 达 SUPPORTED 及以上；RECEPTION 只能支撑公共记忆与体验接受度侧的问题；经规定保真抽查覆盖。`LEGACY_UNRESOLVED` 与 `qualification_eligible: false` 的 legacy scene **禁止进入资格统计**。

### 9.2 玩法断言三层（v0.2 新增）

不要把所有产出都叫 Element。分成：

- **Narrative Observation**：仅是观察，例如"人物被公开击败后感到震惊"。记录但不进入机制讨论；
- **Gameplay Structure**：已具备玩法形态，例如"一个角色可以阻止另一角色移动经过其所在窄道"；
- **Gameplay Primitive Candidate**：进入重点分析，必须同时满足——玩家可以主动利用或主动对抗；会改变世界状态；产生新的后续选择；可与其他结构重组；不绑定某一特定桥段。

已知候选动词族（仅示例，不是清单，也不代表任何一个会进入底层机制）：阻挡、持续控制、转移控制、隐藏、揭露、附着、解除、追踪、标记、承诺、携带、争夺。

### 9.3 动词导向

本工作流寻找的是"通用动词 + 状态变化"，不是玄幻名词。小说表面有炼丹、阵法、御剑、灵兽、傀儡、储物戒、神识、禁制、秘境（名词世界）；要发现的是混合、加热、注入、附着、连接、解除连接、控制、远程控制、封锁、解锁、感知、遮蔽、追踪、标记、携带、放置、交换、争夺、破坏、修复（玩法世界）。

## 10. ElementClaim 格式（v0.2 强制五问）

宽泛断言（"需要有限认知""需要 NPC 重评"）价值有限。每条 ElementClaim 必须逐项回答：

1. **玩家要做什么？**（具体到一个玩家会反复做的尝试）
2. **现有规则为什么不够？**（指出当前结构在哪一步失效）
3. **缺的具体世界能力是什么？**（一句可实现的状态／事实链／查询）
4. **它创造了什么新的玩家选择？**（列出因此变得可行的若干做法）
5. **可以和什么重组？**（跨桥段复用面）

禁止主题标签式元素（"打脸系统""扮猪吃虎系统""夺宝系统"）。宽泛到任何叙事都命中的元素不得进入映射统计。由 ElementClaim 派生的机制候选在面向用户展示前，还须过 AGENTS.md §2.3.1 的原子机制前置门；原作依赖复杂隐藏模拟的桥段应如实记录该依赖，并在映射中标注"原作结构不满足项目准入"，而不是简化改写原作事实。

映射标记：**COVERED**（现有规则已能表达）／**PARTIAL**（部分可表达，指明缺口）／**ABSENT**（缺失；须注明是否超出当前里程碑范围）／纠偏用 **NOT_A_GAP**（现有规则已覆盖，不是缺口）／**REJECTED_BY_CONSTRAINT**（与冻结约束冲突）／**UNKNOWN**（未验证）。不得把 NOT_A_GAP 或 REJECTED_BY_CONSTRAINT 统称为分析假设。

## 11. Evidence workflow 资格门（v0.1 保留）

- **对抗夹具**：同一 EvidenceBundle 至少提取两次——(a) 零项目上下文；(b) 注入伪造的"项目希望得出某结论"背景。两次的 ORIGINAL_FACT 断言集合与分级不得实质漂移；漂移则 `adversarial_fixture: FAIL`。INFERRED／UNKNOWN 计数的显著变化、文件自己承认项目注入改变了选材或推断倾向，均视为实质漂移。
- **可复核性**：RUN-A/B 原文、完整产物 hash、每条 retrieval 的页面 hash、模型、完整提示、参数与运行 manifest 缺任何一项，则 `reproducibility: INCONCLUSIVE`。精确材料包不得只留在 `/tmp`。
- **资格信用**：`qualification_credit` 仅为 `NONE` 或（资格通过后的）精确 build id。`FAIL` 与 `INCONCLUSIVE` 不得合成一个 verdict；不可复核的 FAIL 运行 `qualification_credit: NONE`。v0.1→v0.2 后旧 PASS 不得累计。
- **资格门校验**：`qualification_eligible: true` 必须同时满足——`research/qualification.md` 的 `eligible_build_ids` 含该 credit；`adversarial_fixture: PASS`；`reproducibility: PASS`；`run_manifest` 含 model/prompt/parameters，以及 RUN-A、RUN-B 的**仓库内相对路径**（`run_a` / `run_b`）与对应文件的 SHA-256（`run_a_hash` / `run_b_hash` 必须等于逐文件计算结果，禁止只填占位 hash）；`materials.file` 必须是仓库内相对路径（禁止绝对路径与 `..`）且 `materials.sha256` 与该文件内容一致；每条 retrieval 有仓库内 `file`，且 `hash` 必须是该文件的 64 位 hex SHA-256（占位字符串如 `hash-a` 不得绿灯）；sources 与 claims 均非空。缺 `qualification.md` 视为没有任何合格 build。只写字段、指向仓库外文件、或 hash 对不上不得绿灯。
- **溯源夹具**：抽查全部 **ACTIVE** CONFIRMED / SUPPORTED 断言：`retrieval_id` 必须存在且列入 `isolation_consumed_retrieval_ids`（有 ACTIVE 行时该字段必填，省略不等于跳过）；CONFIRMED 必须满足 §9.1 的 A 或独立双 B；**SUPPORTED ORIGINAL_FACT 不得仅凭 Tier D / 搜索摘录**（SUPPORTED 会进入后续收敛）。搜索摘录不得被当成完整页面来溯源。
- **YAML**：`evidence.yaml` /（0.2）`claims.yaml` 必须能被 `yaml.safe_load` 解析。含 `:` 的标量必须加引号。依赖 **PyYAML**（`pip install -r research/scripts/requirements.txt`）。检查入口：`python3 research/scripts/check_evidence_yaml.py`；合同自检：`python3 research/scripts/test_check_evidence_yaml.py`。`scene-facts.md` 由 `python3 research/scripts/generate_scene_facts.py` 生成，不得手改。
- **schema_version**：每个 `evidence.yaml` 必须声明。`0.2` 为当前合同；`0.1-legacy` **仅允许** `SCENE-2026-08-29-001` 且目录名必须匹配，必须 `qualification_eligible: false`。任意新 scene 冒充 legacy → 失败。
- **保真抽查**：每批 ≤5 个 scene，用户抽查其中 1–2 个的 `scene-facts.md` 对照 retrieval。
- **资格失效**：模型、提示模板、流程或关键参数实质变化后，既有资格失效，必须重新校准。
- 资格结论记录于 `research/qualification.md`；该文件不存在即视为未通过资格，全部产物按 PILOT 处理。
- CONFLICTING 时间线必须在 `scene.timeline.branches` 并列；D 级 branch lead 不得写入 participants / key_decision 主定义。
- 机制映射不得把「现有规则已覆盖」的项叫分析假设。已有能力标 `NOT_A_GAP`；与冻结约束冲突标 `REJECTED_BY_CONSTRAINT`；未验证项标 `UNKNOWN`。不得写成 COVERED／“已过原子门”，若未过 AGENTS.md §2.3.1，或与 `interrupted` 排除、未冻结探知、已有 MoveActivity 冲突。

## 12. scene 目录 schema

每个 `evidence.yaml` 根级必须有 `schema_version` 与 `qualification_eligible`。

**`schema_version: "0.2"`（当前合同）**

```text
research/scenes/SCENE-YYYY-MM-DD-NNN/
  context-snapshot.md
  evidence.yaml         # scene 卡 + sources[].retrievals[]（retrieval_id / access_kind / tier / hash；资格通过时还须仓库内 file）
  claims.yaml           # 断言登记表（effective_status / supersedes / retrieval_ids）
  scene-facts.md        # 与 claims.yaml 一致的人类可读表
  adversarial-check.md
  analysis.md
  elements-mapping.md
  report.md
```

每个 source 必须有非空 `retrievals[]`。搜索摘录 retrieval 一律 Tier D。`isolation_status: SUPERSEDED` 表示历史隔离不能绑定到当前 retrieval 集合；在重跑之前不得有 `ACTIVE` FactClaim。

**`schema_version: "0.1-legacy"`（冻结 allowlist：仅 `research/scenes/SCENE-2026-08-29-001/`）**

未迁移：可以没有 `retrievals[]` / `claims.yaml`，文件里也可以仍把搜索摘录标成 B/C。这**不是** 0.2 合规，也**不是**资格绿灯。必须 `qualification_eligible: false` 且 `qualification_credit: NONE`。新 scene 不得使用该 schema。

失败的 0.2 scene 留短 tombstone：`claims: []`、生成的 `scene-facts.md`、隔离原文在 Git 历史。不要在工作树保留零条 live claim 的长 ledger。

`python3 research/scripts/check_evidence_yaml.py` 必须通过。该脚本依赖 PyYAML，见 `research/scripts/requirements.txt`。canonical 禁词放在 `canonical_exclusions` / `canonical_forbidden_tokens` 数据里，不写死在校验器。

## 13. 版权与保存纪律

只持久化研究证据：URL、标题、作者/作品、章节定位、访问时间、来源类型、页面 hash、每条 ≤50 字的必要摘录、Agent 自己的事实转述。不保存作品全文或成章内容；本目录不得演变为文本库。

## 14. 与 design-map / M-1 的关系

- 每个完成的 scene 在 `design-map/inbox.md` 追加一条 WEB_SOURCED 案例（引用 scene 目录；逐断言标注分级与 evidence_eligible；PILOT 期一律 `evidence_eligible: false`）；
- 挂格、冲突并列、UNPLACED、收敛扫描与晋升完全沿用 AGENTS.md §8；候选晋升只由用户裁决；
- 本工作流不阻塞、不推进 M-1；其候选产出不得以任何方式绕过 CURRENT_STATE 的当前禁止扩展。

## 15. v0.1 → v0.2 变更摘要

- 定位从 Scene Research（叙事因果）转为 Gameplay Reverse Engineering（玩法因果）；
- 新增 §2 核心分析模型、§3 优先级原则、§4 NPC 地位与不变量边界、§7 十段主视图、§9.2 玩法断言三层、§9.3 动词导向、§10 五问 ElementClaim 格式；
- 提取阶段强制"不回应项目问题"，并把提问方向改为操作链事实重建；
- 证据侧（隔离、来源分级、断言资格、资格门、版权、design-map 关系）全部沿用 v0.1，未削弱；
- 已完成的 SCENE-2026-08-29-001 按 v0.1 模型分析，其分析层需按 v0.2 重看（见该 scene 的 report 附注与 RQ-001 状态）。证据层标为 `schema_version: 0.1-legacy`，`qualification_eligible: false`，不进入资格统计。
- 2026-08-29 用户裁决：不对盗版网站做任何分层限制。废除「盗版全文站不得计为 Tier A」。checker 不得对 `unauthorized_reprint` / `catalog_page` 强制 D 或禁止 A。已有 PILOT scene 未按新合同重分级。

### 15.1 2026-08-29 纠偏补丁（SCENE-002）

- 搜索摘录强制独立 retrieval 且 Tier D；**新的隔离提取**产出的 ACTIVE FactClaim 必须引用当时实际读取的 retrieval_id。
- 夹具三分项：`adversarial_fixture` / `reproducibility` / `qualification_credit`。
- CONFLICTING 在 `scene.timeline.branches` 并列；D 级 lead 不是主定义。canonical `participants` / `situation` 不得写入仅 D 级出现的人物或未被支持的击杀后组织交付顺序。
- `claims.yaml` 是失败 scene 的 tombstone（`claims: []`）。37fd364 隔离原文与事后绑定 ledger 只留 Git 历史。`scene-facts.md` 由脚本生成。live CONFIRMED / ACTIVE 计数为 0。这不禁止其他 0.2 scene 按 §9.1 得到 CONFIRMED，也不因 scene_id 永久要求 SUPERSEDED。
- 映射状态：NOT_A_GAP / REJECTED_BY_CONSTRAINT / UNKNOWN，不统称分析假设。
- YAML 解析与合同检查：`research/scripts/check_evidence_yaml.py`（依赖 `research/scripts/requirements.txt`）。
- 本工作流仍不推进 M-1。H-A 保持 UNKNOWN。
