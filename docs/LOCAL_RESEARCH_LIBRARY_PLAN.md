# 本地原文库实施计划 v1

- 日期：2026-09-05。
- 状态：**设计交付；实施未开始**。下面命令是拟议宿主接口，当前不能运行。
- 设计依据：[LOCAL_RESEARCH_LIBRARY_DESIGN.md](LOCAL_RESEARCH_LIBRARY_DESIGN.md)。
- 基线：`104f0028116d29deb29b78adb64e8f94700d0707`。

## 1. 实施顺序

优先做“封存章节原文可复用 + 统一索引”，再接产物入口，最后验证自动研究全过程。
每个阶段独立提交，保持已审阅历史。沿用已有获取与原生执行能力，不重写下载器。

| 阶段 | 交付与负责层 | 完成门槛 |
| --- | --- | --- |
| L1 原文登记与章节索引 | `scripts/research_library.py`；`contracts/host_library/`；来源/索引测试 | 合成封存源可登记、回放和逐章查询；缺章、篡改、歧义和越界均被拒绝 |
| L2 查询与原生产物 | 同一宿主脚本扩展；仅在确有需要时抽取原生公共验证入口 | Scene/Generic 原生完成产物可登记、查询、回到精确 support；失败与零结果正确区分 |
| L3 自动研究接入 | canonical explore/observe Skills、同步镜像、工作流文档和集成测试 | 本地命中继续原生执行，未命中继续现有获取，预算和任务等待语义不变 |
| L4 迁移与真实试运行 | 宿主操作、版本化验收报告；运行数据留库中 | 旧材料原址登记、索引重建、斗破首个完整来源和首个端到端研究分别验收 |

L1—L3 是代码实施完成门槛；L4 是真实数据和运行验收，受真实来源完整性、访问、
质量审查和语义任务规模影响，不预先承诺取得全文或把它记为已经通过。
斗破先跑通端到端，其余作品按已冻结研究范围、预算分批推进；单纯验证库接入时，
其余书只到来源层，不能把这个测试范围当成已完成用户的全部研究。

## 2. L1：原文版本与章节索引

### 改动范围

- 新增宿主脚本 `scripts/research_library.py` 和 `contracts/host_library/*.schema.json`。
  schema 与宿主资源不自动进入 core wheel；脚本必须从可信 checkout 定位并校验其版本。
- 复用获取脚本的 `validate_sealed`、现有规范 JSON/hash/原子写 primitives 和
  `DirectoryNovelAdapter`。新功能不修改已封存来源，也尽量不修改获取脚本本身。
- 先建立 source/validation 登记及 WorkRef 连接，再生成章节和 SQLite 元数据投影。
  WorkRef 必须来自原生已验证声明；仅导入章目录不足以伪造一个书目身份。
- 直接使用现有封存章节和 `chapter-view.json`，不生成整本 TXT、不增加拼接导出
  接口。未完成获取保留逐章文件和缺口状态，原始输入 TXT/EPUB 作为 provenance 保留。

### 拟议 CLI 契约（尚未实现）

统一入口为 `python scripts/research_library.py --library-root L SUBCOMMAND`。
以下路径和 ID 均由宿主从真实记录填入，不要求用户手写 JSON。

| 子命令 | 输入 | 确定性输出/副作用 |
| --- | --- | --- |
| `init` | 固定绝对根目录 | 初始 library.json；重复同配置幂等，不迁移已有库 |
| `register-source S --declaration D --native-root P_OR_R` | 已封存源、已验证普通声明及其原生根 | 来源登记 ID、核验结果、source_revision；不调用模型 |
| `list-works` / `list-sources --work WORK_REF_ID` | 精确身份或元数据筛选 | 候选、版本和当前验证状态；来源优选由宿主说明 |
| `verify RECORD_ID` | 某项登记 | 重放相关来源/产物闭包，输出验证回执 |
| `reindex` | records 与仍可读取的原生根 | 重建 SQLite；缺失/损坏记录列为不可用，不丢失错误 |

`register-source` 的 D 是当前原生 builder 真实产生的声明，不能调用者随填
COMPLETE 即接纳。登记同时验证 D、S 和原生回放的连接。未来若支持封存后但
prepare 前的暂存发现记录，应使用独立非准入类型；L1 不以此绕过普通 Handoff。
宿主工作流可在 prepare 取得 D 后登记，freeze 前后均重新检查要求的闭包。

