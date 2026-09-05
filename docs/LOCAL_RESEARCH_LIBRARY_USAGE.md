# 本地章节原文库使用说明

`scripts/research_library.py` 是宿主侧工具，需在可信仓库 checkout 中运行。
它管理章节来源与原生产物的引用，不执行取书、模型调用或任务调度。
默认库根为 `~/Documents/xhnovel-library`，可用 `XHNOVEL_LIBRARY_ROOT` 或优先级更高的
`--library-root` 覆盖。以下 L、ID、P/R/W 和文件路径均由宿主从真实返回值填入。

## 1. 分配新研究目录

```bash
python scripts/research_library.py --library-root L init
python scripts/research_library.py --library-root L new-research request.json --key campaign-key --name '研究名称'
```

`request.json` 保存用户原始需求的 JSON 元数据，工具原样保存其字节；它不是原生
Intake、Brief、Definition 或预算，也不替代它们的冻结。新任务先分配库目录，再在
返回的原生根内运行已有规划流程。需求冻结后才开始查库或搜索。已有研究保留
原来的 P/R，无需移动冻结的需求材料；用同一个 request/key/name 恢复登记。

返回的 `result.record_id` 是本库 SHA-256 登记 ID；`result.record.research_id`
是目录用的短 ID，二者不能互换。后续命令的 research_id/source_id/execution_id/
product_id 参数均指 **record_id**，原生对象 ID 保持原有格式。

`result.paths` 返回 research_dir、phase0_base、campaign_root、reports_dir、
sealed_output、acquisition_root。Scene 的 P 使用 `phase0_base/<来源摘要>`，保证每个
来源的 preparation-input 独立；Generic 的 R 使用 campaign_root，支持多来源。
同一来源需要不同准备输入时分配独立的 P，不覆盖旧 preparation-input。

```text
L/
  acquisition/<source-run>/             # 原始材料、逐章断点、缺口清单
  sources/sealed/<版本摘要>/
    chapters/*.txt                      # 完整来源的逻辑章
    chapter-view.json                   # 原生获取工具的目录、页→章关系
    provenance/...                      # 原始取得材料和审查依据
    source-manifest.json
  research/<研究ID>/
    request.json
    phase0/<来源摘要>/                   # Scene 的准备根 P
    campaign/                           # Generic 的准备/审计根 R
    works/<作品ID>/<来源版本ID>/<执行ID>/
      native/                           # 原生 W，内部布局由编译器管理
      reports/                          # 本次执行的宿主报告
    reports/                            # 整项研究汇总
  records/sha256/...                     # 不可变登记和核验记录
  bindings/executions/...                # 同一原生 Handoff 的固定目录绑定
  index/library.sqlite                  # 可重建的元数据投影
```

库不生成整本 TXT。最初取得的 TXT/EPUB 仍是原始材料，不能因此删除；全书查找逐章
执行。书名、主题、日期、状态供索引筛选，不继续增加目录层级。

## 2. 查库和来源登记

```bash
python scripts/research_library.py --library-root L list-works --query '书名'
python scripts/research_library.py --library-root L list-sources --work-ref-id WORK_REF_ID
python scripts/research_library.py --library-root L verify SOURCE_RECORD_ID
```

列表的 `validation=NOT_CHECKED` 表示元数据候选，不能据此复用源。只有重新 verify
成功并与当前需求身份、版本和权限一致的封存源才可选择。多个来源记录可引用同一
source_revision；这是多次准入引用，不是重复下载。不同身份 basis 不按书名自动合并。

未命中合格来源时按 [现有获取流程](SOURCE_ACQUISITION_WORKFLOW.md) 继续：来源运行
放 acquisition_root 下，seal 输出放 sealed_output。旧材料可原址接入；S 是现有
`seal` 的真实返回路径。工具不迁移或复制已冻结路径。

由现有 `prepare` 或 `prepare-generic` 产生普通 Handoff 后登记来源：

```bash
python scripts/research_library.py --library-root L register-source S --protocol SCENE --handoff H --native-root P
python scripts/research_library.py --library-root L register-source S --protocol GENERIC --handoff H --native-root R
```

选择适用的一条。工具回放 seal、原生 Handoff、身份、来源字节及权限声明的连接，
不接受手填 COMPLETE。来源位于 L 时必须已经封存在上述固定 sources 路径；L 外
来源登记为 EXTERNAL_REFERENCE。获取实现 SHA 不兼容时失败，不重写旧 binding。
新版本只能明确重新取得/导入，或使用可信兼容 checkout 执行旧版本。

复用已登记源的新研究仍需在自己的 P/R prepare 新 Handoff，保留新的需求和
完整 Lead 集合；不能把原来 Handoff 的语义问题拿来替代新问题。此时无需为
同一源重复登记，allocate-execution 会验证新的 Handoff 与已登记来源的连接。
Scene/Generic 准入可分别登记同一个来源版本，普通 Spec 仍保持各自的分支契约。

## 3. 分配执行目录并继续原生研究

```bash
python scripts/research_library.py --library-root L allocate-execution RESEARCH_RECORD_ID SOURCE_RECORD_ID --handoff H --native-root P_OR_R --key execution-key
```

返回 `result.paths.work_dir` 为 W。key 是宿主首次固定的执行标签，不参与窗口或模型
指令。同一 Handoff/P_OR_R 的目录绑定不可更换；恢复沿用相同输入，不重新分配 key。
登记已存在的旧执行时显式传 `--work-dir OLD_W`，必须是 L 外实际存在的目录。
工具只登记旧路径，不移动 Catalog、CAS、回执或任务。

