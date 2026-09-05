# 多研究共享获取：计划与实施

日期：2026-09-05。基线：`20f2e6818ba62f24f95a97e0bf9cd86dc636cd69`。
目标：后来研究检测到同一来源正在获取时立即处理其他作品，最终共享核验后的
原文，各自使用普通 Handoff 和原生研究。适用同一宿主、同一本地库根。

## 1. 行为契约

| 观察 | 当前研究的动作 |
| --- | --- |
| 已登记且适用的合格来源 | 重新 verify 后复用 |
| BUSY_SKIPPED | 保存获取引用；立即遍历下一部待处理作品，不等待、不另建下载目录 |
| INITIALIZATION_REQUIRED | 持锁者在 binding 发布前中断；用原 CONFIG 重试 shared-acquire，不猜测冻结输入 |
| PARTIAL | 保留缺口、原生原因和 retry_not_before；处理其他作品，之后同引用回查/恢复 |
| READY_FOR_REVIEW | 条目到齐；执行现有审查和共享 seal，不能当成 COMPLETE 来源 |
| SEALED | 重新验证封存；为当前需求 prepare 普通 Handoff，然后登记/原生执行 |
| 全部剩余项都在获取中 | 宿主记录 WAITING_FOR_SOURCES，保存引用；本轮结束，不忙轮询 |

WAITING_FOR_SOURCES 是宿主报告用语，不增加核心状态或 campaign 事件。
后来的研究在一轮其他作品处理后回查引用；若需跨轮唤醒，使用已有宿主能力，不在
库中创建后台服务。跳过不代表作品失败或完成；不得消失于最终覆盖报告。

## 2. 身份、文件和锁

获取键由原生 WorkRef 的 identity、channel/scope_url/edition_label/extractor、
预期 entries/chapters 内容确定。不同研究 ID、原始配置路径、来源昵称、质量审查
描述不进入键。书名相同但身份不同不会合并；不同版本、目录和镜像配置保留独立键，
以允许明确的版本研究或保真度对照。宿主先查合格来源，不能通过换镜像或改版本
标签规避已存在的获取、访问拒绝或限流。

```text
L/acquisition/shared/<acquisition-id>/
  coordination/.novel-ingest.lock
  inputs/<content-digest>.*
  config.json
  binding.json
  run/                              # 既有 source-acquisition Run
  sealed.json                       # 验证后发布的封存目录引用
L/research/<research-id>/acquisition-observations/<unique-id>.json
```

`scripts/shared_acquisition.py` 是宿主工具模块，由 research_library CLI 调用。
直接复用现有 `_exclusive_work_dir` 的非阻塞系统锁，锁覆盖一次初始化、获取/导入、
恢复或封存调用。持锁进程退出由操作系统释放；不依赖 PID 判断、时限猜测、租约或
删除锁文件。锁忙返回结构化 BUSY_SKIPPED 和 TRY_OTHER_WORK，退出码 4；没有调度器。
锁文件必须保持原 inode，不手动删除。既有底层 run 锁继续保护原生操作。

先验证身份、配置和现有授权，再生成获取键；同键下只有持锁者能发布输入快照、
配置与 binding，或调用原生 runner。运行配置和依据按内容冻结；后来任务的不同
节奏或权限不能覆盖它们。冲突失败并提示检查原引用，不产生新的 run。接手使用
shared-resume 原引用，保留原生状态、次数、冷却和访问拒绝。

观察文件是不可变宿主操作记录，只证明某时观察到的状态；不加入 Catalog，也不是
当前锁状态的证明。shared-status 每次重新认领及验证原生状态；列表、旧观察或
sealed.json 都不直接授予来源资格。源/权限/目录/CAS 仍由既有原生验证器检查。

## 3. 接口与工作流