所有命令 stdout 给出结构化 JSON：format_version、command、outcome、record_refs、
paths、issues。宿主操作失败码独立于原生退出码；建议 0 成功、1 校验/输入错误、
2 环境/IO 错误、4 明确未就绪。不得把 3 用作宿主“成功”，也不吞掉原生
WAITING_FOR_AGENT。具体错误记录原生 code/message，恢复动作由原生契约决定。

登记先原子发布不可变文件，再事务更新索引；中断最多留下未被索引的有效登记。
重试或 reindex 可恢复。登记文件采用 staging → 验证 → 原子发布顺序。
源文件、manifest、目录、长度和摘要任何不一致都拒绝发布，不覆盖现有正确记录。

## 3. L2：研究与产物入口

### 拟议接口

| 子命令 | 行为 |
| --- | --- |
| `register-research INPUT` | 记录实际 P/R/W、需求/Profile/Handoff/来源引用；不复制或改写原生事件 |
| `register-product INPUT` | 分 Scene/Generic/Report 验证真实产物，发布登记；重复相同输入幂等 |
| `list-research` / `list-products` | 按作品、来源版本、需求、Profile、产物类型和原生状态过滤 |
| `search-text SOURCE_ID --query TEXT --limit N` | 有界逐章字面搜索，返回 TEXT_MATCH、原文坐标和截断标识 |
| `show-evidence PRODUCT_ID --record-id NATIVE_RECORD_ID` | 原生闭包校验；列出每项 support 的规范化 span 及对应章节入口 |

输入文件是宿主登记契约，schema 需明确每种分支的必需字段。已有原生 ID/回执路径
必须读取并验证，不接受自述 COMPLETE、手算 candidate ID 或任意 quote 字段。
先列元数据再按需要读取正文；默认产物导出沿用原生 OFFSETS_ONLY/权限策略。

### 原生验证适配

- Scene：调用 `phase0_execution` 原生回执/历史校验及 `validate all` 对应生产入口；
  获取 run 的真实 Catalog、SourceChapter、Segment、SceneCandidate 和 export。
- Generic：调用 `validate_generic_execution` 及现有 snapshot/corpus/reduction 闭包
  校验，从 R 的真实 CAS 引用和 W 的 ingestion 解析 source_spans。
- 不复制这些验证规则到宿主脚本；若唯一可调用入口为私有函数，则单独提交最小
  公共入口抽取并保留验证顺序和错误语义，执行受影响测试，不能写简化版验证器。
- REPORT 保存产物引用及内容摘要；执行保证、物理覆盖、语义覆盖从原生状态继承，
  不把 DRAFT/UNVERIFIED 或 UNQUALIFIED 升为事实保证。
- search-text 的章节文件字符位置与 show-evidence 的 Segment 位置使用不同字段名。
  用户查看证据时规范化文本准确高亮，并提供章节入口，不做模糊引文修补。

## 4. L3：自动接入

修改 canonical `.agents/skills/xhnovel-explore/SKILL.md` 与
`.agents/skills/xhnovel-observe/SKILL.md`，同步 `.claude/skills/`，扩展
[SOURCE_ACQUISITION_WORKFLOW.md](SOURCE_ACQUISITION_WORKFLOW.md)。不要复制整套
Skill 到 AGENTS.md，也不在 runtime 加入 agent scheduler。

宿主操作顺序为：中立需求冻结 → 本次绑定/全库查询 → 来源重验或获取 → 原生
prepare 返回声明 → 登记来源及章节索引 → freeze → 原生语义执行 → 原生验证 →
登记产物 → 报告。来源命中前不能静默改写已冻结需求，源登记不能把
先前仅“源准备”的用户请求扩大成完整研究。

对观察 campaign，本地来源准入也必须 attach 实际材料并记 SOURCE_STARTED，
prepare/freeze 成功后 SOURCE_FINISHED；不通过更换 root 或操作 ID 重置失败预算。
本地合格源和历史产物查找都不能把 FULL_WORK 改成关键词命中章节集合。

