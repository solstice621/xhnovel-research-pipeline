# Xuanhuan Domain Transfer Stress Test v1 — 执行报告

固定代码：`main@ceb1383b75bd4bea44d8c5ae7fd2a8785c2fca1a`

## Execution provenance

1. A1/B/A2 是否全部由 `xhnovel-pipeline research-novel` 生成？ **NO（尚未执行）**
2. 是否使用了项目原生 SceneWindows？ **探测阶段 YES**（`build_scene_windows()`）；三次正式 scout **未执行**
3. 是否使用了项目原生 provider request construction？ **NO（尚未执行）**
4. 是否使用 `_validate_scout_output` 的正式验证链？ **NO（尚未执行）**
5. 是否使用正式 merge？ **NO（尚未执行）**
6. 是否执行 `validate all`？ **NO（无成功 research catalog）**
7. 是否存在任何自写代码修改 primary SceneCandidate？ **NO**
8. 是否存在直接模型调用替代 xhnovel pipeline？ **NO**

第 7、第 8 题为 NO，实验未被判作废。因正式 scout 未跑完，**不给出 DOMAIN_VERDICT**。

## Blocker

原生入口在本环境失败：

```text
xhnovel-pipeline research-novel experiments/xuanhuan-domain-v1/experiment-A1.json \
  --scout-model gpt-4.1 --work-dir ...
FAIL: E-MODEL-CREDENTIAL: OPENAI_API_KEY is not set
```

`OpenAIResponsesClient` 只读取 `OPENAI_API_KEY` 并请求 `https://api.openai.com/v1/responses`。环境中无此密钥。按执行合同 0.3：**停止并报告 blocker，不用临时 runner 绕过**。

恢复方法：配置 `OPENAI_API_KEY` 后执行

```bash
SCOUT_MODEL=gpt-4.1 experiments/xuanhuan-domain-v1/run_native.sh
```

该脚本只调用 `xhnovel-pipeline research-novel` 与 `xhnovel-pipeline validate all/scene/evidence/export`。

## 已冻结材料

| 项 | 值 |
| --- | --- |
| 语料 | `fixtures/xuanhuan-domain-v1/chapters/chapter-01.txt` … `chapter-08.txt` |
| 标题 | 《灰河司灯》（原创，非商业全文） |
| 汉字（CJK） | 72534 |
| 权利 | `USER_AUTHORIZED_LOCAL_COPY`；可存全文；可送外部模型；不可导出摘录 |
| 质量 | `USER_VERIFIED_COPY` + `COMPLETE` |
| Gold | `fixtures/xuanhuan-domain-v1/gold-scenes.jsonl` |
| Gold 计数 | A=10, B=8, HN=10 |
| 相邻独立组 | G1 A-03/A-04；G2 A-05/A-07；G3 B-01/B-02；G4 B-04/B-05 |
| Spec | `experiments/xuanhuan-domain-v1/experiment-{A1,B,A2}.json` |
| A1 brief | 寻找对象控制转移、争夺、阻挡或权限变化导致角色后续行动空间改变的场景。重点区分物理持有、所有权、使用权限以及禁制或精神绑定。 |
| B brief | 寻找交易、承诺、债务、委托或宗门关系形成持续义务，并因此改变角色未来行动空间的场景。区分已经完成的一次性交易、未被接受的提议和真正持续存在的义务。 |
| A2 brief | 与 A1 逐字相同 |
| spec 其余字段 | A1/B/A2 一致 |
| 原生窗口 | `window_chars=10000`, `overlap_chars=1800` → **10** 个 SceneWindow |
| 计划调用量 | 10×3 = 30（上限） |
| 语料冻结 commit | `53c304f` |
| gold/spec 冻结 commit | `5a42969` |

Gold 在任何 Scene Scout 模型调用之前冻结。标注 Agent 只读冻结章节，未读 scout prompt/schema/输出。

## 原生窗口探测（非候选生成）

在无模型密钥条件下，仅运行 `run_novel_ingestion` → `prepare_novel_evidence_bundle` → `build_scene_windows`：

- ingestion `SUCCEEDED`，8 章 READY
- concatenated stream 82811 字符
- 10 个原生 SceneWindow，长度约 8919–9956

未调用 provider，未产生 SceneCandidate。

## 指标（未计算）

A1/B/A2 尚未由 `research-novel` 产出 `catalog.json` / `scene-candidates.json`。recall、precision、separation、state fidelity、merge quality、rejection distribution、token usage 均待原生 run + `validate all` PASS 后，由 `evaluate_xuanhuan_domain.py` 只读计算。

## 分工记录

- Agent A：盲写 8 章连续原文（未读 scout prompt/schema/validator/merge）
- Agent B：只读冻结 corpus，写 gold JSONL
- Agent C：写 spec、探测原生窗口、封装 `run_native.sh`；因缺密钥未完成三次 research
- Agent D：评估脚本已就位，等待原生产物
- Agent E：确认无自写候选、无直连模型替代 pipeline；gold 先于模型调用冻结；A1/A2 brief 相同；窗口探测为 10，未丢弃失败 window（尚无 window execution）

## DOMAIN_VERDICT

**不给出。** 正式 scout 未完成。恢复密钥后应继续同一分支，不得改 prompt/schema/validator/merge 后再把结果算作本实验。
