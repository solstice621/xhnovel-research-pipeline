# 秘境情节研究 — 阶段报告（2026-09-04）

研究问题：玄幻小说中秘境类情节——有界特殊空间（秘境/洞府/遗迹/禁地/小世界/古界/古墓/秘库/传承之地）的明确提及、开启/进入/离开/关闭/封印事件，以及与秘境明确相连的宝物/传承/凶险/禁制。

方法：xhnovel 通用全书抽取框架（PR #11）+ 新增 `secret-realm-v1` Profile（`REALM_MENTION`/`REALM_ACCESS`/`REALM_STAKE`，每条记录带 RFC 6901 证据绑定与精确源文偏移），host-agent 以 agent-files 执行器作答全部 native 任务，管线完成证据校验、精确去重与可重放导出。

## Profile 开发（已提交）
- 分支 `codex/secret-realm-profile` @ `e27771b`（基于 PR #11 head `6cc794f`）
- `profiles/generic/secret-realm-v1/`（manifest/prompt/schema）+ wheel 打包注册 + 一致性测试
- 全量验证：537 passed；sync_skills/compileall/git diff --check/build wheel 通过
- 验证了 PR #11 的扩展点声明：新增领域仅需 Profile 包 + 测试，未改抽取引擎

## 《斗破苍穹》全本运行（SUCCEEDED）
- 源：操作者授权本地副本（1663 章，run-004 同源），rights `FAIR_USE_RESEARCH`，`may_store_full_text=true`，`may_send_to_external_model=true`
- 摄取：NING-E69CAE71428F31957153（1663/1663 ready，0 重复）
- 抽取：676 单元全覆盖（eligible=covered，text_coverage=FULL）；1008 条 LocalObservation → 精确去重后 **408 条 CorpusRecord**
- 语义保证：`semantic_assurance=UNQUALIFIED`（技术成功≠语义完备，符合契约）；人工复核未做全量质量声明
- 语料构成：REALM_ACCESS 205 / REALM_MENTION 78 / REALM_STAKE 125
- 高频秘境（按出入事件）：天焚炼气塔 23、天墓 17、妖火空间 13、星域 11、丹界 10、岩浆世界 10、星界 9、远古遗迹 9
- 高频机缘（TREASURE）：古帝洞府 12、天焚炼气塔 8、远古遗迹 7、丹界 6、岩浆世界 5、天墓 4、斗圣遗迹 4、菩提古树 3
- 验证：`xhnovel-extract validate` VALID（CPS-E51BC95C48E837D06B2F）；核心 `validate all` OK
- 过程发现：6 个单元因「必选证据组拆分多 binding」被拒 → 合并 binding 后重试通过（拒绝尝试保留在审计链中）

## 《完美世界》— 暂停中（90/809 单元已答）
- 源：quanben.io 拉取 2010 章全文本地保存（授权见 operator-authorization.txt；书目佐证：百度百科 2014 章/658 万字）
- 摄取：NING-516C3D14AF208A8F9A09（2010 章 ready；7 个序号缺口的 WARNING 为 PARTIAL 唯一来源）
- 809 个抽取任务已物化；90 个单元已答（全部通过自检，含散布 10 个）
- 断点续跑说明：`RESUME.md`（幂等：重跑同一命令即续）

## 线索清单（LEAD_ONLY，非证据）
`leads-manifest.json`：完美世界、遮天、斗罗大陆、武动乾坤、大主宰、凡人修仙传、仙逆、一念永恒、圣墟、神墓、莽荒纪、吞噬星空——均 `rights=UNKNOWN`，未拉取。quanben.io 可达且含凡人修仙传等；多数笔趣阁系站点在本环境不可达。

## 诚实边界
- 本报告不含语义完备性声明：全文覆盖≠语义召回；语料是「本轮语义构建产出了什么」，不是「书中秘境全集」。
- reducer 仅做精确 payload 去重：同名不同指、同指不同表述都保留为不同记录，未做实体归并。
- 《完美世界》语料在断点处不构成可报告的全书结论。

## 审计留痕
工作区：`/private/tmp/xhnovel-secret-realm-research/`（授权、双书 CAS/任务/答案/checkpoint/CorpusSnapshot、线索清单、RESUME.md）。生成运行态一律未入库。