验证断点恢复时必须继续同一 W/需求/Handoff/配置；成功执行但登记失败时只补登记。
测试用原生生成任务和固定合成答案验证接缝，不用伪造 receipt 代替原生执行。

## 5. L4：现存材料与运行安排

1. 只读盘点已有 acquisition、sealed、P/R/W 和产物，记录路径、格式、数量、
   manifest 与实现版本。对现有斗破材料重新读取实际进度，报告里的 136/1646
   仅为历史线索；不得把目录中“有文件”视为已验收。
2. 未完整材料继续用现有 source-run 恢复。若版本不兼容，用可信历史 checkout
   或明确新导入；不能改 binding。保留逐章文件及缺口清单，不做整本拼接。
3. 合格封存源和已完成原生研究以 EXTERNAL_REFERENCE 原址登记，不移动旧目录。
   新研究先固定 L 中的路径；记录所有外部依赖供备份。
4. 首本真实完成覆盖/质量核验后登记章节目录并验证跨章节查询。依需求冻结的范围
   完成一次原生端到端研究，登记产物和规范化证据；原文获得与语义完成分开记状态。
5. 再发起不同研究需求验证复用同一源、不重复下载、不误复用旧语义结果。若只是
   库功能验收，用完整合成书完成此步骤即可；真实大规模模型任务需计入研究预算。
6. 删除派生索引副本并重建；在备份副本按原路径恢复的隔离环境验证闭包，不破坏
   活跃数据。不同绝对路径恢复记为未支持，不手工改回执冒充通过。

## 6. 验收矩阵

下面是必须落实的行为测试，不是已经通过的测试清单。首次设计的 A07、A10、A24
对应整本派生功能，随该功能删除；其余 ID 保留，现有 21 项有效验收。

| ID | 场景 | 通过条件 |
| --- | --- | --- |
| A01 | 完整合成书注册、重复注册 | 使用原生 WorkRef；同一封存来源不生成冲突身份 |
| A02 | 同名不同作者/identity basis | 只给候选，不静默合并或挑另一书继续 |
| A03 | 少一章/审查未解决/连载当前快照 | 不能登记为可复用完整来源或产生完整 Handoff |
| A04 | 封存目录额外文件/篡改章/篡改清单 | 当前验证失败；旧 PASS 不放行 |
| A05 | 未编号番外、章号重置、同名章、分页章 | 现有 chapter-view 保持明确阅读序和页→章关系，无正则重切/去重 |
| A06 | 中文、emoji、组合字符、连续空格 | 章内查询 codepoint 区间精确回取命中；字节范围另算，不得以 UTF-16 索引混算 |
| A08 | 写入中断、空间不足、并发登记 | sealed 未改变；未提交记录不可见；恢复不覆盖正确结果 |
| A09 | 章节索引缺项/重复/越界/路径逃逸 | 重建或拒绝，不通过不完整查询结果兜底 |
| A11 | NFKC/空白造成规范化长度变化 | 精确高亮 native span；不声称章节文件相同位置就是证据 |
| A12 | Scene/Generic 多 support、跨章、重复正文 | 每项经 native lineage 定位；不按相同文本猜绑定 |
| A13 | UNKNOWN/缺存储许可/缺模型许可/禁摘录 | 各出口遵守对应权限，不因本地存在而默认授权 |
| A14 | 原生成功但零结果 | 可登记成功执行，查询解释零条，不伪造观察 |
| A15 | WAITING/FAILED/登记中断 | 原生状态原样保留；仅登记中断不重新执行模型 |
| A16 | SQLite 删除/落后/登记后崩溃 | 从不可变记录和原生验证重建；错误关联不可用且可见 |
| A17 | 老 W/R 移动或 CAS 缺失 | MISSING_PATH/原生失败，不能按 title/profile 就替换根 |
| A18 | 旧获取 script SHA 不兼容 | 明确不可回放或用可信匹配版本；不执行源包代码 |
| A19 | 第二次不同需求命中同一原文 | 无重复下载；普通新 Handoff 和 FULL_WORK 语义路径 |
| A20 | 查已有结果与要求新研究 | 历史成果可读；跨研究相似结果不伪装为新 campaign 成功 |
| A21 | Generic 本地来源命中/失败/恢复 | SOURCE_STARTED/FINISHED、预算与原生 resume/retry 均闭合 |
| A22 | search-text 命中/无命中/截断 | TEXT_MATCH 标签；无证据升格或研究章节缩小 |
| A23 | 原始 TXT/EPUB 和封存章节 | 原始字节仍在 provenance；使用已验证章节目录，不重新拼接或分章 |

