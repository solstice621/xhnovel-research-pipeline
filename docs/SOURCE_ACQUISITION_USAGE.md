# 宿主原文获取工具使用说明

实现入口：[scripts/source_acquisition.py](../scripts/source_acquisition.py)。
从仓库根目录使用 Python 3.11+；依赖沿用项目环境，不新增模型 SDK 或产品 CLI。
本工具不打入 wheel。C1 的总请求超时目前针对 macOS/POSIX 主线程实现。

研究任务中的宿主应自动调用这些步骤，参见
[自动原文获取工作流](SOURCE_ACQUISITION_WORKFLOW.md)。以下配置与命令是宿主
执行接口，不要求用户逐项手动填写；原生 Scene 和 Observation 分别使用对应分支。

## 1. 已实现范围

- C1：固定有限目录、单请求、实际请求间隔、每跳重定向检查、有限重试、跨进程冷却。
- C4：既有章节目录、完整 TXT、EPUB；整本文件通过原生 adapter 发现章节和阅读序。
- 原始文件/响应、attempt、派生正文、accepted 提交记录；崩溃恢复和文件闭包验证。
- 完整性检查、固定质量样本、显式审查记录、多值标题匹配与多对多章节对齐。
- 有界 Levenshtein 差异统计，独立封存、原生 Handoff 准备和原生预冻结。

C2 仍是条件扩展：配置可记录独立授权引用，但 acquire 不会启动浏览器、
调用 AppleScript 或降级到 C1。当前只实现明确拒绝和隔离边界，
不是已完成浏览器传输。S5 的真实标签/回传测试不计为已通过。

## 2. 输入配置

文件引用统一是 path + sha256，摘要格式为 sha256: 后接 64 位小写十六进制。
相对引用从引用文件所在目录解析。可用 shasum -a 256 核对文件字节；
普通 JSON 文件允许空白排版，引用摘要仍针对原始文件字节。
config/catalog/审查记录拒绝未知字段，不能附加 Lead hints 或私有 request 字段。

示意配置如下；示例摘要占位符需要替换为实际摘要，不能直接用于运行：

~~~json
{
  "format_version": "source-acquisition-v1",
  "run_dir": "run",
  "work": {"title": "作品名", "author": "作者", "language": "zh"},
  "source": {
    "id": "source-a",
    "channel": "C4",
    "scope_url": "https://example.org/book/work/",
    "edition_status": "UNKNOWN",
    "edition_label": "来源版本及已知限制",
    "extractor": {
      "kind": "TXT",
      "title_selector": null,
      "body_selector": null,
      "exclude_selectors": [],
      "strip_leading_title": false
    },
    "browser_authorization": null
  },
  "attestation": {"path": "operator-attestation.json", "sha256": "<actual-sha256>"},
  "catalog": {"path": "catalog.json", "sha256": "<actual-sha256>"},
  "limits": {}
}
~~~

standing operator attestation 必须是已有有效声明，工具不创建或重签。
edition_status 不因可访问或能下载而自动变为 OFFICIAL/UNOFFICIAL_COPY。
来源 URL scope 必须是明确的作品路径，以 / 结尾；不接受凭据、fragment、路径穿越。
C4 的 entry.url 可以为 null；存在 URL 时仍核对 scope。

HTML extractor 使用 kind=HTML，title_selector/body_selector 是单个 tag、#id、
.class、tag#id 或 tag.class，exclude_selectors 是同样形式的列表。
要求标题/正文选择器各唯一命中，HTML 结构完整；不执行脚本或修复段落顺序。
不支持的结构应调整固定提取规则并新建 source-run，而不是关闭校验。
TXT 首行为原题，其余内容为正文；只接受严格 UTF-8（允许 BOM）。

limits 可指定以下整数，其他字段拒绝：

| 字段 | 默认 |
|---|---|
| min_gap_seconds | 5 |
| slow_start_gap_seconds / slow_start_requests | 10 / 20 |
| request_timeout_seconds | 30 |
| max_response_bytes | 2000000，每个获取条目 |
| max_input_bytes | 500000000，本地完整 TXT/EPUB |
| max_attempts_per_entry | 3，每次实际请求包括重定向都计数，跨重启累计 |
| max_redirects | 5，同时受每条目总请求数上限约束 |
| max_run_seconds | 1800 |
| consecutive_transport_failures | 3 |
| no_commit_seconds | 300 |

冷却基于持久化 HTTP 尝试和状态共同恢复。STARTED 存在而结果未写入时，
按“原请求 timeout + gap”保守等待，不能通过进程重启绕开节流。
transport 复用原生单跳、公共地址校验及固定 IP 连接；不更改系统代理，
不自动切换代理、镜像或浏览器。压缩响应目前明确拒绝，保留原字节及原因。