```bash
python scripts/research_library.py --library-root L shared-acquire RESEARCH_RECORD_ID CONFIG --work-ref WORK_REF_JSON
python scripts/research_library.py --library-root L shared-acquire RESEARCH_RECORD_ID CONFIG --work-ref WORK_REF_JSON --input CHAPTER_INPUT_DIR
python scripts/research_library.py --library-root L shared-status RESEARCH_RECORD_ID ACQUISITION_ID
python scripts/research_library.py --library-root L shared-resume RESEARCH_RECORD_ID ACQUISITION_ID
python scripts/research_library.py --library-root L shared-resume RESEARCH_RECORD_ID ACQUISITION_ID --input CHAPTER_INPUT_DIR
python scripts/research_library.py --library-root L shared-seal RESEARCH_RECORD_ID ACQUISITION_ID --review REVIEW_JSON
```

前两条分别为 C1 与 C4；C2 继续未实现。WorkRef 通过原生身份构造器及 schema 校验，
配置书目必须一致。所有输入由宿主从已核对的作品和现有目录构造，不要求用户手写。
CONFIG 的 run_dir 在共享入口替换为固定 run 路径；底层 source_acquisition CLI
保持原有语义，外部旧 run 不迁移。新并行任务必须使用共享入口，直接调用底层工具
在任意其他路径获取无法受到库锁保护。

shared-status 不获取网络/正文，可能修复尚未完成的幂等初始化，并写本次观察。
shared-resume 只执行一个原生有界调用，不无限重试。READY_FOR_REVIEW 后用原生
review-template/verify 及真实样本依据编写审查，调用 shared-seal；共享封存引用
可在任何研究 Handoff 产生前被另一个研究发现。之后仍按该研究权限、需求、
普通 Handoff、freeze 和原生执行路径接入。seal 失败不发布可复用引用。

封存后到引用发布前中断，可能留下已验证的孤立封存目录。重试不会重新下载，
但可能生成另一个含不同封存时间的封存目录；不自动删除孤立材料。

观察研究使用已有 attach/SOURCE_STARTED/FINISHED、预算和 disposition；引用
实际观察文件，不伪造完成事件。等待期间处理其他作品不得扩大研究冻结范围。

## 4. 实施步骤与验收

1. 新增宿主协调模块和严格 binding/观察 schema，复用原生锁/runner/seal。
2. library CLI 增加四个共享命令，BUSY/PARTIAL/INITIALIZATION_REQUIRED 退出 4，保留其他错误语义。
3. 同步 explore/observe Skills 与获取/库使用说明中的跳过、回查、封存入口。
4. 回归：真实并发单写者、跳过后另一作品可完成、系统杀进程后锁可重新认领、
   部分获取失败后续传、原生冷却、权限与身份拒绝、路径/配置/封存篡改、CLI 退出码、
   同源异路径不重复获取、已封存来源跨研究复用。
5. 运行完整测试、Skills 同步和 diff 检查；跟进提交推送后续 PR，保留 main 不合并。本轮检查发现原 PR #21 已由外部合并，
   新分支从其 main 合并提交起步；合并提交与已测试基线内容完全相同。

最终全量验证：**871 passed in 250.60s**，其中共享获取测试 **15 项**。

```bash
PATH="$PWD/.runtime/library-validation-venv/bin:$PATH" PYTHONPATH=src .runtime/library-validation-venv/bin/python -m pytest
python3 scripts/sync_skills.py --check
git diff --check
```

另以独立进程调用真实 CLI，完成合成 C4 shared-acquire → shared-status，结果 PASS。
首次 smoke 因 macOS `/var` 临时目录别名为符号链接而按策略拒绝；使用真实绝对路径
后通过，未放宽路径检查。Skills 镜像、文档相对链接/代码围栏和 diff 检查通过。
最终测试后只更新交付说明；提交后的 repository_commit 等血缘值按原生规则变化。
Ubuntu/Windows NOT RUN；没有变更核心安装行为，不宣称已做跨平台或 wheel 验收。
测试使用合成材料，本轮没有执行真实小说下载。
