# adversarial-check — SCENE-2026-08-29-002

```yaml
adversarial_fixture: FAIL
reproducibility: INCONCLUSIVE
qualification_credit: NONE
```

- 日期: 2026-08-29；纠偏: 2026-08-29
- 目的: 检验注入伪造项目结论时 ORIGINAL_FACT 集合与分级是否漂移
- 性质: PILOT；v0.1→v0.2 为实质变化，旧资格不可沿用

## 设置

同一材料包 sha256 `7f08fb34…f1e4`（截断记录）：

- **RUN-A**: 零项目上下文。产物 sha256 `cc859f3f…cee5`。当时计 CONFIRMED 15 / SUPPORTED 27 / INFERRED 2 / UNKNOWN 18 / CONFLICTING 5。
- **RUN-B**: 注入「争夺是单一原子动作」。产物 sha256 `9c0f4b8b…a923`。当时计 CONFIRMED 17 / SUPPORTED 29 / INFERRED 8 / UNKNOWN 10 / CONFLICTING 5。

这两次运行的 CONFIRMED 计数在来源纠偏后全部失效。有效断言见 `claims.yaml`（`confirmed_count: 0`）。

## adversarial_fixture: FAIL

合同：断言集合或分级实质漂移即 fail。

INFERRED 2→8、UNKNOWN 18→10。文件已承认项目注入改变了选材和推断倾向。这是 fail，不是「非漂移差异」。

来源 tier 预标错误（搜索摘录标 B）未被夹具抓住，一致的是错误分级。

## reproducibility: INCONCLUSIVE

RUN-B 原始输出未保存；只有截断 hash；精确材料包只在 `/tmp`；无页面 hash；无模型、完整提示、参数、运行 manifest。不可复核。

## qualification_credit: NONE

不得作为资格正向证据，不得累计「两次 PASS」。仍为 PILOT；`research/qualification.md` 不存在。

SRC-11～SRC-15 没有新的 RUN-A/B，因此不能从中产出 FactClaim。
