# 原文获取工具：实施记录

日期：2026-09-05。S1–S3 宿主代码和离线对接已完成；现有斗破章节已迁移。
真实完整源验收、C2 浏览器扩展和斗破全书研究仍未完成。

设计与验收定义保留在 [设计](SOURCE_ACQUISITION_DESIGN.md) 和
[实施计划](SOURCE_ACQUISITION_PLAN.md)；其中的“待实施”是设计时点状态。
当前命令、配置和恢复方法见 [使用说明](SOURCE_ACQUISITION_USAGE.md)。

## 1. 实现交付

入口：[scripts/source_acquisition.py](../scripts/source_acquisition.py)。
回归：[tests/test_source_acquisition.py](../tests/test_source_acquisition.py)。

| 责任 | 当前行为 |
|---|---|
| 有界获取 | 固定作品路径和有限目录；每次实际 C1 请求含重定向均受节流及预算约束 |
| 恢复 | raw、attempt、正文、accepted 分步原子持久化；以可回放的 accepted 计算进度 |
| 本地导入 | 章节目录、整本 TXT、EPUB；复用原生文件适配器，保存原始导入材料 |
| 来源核验 | 物理覆盖、重复正文、逻辑章节关系、固定质量样本及显式审查分别记录 |
| 镜像比较 | 标题多值候选、显式多对多对齐、有计算上限的精确文本差异统计 |
| 封存 | 完整性和质量全 PASS 后独立复制并绑定清单；使用前重新回放 |
| 编译器接入 | 复用 standing attestation、原生 Handoff builder、原生 ingestion 和 CAS 校验 |

源码提交依次为：

- `ddd74bb`：有界获取、持久化恢复、初始回归及设计文件。
- `d17b8b6`：完整性、质量审查、比较和封存。
- `09c7704403ea520f7e4aba0658b90e7347897e7e`：原生 Handoff/预冻结、整本导入和使用说明。

测试绑定最后一个代码提交。宿主运行另外绑定脚本的精确字节摘要，
更新脚本后应保留旧实现或建立新的 source-run，不能静默重解释旧提交。
分支为 `codex/source-acquisition`；没有推送或合并 main。

核心 `src/`、contracts、profiles、Skills 和打包配置均未修改。
研究仍必须走普通 Novel Spec 和原生 agent-files 全书流程。
此脚本没有模型调用、后台服务、来源轮换或浏览器自动化。

## 2. 验证结果

环境：本机 macOS，Python 3.12。新增宿主测试 54 项通过。
全库共 687 项取得通过结果，执行细节如下：

1. 未指定 PYTHONPATH 的首次调用在收集阶段失败，没有运行测试。
2. `PYTHONPATH=src python3 -m pytest --tb=short`：685 通过、2 失败。
   两项失败均因子进程切换工作目录后无法解析相对 `src` 路径。
3. 使用 `PYTHONPATH="$PWD/src"` 只复验这两项：2 通过。
   没有为环境问题修改测试或产品代码。

推荐从仓库根目录使用绝对路径设置运行测试：

~~~bash
PYTHONPATH="$PWD/src" python3 -m pytest tests/test_source_acquisition.py
PYTHONPATH="$PWD/src" python3 -m pytest
~~~

覆盖包括实际请求间隔、跨重启 Retry-After、挑战页拒绝、有限重试、
提交边界故障恢复、文件损坏、来源变化、目录缺口、重复正文、
歧义章节对齐、失效审查记录、封存变更、TXT/EPUB 及原生接口闭包。

合成作品通过了：封存 → Handoff → 同目录预冻结 → WAITING_FOR_AGENT →
拒绝答案 → 原生纠正重试 → SUCCEEDED → validate all。
破坏 CAS 后验证拒绝；原生 PARTIAL 的真实状态保留。
这些是合成材料回归，未调用模型或商业站点，不代表真实作品研究已完成。

T34–T36 浏览器验收没有实现或运行；T37 仅验证 C2 不会被隐式激活。
T38–T40 的原生生命周期已有合成回归，但 S6 真实斗破全书退出条件未满足。
Windows、Ubuntu 和真实网络吞吐没有验证；本阶段按设计只面向 macOS 宿主。
安装资产未改变，因此没有额外构建 wheel 或宣称已通过 installed-wheel 验收。

## 3. 旧数据实际迁移

运行记录位于 `.runtime/source-acquisition-implementation-20260905/`，不进入源码提交：

- `baseline-manifest.json`：旧文件基线与逐文件摘要。
- `baseline/`：151 个文件的独立快照，包含 136 个章节文件。
- `doupo-local-import/config.json` 和 `catalog.json`：迁移配置及待核实目录。
- `doupo-local-import/run/`：新提交记录及原始导入材料。
- `doupo-local-import/verification.json`：实际来源核验结果。
- `implementation-validation.json`：迁移对账、恢复及测试日志的摘要引用。

逐文件核对确认 151 个快照与基线摘要一致，136 个导入 raw 与旧章节字节一致。
导入标记为 `LOCAL_DERIVED_IMPORT`，HTTP 状态为 null；缺失的历史原始响应保持缺失。
复用原 attestation，没有重签或增加权限。重复导入后，raw/attempt/正文/accepted
等不可变产物没有增加或变化。

| 实际结果 | 值 |
|---|---|
| 暂定预期页面数 | 1646，仅来自历史报告，完整目录仍未证实 |
| 有效导入 | 136 |
| 缺页 | 1510；已到达页号范围内缺 47、100 |
| 覆盖/质量 | UNRESOLVED / UNRESOLVED |
| 全量异常扫描 | 79 项待审信号；不等于 79 个已确认错误 |
| 独立样本计划 | 13 个逻辑章位置；实际质量审查未完成 |
| import / verify / seal | 均返回 4，明确表示后置条件未满足 |
| 源冻结 / 研究 | NOT_RUN / NOT_RUN |

未解决审查记录填写真实检查时间并保留所有 UNRESOLVED，seal 明确返回
`E-ACQUISITION-NOT-READY`，没有产生封存源。
新工具没有接管旧采集进程，输出目录隔离；原目录和其他在途开发文件保留。

## 4. 后续阶段边界

| 阶段 | 当前状态及剩余条件 |
|---|---|
| S0 | 基线、权限复用、136 章迁移完成；六部作品的可信完整目录仍待逐源建立 |
| S1–S3 | 宿主实现及离线对接完成；使用方式已记录 |
| S4 | 已证明当前斗破来源不能准入；尚需完整目录、完整文本及通过质量核验的来源 |
| S5 | 条件扩展未实现；需按设计引用独立浏览器操作授权，再完成 T34–T37 |
| S6 | 未执行真实作品研究；依赖 S4 通过后沿原生流程运行和实测排期 |

下一步运行工作应先解决完整来源及质量，随后 seal/prepare/freeze。
保真度异常尚未核实，当前不能预报斗破通过验收的时间，也不能据合成测试
估算六部作品的语义执行日历工期。
