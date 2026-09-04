# RESUME — 秘境研究断点说明（2026-09-04）

## 环境与分支
- 管线代码 worktree：`/private/tmp/xhnovel-generic.QqVudV`（分支 `codex/secret-realm-profile` @ `e27771b`，基于 PR #11 head `6cc794f`）
- 已 editable 安装（`pip install -e .`），CLI：`xhnovel-pipeline` / `xhnovel-extract`
- 研究工作区：`/private/tmp/xhnovel-secret-realm-research/`（全部运行态，不入库）
- 授权记录：`operator-authorization.txt`

## 《斗破苍穹》— 已完成，无需续跑
- spec：`doupo/novel-spec.json`（ingestion NING-E69CAE71428F31957153）
- run SUCCEEDED：`XRUN-14F19D57FA63D29CBA91`，corpus `CPS-E51BC95C48E837D06B2F`（408 条）
- 验证：`xhnovel-extract validate …` VALID；`xhnovel-pipeline validate all …/catalog.json --store …/objects` OK
- 语料：`doupo/generic-extraction/profiles/secret-realm-v1/extractions/XBLD-555A5E21E238D5663122/reductions/RRUN-B666392F42CDBD1170CF/corpus.jsonl`

## 《完美世界》— 断点：90/809 单元已答
- spec：`wanmei/novel-spec.json`（ingestion NING-516C3D14AF208A8F9A09；2010 章 ready，7 个序号缺口告警为 PARTIAL 来源，非缺失文件）
- 任务根：`wanmei/generic-extraction/agent-files/secret-realm-v1/e2f2c8dd51a60a6642c9/`
  - tasks/ 809 个任务；index/ 关键词位置索引；answers/ 已有 90 个答案（全部通过 check_answer.py）
- 批次清单：`batches-wm/batch-001..081.json`（每批 10 单元）；已覆盖：batch-001..008 全部 + 散布 10 个（见下）
- 作答规则：`agent-tools/RULES-WM.md`；工具：`agent-tools/locate.py` / `check_answer.py`

### 已覆盖批次之外的任务（10 个，来自被中断波次）
XUNIT-20FBA73B…, 213AAC0B…, 2175C2CD…, 21792B4F…, 23BC3E59…, 25B65D56…, 25FFF8FD…, 27EF6D75…, 2831FF7D…, 2B63CE72…

### 续跑步骤
1. 按批次继续派发子代理（提示模板：读 `agent-tools/RULES-WM.md`，执行 `batches-wm/batch-NNN.json`；幂等——答案已存在且通过 check 则跳过）。
2. 全部答案就绪后重跑同一命令（断点续跑，已答单元从 checkpoint 复用）：
   ```
   xhnovel-extract run /private/tmp/xhnovel-secret-realm-research/wanmei/novel-spec.json \
     --profile secret-realm-v1 --executor agent-files \
     --work-dir /private/tmp/xhnovel-secret-realm-research/wanmei
   ```
   - 退出码 3 = 仍有单元待答；退出码 2 = PARTIAL（按 checkpoint 中 `failed` 的 `E-GENERIC-EVIDENCE-MISSING` 修复答案——常见原因是必选组拆成了多个 binding，需合并为单一 binding 联合覆盖；参考斗破修复脚本思路）后重跑；0 = SUCCEEDED。
3. 冷进程验证：
   ```
   xhnovel-extract validate … wanmei/novel-spec.json --profile secret-realm-v1 --work-dir …/wanmei
   xhnovel-pipeline validate all …/wanmei/ingestion/ingestions/NING-516C3D14AF208A8F9A09/catalog.json --store …/wanmei/ingestion/objects
   ```

## 其他候选作品（未拉取，rights=UNKNOWN）
见 `leads-manifest.json`（遮天/斗罗大陆/武动乾坤/凡人修仙传等 12 部）。quanben.io 可达且含 `/n/fanrenxiuxianzhuan/` 等；下载脚本模板：`wanmei/download.py`（curl 版，含退避；quanben.io 限流时放慢至 ~1.2s/章）。
