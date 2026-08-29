# elements-mapping — SCENE-2026-08-29-002

```yaml
EC-1: UNKNOWN
EC-2_carry: NOT_A_GAP
EC-2_reacquire_in_motion: UNKNOWN
EC-3: REJECTED_BY_CONSTRAINT
EC-4: UNKNOWN
EC-5: ABSENT
EC-6: UNKNOWN
EC-7: UNKNOWN
h_a: UNKNOWN
```

映射基准: context-snapshot.md。结论是 Agent 提案。机器可读状态以 `claims.yaml` 的 `element_mapping` 为准。不要把四条统称为「分析假设」。

---

## EC-1 持有者失效后按所在地支配方派生 —— **UNKNOWN**

原作没有证明墨承是 runtime `current_holder`，也没有证明死亡后控制按所在地自动派生。所谓一次查询藏了领地、组织、继任和权利系统，未过原子门。本案例不能把这条写成已证实的缺口。不推进。

---

## EC-2 取得后的携物移动 —— **NOT_A_GAP**

M-1 已有：取得拍品后 `MoveActivity(auction_stage)` → 付款取物 → `MoveActivity(venue_exit)`。不缺携物移动。U-SW 截止在取得是证据范围，不是规则没有移动。

途中第三方如何对**明确当前持有者手里的物品**重新取得：仍是 **UNKNOWN**。本案例因 C-1 未决、对象是人、没有 contested Take，不能充当这次测试。

---

## EC-3 攻击另设活动失效位 —— **REJECTED_BY_CONSTRAINT**

与已排除的通用 `interrupted` 冲突。现行规则：攻击先改变失能、击退、位置、可达性，再由领域活动重检。白牙是否殿后最多是 Researcher note / INFERRED，也不能用来证明需要万能中断位。不推进。

---

## EC-4 干预实力门槛 —— **UNKNOWN**

不得标 COVERED。项目只冻结一次攻击（`hp 10 -> 7`）。`PROBE-01` 的明显更强/更弱仍是未冻结探知候选。嫣然警告不能把该候选写成已覆盖。

---

## EC-5 —— **ABSENT**（超出当前范围）

## EC-6 / EC-7 —— **UNKNOWN**（本案例分别为 0 案例 / 仅其他作品 D）

---

## 汇总

| Element | effective_status | 行动 |
|---|---|---|
| EC-1 | UNKNOWN | 不推进 |
| EC-2 携带 | NOT_A_GAP | 不新增 |
| EC-2 途中重取 | UNKNOWN | 需真正物品的 contested Take 案例 |
| EC-3 | REJECTED_BY_CONSTRAINT | 不推进 |
| EC-4 | UNKNOWN | 不得 COVERED |
| EC-5 | ABSENT | 只挂图 |
| EC-6 | UNKNOWN | 仍 0 案例 |
| EC-7 | UNKNOWN | 不进入统计 |

RQ-002 下一例硬门见 `RESEARCH-QUESTIONS.md`：必须确实发生 contested Take。
