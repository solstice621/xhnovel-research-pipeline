# Host search log (LEAD_ONLY) — Phase 0 run-004 from sealed Phase -1

Not evidence. Not injected into `discovery_brief` / Scene Scout / initial SceneCandidate.

Planning lineage (already sealed, not re-authored this run):

- Planning root: `.runtime/planning/run-004/`
- Intake `RIN-DC982B940C8ED4D2703B`
- Neutral frame `NRF-912354EF965F18588F20`
- Plan `XPL-8DE3A91D103721566E42`
- Brief `XBR-43C121A4ED04DCADE2F5` / `sha256:ea099e2dc765d87c92f769aaffea4ffec8e9bc39ca32c1c484fa71327d4713dd`
- Receipt `PCR-BF640547AC7578C6EB26`
- Compiler commit `53f76e95cc60970a166abedf7afc551998f80a80`

Host used the sealed Brief as the only formal search question. Plan seeds/diversity steered search only:

- families: 误入隐秘之地或遗存；危机中触发未知机缘；拾得非常规之物；遭遇残存意志或无名施助
- `min_works=5`, `min_interaction_families=4`, `max_initial_leads_per_work=2`
- budget `target_leads=16` (cap, not a quota to fabricate)

## Queries (open web)

- 斗破苍穹 药老 戒指 苏醒 维基百科
- 盘龙 林雷 盘龙之戒 德林·柯沃特 百度百科
- 武动乾坤 林动 符祖 石符 神秘石符
- 完美世界 石昊 柳神 至尊骨 石村 百科
- 神墓 辰南 神魔陵园 复活 百科
- 遮天 叶凡 九龙拉棺 泰山 青铜巨棺
- 大主宰 牧尘 九幽雀 夺舍 百科
- 星辰变 秦羽 晶石 内丹 奇遇 百科
- 遮天 辰东 玄幻 仙侠 百度百科 分类

## Work / scene locators retained

| Work | Lead-only locators | Host note |
|---|---|---|
| 斗破苍穹 | https://zh.wikipedia.org/wiki/斗破苍穹 ；https://baike.baidu.com/item/药尘/11056743 | Wikipedia: 戒指中药老吸斗气后苏醒、拜师、传焚诀。章节标题「第九章 药老!」来自先前本地目录，不写入 Brief。 |
| 盘龙 | https://baike.baidu.com/item/林雷·巴鲁克/3710353 | 百科称清洁宗堂得盘龙戒指，晨练袭击滴血认主后结识德林·柯沃特。 |
| 武动乾坤 | 章节目录 https://mdushu.read.qq.com/chapter/1000481126 （第5章 神秘石符）；问答/改编综述称山洞拾石符 | 条目大量混入电视剧；仍 LEAD_ONLY。 |
| 完美世界 | https://baike.baidu.com/item/完美世界/9446056 ；https://baike.baidu.com/item/石昊/9138725 | 百科称夺至尊骨后寄石村，遇柳神指点。 |
| 神墓 | https://baike.baidu.com/item/神墓/9947627 | 百科称自神魔陵园复活。不要把九龙拉棺安到神墓上。 |
| 遮天 | https://baike.baidu.com/item/遮天/7572 ；https://baike.baidu.com/item/三世铜棺/63631773 | 词条标题称玄幻，正文/起点常标仙侠。Host 保留并在报告注明类型边界；`scope.avoid` 为空故非硬排除。 |
| 大主宰 | https://baike.baidu.com/item/牧尘/7418807 ；https://www.baike.com/wikiid/8347939126480527637 | 九幽雀进化失败入体欲夺舍、黑纸镇压后血脉。灵路试炼本身偏事先约定，不作为当场奇遇主 Lead。 |
| 星辰变 | https://baike.baidu.com/item/星辰变/10988 | 百科称流星泪融入体内改变命运；另称得雷卫所留传承。 |

## Source resolution (separate from Lead truth)

- Official Qidian / bookresource remain VIP-walled → `PARTIAL`, ineligible. Not re-crawled this run.
- Pipeline User-Agent still must not be spoofed. Prior 403 on some catalogs stands.
- Reuse of `.runtime/exploration/run-003/input/chapters` (1663 txt, ~15.3MB, `edition_status=UNKNOWN`) is a **new** SourceDeclaration + **new** Novel Spec bound to the sealed encounter Brief. It does **not** resume run-003's ownership-brief 677 windows.
- No COMPLETE freeze for 盘龙 / 武动乾坤 / 完美世界 / 神墓 / 遮天 / 大主宰 / 星辰变 this run → those Leads stay `UNRESOLVED` in the denominator.
- Do not declare `UNOFFICIAL_COPY` merely because a storefront is unofficial.

## Explicitly not used

- run-003 handoff `EHO-E6C3528C9908B783DFAB` discovery brief (所有权/权限分离)
- 青莲地心火争夺（更像计划性夺宝）
- 大主宰灵路血祸/名额战（事先约定试炼）
- 把百科升成 SceneCandidate / KNOWN
