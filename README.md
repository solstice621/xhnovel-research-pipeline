# Xuanhuan Gameplay Probes

`xhnovel-research-pipeline` 现作为 **`xuanhuan-gameplay-probes` Skill 的独立权威仓库**。

旧的 Research Infrastructure / G0–G12 Pipeline 方向已经停止；原 `AGENTS.md` 与 `IMPLEMENTATION_PLAN.md` 已从当前主线删除，历史仍保留在 Git history 中，不应继续实施。

## 当前用途

这个 Skill 把经典玄幻桥段当作玩法试纸，用来探索：

- 玩家能否组合少量现有机制解决局部问题；
- NPC 与世界至少需要怎样作出基础、可观察的响应；
- 玩家行动怎样改变客观状态并产生新的选择；
- 哪些步骤由已有通用机制承接，哪些只是场景局部响应，哪些是真正缺口；
- 批量研究时如何用多个搜索／分析子 Agent 扩大案例覆盖，而不把研究重新做成基础设施平台。

核心工作合同只有 [`SKILL.md`](./SKILL.md)。本仓刻意不提供数据库、schema、validator、抓取器、ArtifactStore、资格系统或 G0–G12 实施计划。

## 在 Xuanhuan Sandbox 中使用

`solstice621/xuanhuan-sandbox` 将本仓作为项目级 Skill 挂载到：

```text
.agents/skills/xuanhuan-gameplay-probes
```

因此宿主项目的 `AGENTS.md`、GDD / DEC、`CURRENT_STATE.md`、SDS、`experiments/` 和实际实现证据继续定义项目事实；本仓只定义 Gameplay Probe 的工作方法。

## 历史

如果未来真实出现定时无人值守研究、数百网页批处理、材料缓存复用、多消费者或精确重放等需求，可以从 Git 历史中重新参考旧 Research Infrastructure 设计；在这些需求出现前，不恢复旧 Pipeline。