## 3. 固定目录

catalog 包含 format_version、entries、chapters、assessments。
entries 顺序必须与 chapters 的 entry_keys 展开顺序完全一致，无遗漏和重复。

~~~json
{
  "format_version": "source-acquisition-v1",
  "entries": [
    {
      "key": "p1",
      "url": null,
      "import_path": "0001.txt",
      "expected_title": "第一章 原始标题"
    }
  ],
  "chapters": [
    {
      "key": "c1",
      "title": "第一章 原始标题",
      "entry_keys": ["p1"],
      "role": "MAIN"
    }
  ],
  "assessments": {
    "identity": {"status": "UNRESOLVED", "reason": "待核实作品身份", "evidence": []},
    "whole_work": {"status": "UNRESOLVED", "reason": "待核实完整作品范围", "evidence": []},
    "catalog_coverage": {"status": "UNRESOLVED", "reason": "待核实目录覆盖", "evidence": []},
    "chapter_relationships": {"status": "UNRESOLVED", "reason": "待核实拆章及附属材料", "evidence": []},
    "text_integrity": {"status": "UNRESOLVED", "reason": "待排查缺段和分页", "evidence": []},
    "dom_order": {"status": "UNRESOLVED", "reason": "待确认文字阅读顺序", "evidence": []}
  }
}
~~~

未完成的来源仍可有界采集/导入，但不能 seal/prepare。
expected_title 或 chapter.title 可以在待核实目录中为 null；
最终封存要求逻辑章节标题明确且覆盖依据通过。
role 为 MAIN 或 SUPPLEMENT，不覆盖原生 chapter_kind 分类。
一个逻辑章节多页时明确列出全部 entry_keys；正文只按该已核实顺序拼接，
并保留逐页派生边界。

每个 PASS assessment 必须说明原因并引用有摘要的依据文件。
这些仍是宿主对来源的判断；程序验证引用与物理覆盖，不冒充全文真实性证明。

整本 TXT/EPUB 导入要求原生发现的条目数量和逐项标题与固定目录一致；
包括前言、附属材料等条目，不能通过按位置猜配跳过差异。
EPUB 的提取器应针对其实际 XHTML 结构配置为 HTML。
原始完整文件一并保留，accepted 复核会从该原始文件重新验证章节字节。

## 4. 获取与恢复

~~~bash
python scripts/source_acquisition.py inspect <config.json>
python scripts/source_acquisition.py import-local <config.json> <chapter-directory-or-book-file>
python scripts/source_acquisition.py acquire <c1-config.json>
python scripts/source_acquisition.py status <run-dir>
~~~

inspect 不请求网络、不创建 run-dir。acquire/import-local 首次调用会封存
config/catalog/attestation/依据文件；再次运行同一配置即可恢复。
目录或提取器改变必须新建 run-dir，不能覆盖原 run。
LOCAL_DERIVED_IMPORT 表示旧派生章节，不能充作历史 HTTP 原始响应。

status 的 0 退出码只说明成功读取状态。ENTRIES_ACQUIRED 仅表示目录条目均有
有效提交，不是完整作品声明或研究完成。来源变化、缺章和冷却另外显示。
有效 C1 提交不足 5 个、停滞或暂停时不编造 ETA；吞吐观察使用真实墙钟区间。

退出码：0=该命令后置条件完成；2=输入或完整性错误；4=缺口、未解决核验或暂停。
这些不是原生 executor 退出码。明确的访问拒绝、提取错误、缺页、版本变化
不会在循环里无限重试；保留该 run，通过有依据的新配置处理后续来源版本。

## 5. 核验、比较与人工/宿主审查

~~~bash
python scripts/source_acquisition.py verify <run-dir>
python scripts/source_acquisition.py review-template <run-dir> --output <review.json>
python scripts/source_acquisition.py compare <left-run> <right-run> --output <comparison.json>
python scripts/source_acquisition.py compare <left-run> <right-run> --alignment <alignment.json> --output <comparison.json>
python scripts/source_acquisition.py verify <run-dir> --review <review.json>
~~~

review-template 只生成 UNRESOLVED 待审项。审查人填写真实身份、UTC 时间、
限制说明及各样本/异常的 verdict；PASS 必须有内容绑定的依据文件。
不要用模板批量填 PASS。任一章节新增或改变，旧 review 的 view_sha256 将失效。
正文重复是硬性阻断，审查意见不能消除复制一章冒充全书的问题。
保真度样本由目录独立决定；专名、Lead hints 和预期事件不参与抽样。