继续 [现有流程](SOURCE_ACQUISITION_WORKFLOW.md) 的 `freeze`/`freeze-generic`，再用
原生 `execute-handoff` 或 `observation-research execute` 和返回的 W 执行。
本地来源复用也需在 Generic campaign attach 实际材料、记录 SOURCE_STARTED，
prepare/freeze 成功才记 SOURCE_FINISHED(ELIGIBLE)。库不代写事件或重置预算。

WAITING_FOR_AGENT 后完成原生任务，再按同一命令继续。原生失败使用适用 retry，
中断使用适用 resume；库中目录或文件存在不代表研究成功。

## 4. 登记产物、查询和证据回查

```bash
python scripts/research_library.py --library-root L register-product EXECUTION_RECORD_ID --receipt NATIVE_RECEIPT
python scripts/research_library.py --library-root L list-products --research-id RESEARCH_RECORD_ID
python scripts/research_library.py --library-root L list-products --query 'race-mention-v1'
python scripts/research_library.py --library-root L read-product PRODUCT_RECORD_ID --limit 20 --offset 0
python scripts/research_library.py --library-root L show-evidence PRODUCT_RECORD_ID --record-id NATIVE_RECORD_ID
```

NATIVE_RECEIPT 使用原生工具的真实 receipt_path。Scene 回放整个原生执行历史和
validate-all 闭包，选择该回执的最终 merge 输出；Generic 回放指定原生 corpus，
不自动拿“最新结果”替换。失败、等待和伪造回执不能登记成成功产物。零结果是合法
成功执行，仍可登记及查询。产物登记失败时只重试登记，不重新运行已成功模型任务。

`read-product` 返回原生 record ID 和 source_spans，支持 `--query` 字面过滤及分页；
`show-evidence` 返回各字段原生 support 的规范化范围、章 ID 和封存文件入口。
Scene 为 DRAFT/UNVERIFIED，Generic 为 UNQUALIFIED；这些命令不提供新的语义保证。

```bash
python scripts/research_library.py --library-root L search-text SOURCE_RECORD_ID --query '检索词' --limit 20
```

原文搜索返回 TEXT_MATCH、章内 byte/codepoint 范围、截断标识和 next_offset。
这是阅读辅助，不改变 FULL_WORK，不把命中提示注入原生语义任务。默认不返回摘录；
search-text/read-product/show-evidence 的 `--include-text` 必须同时具备存储、模型
发送及摘录权限。禁止摘录时仍可得到偏移和文件引用，不能用查询输出绕过出口规则。
规范化 span 不是原始章节文件的同位字符范围，不做模糊引文修补。

## 5. 报告、状态与恢复

历史 TXT/EPUB 研究没有 source-acquisition 封存清单时，使用原址归档入口：

```bash
python scripts/research_library.py --library-root L register-external-execution RESEARCH_RECORD_ID --protocol SCENE --handoff OLD_H --native-root OLD_P --work-dir OLD_W
```

Generic 使用 --protocol GENERIC 和 OLD_R。必须已有真实原生执行历史；空目录或
只有准备好的 Handoff 不构成历史执行。该入口创建 external-execution 记录，随后
可使用相同 register-product/read-product/show-evidence 登记和读取已验证产物。
它不产生 source 记录，不进入可复用封存源列表，不为旧 TXT/EPUB 伪造章节目录。
此时证据返回原生 chapter_locator、store_root 和 source_artifact_id；没有封存的
独立章文件时 chapter_path 为 null，规范化 span 仍经原生 CAS/Catalog 精确回放。
现有原生验证器不兼容的历史版本会明确失败，不能用归档入口绕过其版本校验。

```bash
python scripts/research_library.py --library-root L register-report RESEARCH_RECORD_ID report.md --executions EXECUTION_RECORD_ID --products PRODUCT_RECORD_ID
python scripts/research_library.py --library-root L show RECORD_ID
python scripts/research_library.py --library-root L list-executions --research-id RESEARCH_RECORD_ID
python scripts/research_library.py --library-root L reindex
```

报告是 HOST_AUTHORED 内容，登记验证文件摘要和所引用执行/产物的真实归属，不
验证报告的语义结论。失败或等待报告可省略 --products；仍须引用真实执行。
`show` 重验实际对象；`verify` 追加含验证器摘要的回执。列表只提供元数据，执行
状态以 show/verify 回放返回的原生状态为准，历史 PASS 不能盖过当前损坏。

元数据列表每次从不可变登记刷新 SQLite 投影，避免陈旧或被改动索引遗漏关联。
`reindex` 还重验原生依赖并列出 UNAVAILABLE 和具体错误，不把损坏数据静默删除。
这是本地小规模库的实现选择，不是常驻全文数据库。

退出码：0 成功；1 校验/输入错误或可见的索引问题；2 IO/环境失败；4 明确未就绪。
原生退出 3/WAITING_FOR_AGENT 语义不变。工具不会自动运行外部模型。
登记与索引写入原子发布，断点重复相同输入可恢复；同一 Handoff 的不可变路径绑定
也随库备份，避免并发分配不同 W。不要只备份 SQLite；必须保留来源 provenance、
原生 P/R/W/CAS 和全部外部依赖，按原路径恢复后重验。
