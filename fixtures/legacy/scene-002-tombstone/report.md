# report — SCENE-2026-08-29-002「墨家夺人」（PILOT；邻接线索）

```yaml
effective_status: ADJACENT_CLUE
evidence_eligible: false
confirmed_count: 0
adversarial_fixture: FAIL
reproducibility: INCONCLUSIVE
qualification_credit: NONE
h_a: UNKNOWN
claims_ref: claims.yaml
```

## 结论

这个案例可以留作桥段线索，不能作为机制证据，不能确认 H-A，也不能推进 EC-1～EC-4。

RQ-002 要的是：玩家想拿别人当前持有的**物品**，直接 Take 不成立之后还能干什么。本桥段没有打中这件事。对象是人；C-1 未决，没有确认的 `current_holder`；没有 contested Take；没有转移尝试期间可观察的阻止/夺回结算。

邻接线索（不是已决机制）：captivity；handover 窗口（仅 D 级 branch lead）；护送与对峙分工（Researcher notes，其中「某人=青鳞」「白牙=殿后」最多 INFERRED）。

H-A 保持 UNKNOWN。

C-1 在 `evidence.yaml` 的 `scene.timeline.branches` 并列 A（已到手）与 B（未到手）。「交接未完成即被截」只是 D 级 branch lead，不是场景主定义。

## 证据状况

- `adversarial_fixture: FAIL`（INFERRED 2→8、UNKNOWN 18→10）。
- `reproducibility: INCONCLUSIVE`（RUN-B 原文未保存、截断 hash、材料在 `/tmp`、无页面 hash、无模型/提示/参数/manifest）。
- `qualification_credit: NONE`。不得累计两次 PASS。
- 有效 CONFIRMED 计数为 **0**。本目录是失败 scene 的 **tombstone**：`claims.yaml` 无 live 行；隔离原文只在 Git 历史。`scene-facts.md` 由 `generate_scene_facts.py` 生成。
- SRC-01 已拆成 SRC-01-PAGE（B）与 SRC-01-SNIPPET（D）。`isolation_status: SUPERSEDED` 期间不得有 ACTIVE FactClaim；合法重跑后可改为 CURRENT，不因 scene_id 永久锁死。
- SRC-11～SRC-15 无新隔离提取，只作 Researcher notes。

## 机制映射（不要统称「分析假设」）

- **EC-1 UNKNOWN**：本案例未证明墨承是 runtime `current_holder`，也未证明死后按所在地派生。
- **EC-2 携带 NOT_A_GAP**：M-1 已有取得后 `MoveActivity(venue_exit)`。途中重新取得仍是 UNKNOWN，且本案例不是该测试。
- **EC-3 REJECTED_BY_CONSTRAINT**：通用活动失效位撞上已排除的 `interrupted`。
- **EC-4 UNKNOWN**：不得 COVERED。只有一次冻结攻击；`PROBE-01` 未冻结。

玩家操作链与 NPC/世界时序已经分开。「任何 SEIZE() 都会压掉十步」仍是稻草人。

## 下一例与主线

RQ-002 下一例必须同时满足：真正物品；`physical_parent` 显示由具身角色握持/携带；抵抗针对同一次未结算转移；**确实发生 contested Take**（两人对同一件实际握持物施加相反控制）。纸面 trace 核心是 `TakeItemActivity`、到期重检、抵抗时序与三项物品状态的原子结算。`MoveActivity` 只在案例确实包含移动携带时加入。物品裸放在台上、`current_holder=auction_house`、守卫在玩家拿取时攻击，不是命中例。

产品直接下一步仍是 A1。`CURRENT_STATE.md` 未改。