比较默认只输出标题的多值候选，不自动确认同章。alignment 格式：

~~~json
{
  "format_version": "source-acquisition-v1",
  "left_view_sha256": "<left-view-digest>",
  "right_view_sha256": "<right-view-digest>",
  "groups": [
    {
      "left": ["c1", "c2"],
      "right": ["other-c1"],
      "assessment": {"status": "PASS", "reason": "<same-chapter-basis>", "evidence": []}
    }
  ]
}
~~~

上例 evidence 必须补上真实引用才会通过。分组禁止重复使用章节或改变阅读序。
比较只统一 NFC/空白，返回 edit_distance 和差异率的整数分子/分母；
没有可信底本时不是错误率。比较计算超过单组 2000 万 DP 单元时返回
COMPARISON_BUDGET_EXCEEDED，不用近似值伪装精确距离。
两镜像一致不能代替覆盖/保真度审查。

## 6. 封存、Handoff 与预冻结

~~~bash
python scripts/source_acquisition.py seal <run-dir> --review <review.json> --output <prepared-sources-dir>
python scripts/source_acquisition.py prepare <sealed-source> <planning-input.json> --phase0-root <phase0-root>
python scripts/source_acquisition.py freeze <sealed-source> <handoff.json> --research-root <research-root> --phase0-root <phase0-root>
~~~

sealed-source 是 seal 输出的摘要命名目录。它包含独立副本和全部核验依据，
没有指向活动原文文件的硬链接。prepare 每次都会重新回放封存闭包。

planning-input 只接受下列结构，不接受用户手填 source_declaration/COMPLETE：

~~~json
{
  "format_version": "source-acquisition-v1",
  "brief": {"path": "<existing-sealed-brief.json>", "sha256": "<actual-sha256>"},
  "leads": {"path": "<explicit-compatible-leads-list.json>", "sha256": "<actual-sha256>"},
  "planning": {
    "root": "<existing-planning-root>",
    "receipt": {"path": "<planning-compilation-receipt.json>", "sha256": "<actual-sha256>"}
  }
}
~~~

只有没有 Phase -1 lineage 的旧式 Brief 才使用 planning=null；
这种输入不声称 Phase -1 规划闭包。工具保留整个显式 Lead 列表，
不为强行匹配来源而悄悄删除不兼容条目。已有 attestation 原样复制，
rights 由原生 builder 注入。

freeze 直接使用原生 CAS 解析的完整 spec，在 research-root/ingestion
运行原生 ingestion 并逐章节核对 CAS 字节。native PARTIAL 的真实状态保留；
只有已解释的未知编号警告可继续，硬性排序/覆盖问题不可继续。
source-freeze-receipt.json 是宿主源核对记录，不是研究成功收据。

然后使用现有原生流程：

~~~bash
xhnovel-pipeline execute-handoff <handoff.json> --executor agent-files --work-dir <research-root>
xhnovel-pipeline validate all <emitted-catalog.json> --store <native-objects-dir>
~~~

exit 3=WAITING_FOR_AGENT；完成原生任务后再次执行同一命令。
FAILED/INTERRUPTED 后按原生规则显式 --retry，原始拒绝答案和 receipt 不得删除。
最终 SceneCandidate 仍是 DRAFT/UNVERIFIED，validate all 不证明语义解释为真。

## 7. 观察研究接入

观察研究使用 `prepare-generic S INPUT --research-root R` 和
`freeze-generic S HANDOFF --research-root R --work-dir W`。
INPUT 的严格字段、campaign 来源事件及后续执行见
[观察研究分支](SOURCE_ACQUISITION_WORKFLOW.md#4-观察研究分支)。
这两个命令复用封存核验及原生 builder/ingestion，不接收新的语义 prompt，
也不把观察输入转为 Scene 输入。准备记录按 Handoff 保存在
R/source-acquisition/，预冻结记录位于 W/source-freeze-receipt.json。
后续 campaign 必须使用同一 W；准备和冻结均不代表原生执行成功。

## 8. 版本边界

每个 source-run 绑定此宿主脚本的确切字节摘要，封存内容也保留这项绑定。
更新脚本后，不允许拿新实现静默恢复或重解释旧提交；保留旧版本脚本及运行，
或把原始材料作为明确的新来源版本重新导入并核验。
核心编译器代码、schema 和全书窗口规则均未修改。宿主 Skills 已衔接自动获取和
后续原生执行，工作流范围及停止条件见前述共享流程。