## 7. 验证与发布

- L1 跑新增来源/章节索引测试和受影响的获取测试；L2 跑对应 Scene/Generic 闭包与
  宿主入口测试。触及共享 runtime、多阶段或 contracts 时按 AGENTS 要求跑全套。
- L3 跑 Skill 文档/接缝测试及 `python scripts/sync_skills.py --check`。
- 每阶段运行 `git diff --check`。改变安装包资源或 installed-runtime 时才增加
  wheel 构建及 checkout 外 smoke；不把宿主脚本暗中变成第二个安装运行时。
- 不恢复已停用的 GitHub Actions，不把未运行的 Ubuntu/Windows 记作 PASS。
- 交付固定提交 SHA、实际测试结果、剩余限制；设计探针、合成端到端和真实全书
  验收分别记录。源冻结、任务生成、语义执行和最终报告完成各有独立证据。

## 8. 设计验证记录

### 8.1 首次设计探针存档

以下是在首次设计提交 `f3321bab538d809fa932c74550ddc4ba81928daf` 上执行的本地
合成探针。整本拼接已从当前方案删除，相关结果仅保留作历史审计，不是待实施功能。
目录分章、规范化坐标和封存完整性结论仍适用。探针没有迁移真实小说、启动语义
研究或实现 library 命令，不能用它们声明上面的 21 项验收已实施通过。

| 设计探针 | 实际结果 |
| --- | --- |
| 4 个显式章节包含未编号番外、同名章和章号重置 | Directory 发现 4 章；重新输入 TXT 适配器仅发现 3 章，确认不能假定等价 |
| 中文、emoji、组合字符和空白的拼接 | 210 字节、88 个 codepoint；两套区间均逐章精确回取，重复生成相同结果 |
| 原生 parse_text 的规范化偏移 | 同一“玉佩”原文行起点为 8，规范化行起点为 5；原生 Segment 内精确切片通过 |
| 现有合成获取 fixture → import → review → seal | 3 章封存回放成功；在旁路 views 生成 TXT/map 后仍成功，原 manifest 字节不变 |
| 在上述合成 sealed 树中额外添加 TXT | 现有 validate_sealed 拒绝；移除探针文件后恢复通过 |

探针复用了 `tests/test_source_acquisition.py` 的 `fixture_config/reviewed`，
只在临时合成树上操作；未给真实材料填 PASS。6 项设计断言全部通过。
本地探针文件为本设计 worktree 下 `.runtime/library-design-probe-20260905/probe.py`，
摘要 `4cd3b1e0fe50d68c98b31037079e8dfca59eeb01442a0cac9bc0758bdf9764d7`；
结果在同目录 `result.json`。这些本地运行文件不随文档提交，也不是未来回归测试的替代。
静态检查：`PYTHONPATH=src python3 -m pytest tests/test_docs_skill_contract.py
tests/test_exploration_skill_contract.py` 实际结果为 **27 passed**；新文档 4 个相对
链接均存在，代码围栏成对。初次未设置 PYTHONPATH 的收集失败在补齐本地导入环境后
解决。`git diff --check` 通过，提交时另检查暂存差异。
Ubuntu/Windows、真实全书和原生语义执行本次均未运行；安装行为未修改，无新增
wheel 验收要求。

### 8.2 当前章节方案修订

当前方案只保留章节原文、章节索引、来源复用和产物关联；移除整本生成、导出、
ViewRegistration、build-view、独立全文映射包及其验收项。README 和 PR 描述同步。
本次只修改设计文档，实际原文和原生输入文件均未删除或移动。当前修订重跑上述
文档契约检查，实际为 **27 passed**；4 个相对链接、代码围栏、21 项有效验收 ID
及整本功能接口移除检查均通过，`git diff --check` 通过。未重新运行已退出当前
范围的拼接探针，未运行真实小说研究。
