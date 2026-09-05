# 本地章节原文库实施记录

- 日期：2026-09-05。
- 设计基线：`0c9d456880cf948fc0c719ae57525bb26c0e528f`。
- 实施范围：L1 原文登记/章节索引、L2 原生产物查询、L3 宿主工作流接入。
- L4：完成默认空库初始化；真实材料归档、来源质量验收和全书语义研究未执行。
- 使用接口：[LOCAL_RESEARCH_LIBRARY_USAGE.md](LOCAL_RESEARCH_LIBRARY_USAGE.md)。

## 1. 实际交付

新增 `scripts/research_library.py`，直接复用既有获取和原生验证接口，未修改
`src/xhnovel_pipeline/` 或 `scripts/source_acquisition.py`。没有新增模型执行器、
窗口、调度器、整本 TXT 生成器，也没有改变当前质量/权限契约。

新研究的路径固定为：

```text
research/<研究ID>/
  request.json
  phase0/<来源版本>/
  campaign/
  works/<作品ID>/<来源版本>/<执行ID>/
    native/
    reports/
  reports/
```

`new-research` 在原生需求冻结前记录用户请求元数据并分配目录；它不代替 Intake、
Brief、Definition 或预算。冻结后的 P/R 不搬动。`allocate-execution` 验证本次
普通 Handoff 与封存来源的连接，再分配 native/reports，返回 W 给原生管线。
同一原生 Handoff/P/R 使用不可变绑定固定到一个执行登记，防止并发分配两个 W。

来源统一保存为 seal 的逻辑章，原始取得材料和质量依据仍在 provenance。不同
研究可指向同一 source_revision；研究问题、Handoff 和执行目录可以不同。产物
只能从成功原生回执登记；历史结果不自动代表新研究已完成。

登记使用 `contracts/host_library/` 的严格 JSON schema，与核心 Catalog 种类分离：
research、source、execution、external-execution、product、report、validation。
记录原子发布并以完整 SHA-256 寻址，SQLite 只投影元数据。列表每次从记录刷新，
重建时可进一步回放原生依赖；损坏项显示问题，不能用旧索引或旧 PASS 兜底。

## 2. 查询、证据与旧数据

- `search-text` 逐章有界字面搜索，返回 TEXT_MATCH 和章内 byte/codepoint 范围。
  查询不改变 FULL_WORK、不向中立需求或原生任务注入命中信息。
- `read-product` 支持原生产物分页/字面过滤，返回真实 record ID 和 source_spans。
  `show-evidence` 保留字段级 support 指针，回查准确规范化 span 及来源章节。
- Scene 回放原生执行历史，并对实际用于展示的内存 Catalog 再执行生产验证，避免
  先验证旧内容、再读取未经验证的展示内容。Generic 读取原生选定 corpus 验证器
  返回的 Catalog/records；不会选择另一个更新的 reduction 替代旧回执产物。
- 原生没有细粒度原文字符映射时不伪造映射。默认 OFFSETS_ONLY_NO_EXCERPTS；
  `--include-text` 受存储、模型发送、摘录权限共同控制。
- 有 source-acquisition 封存绑定的旧 W 可用 allocate-execution 的 --work-dir 原址
  登记。直接 TXT/EPUB 等旧原生执行使用 register-external-execution，必须有真实
  Handoff 执行历史和可回放闭包，不能因此进入可复用封存源列表。
- 普通直接执行但没有 Handoff 历史的孤立输出不在这两个准入入口内；不补造历史。
  源/编译器版本不兼容、路径缺失或 CAS 损坏明确失败，保留原目录等待匹配版本核验。
- 报告始终 HOST_AUTHORED，校验内容摘要和真实执行/产物归属，不认证报告语义。
  失败报告可以引用失败执行，不能带伪造的成功产物。

## 3. 宿主接入

canonical explore/observe Skills 及 Claude 镜像增加本地库工作流入口，共享来源
工作流同时明确新任务根分配、查库、来源登记、W 分配和产物登记的位置。用户不需
手写配置或在常规阶段回复继续，宿主按已有授权和原生预算连续推进。

新流程是：用户需求元数据 → 固定研究根 → 原生中立规划冻结 → 查库/核验或取得
来源 → 普通 Handoff → 登记来源/分配 W → 原生冻结与执行 → 原生验证 → 登记产物
与报告。历史 P/R/W 继续原址引用。仅规划、线索或源准备的请求仍保留原范围。

## 4. 验证记录

新增 `tests/test_research_library.py` 覆盖严格登记、原生身份、固定路径、同源不同
需求、跨章查询、Unicode、源/权限/CAS 损坏、索引重建、并发与中断、原址引用、
Scene/Generic 原生生命周期、成功零结果、失败报告、历史 TXT 执行归档及展示内容
变动后的拒绝。全部输入为明确合成测试材料，未给真实小说填写 PASS。

最终代码全量测试：**856 passed in 265.72s**，其中本地库测试 29 项。
执行命令（隔离工作树中）：

```bash
PATH="$PWD/.runtime/library-validation-venv/bin:$PATH" PYTHONPATH=src .runtime/library-validation-venv/bin/python -m pytest
python3 scripts/sync_skills.py --check
git diff --check
```

Skills 同步、文档链接/代码围栏和 diff 空白检查均通过。测试于最终实现代码树上
运行，随后仅更新交付文档；提交后 repository_commit 等构建血缘值按原生规则变化。

安装验证使用工作树下独立 venv（system-site-packages），离线 editable 安装以
提供匹配的原生 CLI；未修改用户全局安装。宿主脚本和宿主 schemas 不作为核心
wheel 运行时发布，未改安装行为。Ubuntu/Windows 未运行，不恢复已停用 CI。

默认 `~/Documents/xhnovel-library` 已通过实际 init/reindex 初始化，索引记录数为
0、issues 为空；用户已有小说、原生输入和运行目录均未删除、复制或搬动。
这证明空库可启动，不是斗破全书已入库或语义研究已完成。真实数据验收必须以
实际封存核验、原生回执和产物登记记录分别报告。

## 5. 并行获取跟进

在上述已验证版本之后，新增 [共享获取设计与计划](SHARED_ACQUISITION_PLAN.md)。
`scripts/shared_acquisition.py` 和 library 的 shared-acquire/status/resume/seal
入口用非阻塞系统锁包住既有 runner。后来任务收到 BUSY_SKIPPED 后保存获取引用，
立即处理其他作品；同源不同研究使用同一 run，完成后共享封存源。

原生运行状态、权限、审查和封存验证继续沿用。接手不会重置冷却/重试，进程退出
由 OS 释放锁。目录未初始化完成时明确要求用原配置重试，不猜测或补造 binding。
不同来源版本/镜像配置保持独立身份；不按书名强行合并，不能用改键规避访问限制。
新并行获取须通过共享入口，旧外部 run 保持原址。本次不启动真实小说获取。

并行扩展的最终测试结果见共享获取计划末尾；前述 856 项是前一实施阶段的结果。
