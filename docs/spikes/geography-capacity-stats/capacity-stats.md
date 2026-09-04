# Capacity statistics — first Doupo geography pilot (zero model calls)

Scope: 24 accepted first-pass answers in `work/` (engineering pilot, pre-6cc794f ABI).
Runtime-only; not committed.

## Headline
- 573 raw records -> 191 unique payloads: **66.7% of emitted records are exact in-response duplicates**
- PLACE_MENTION 548 raw -> 166 unique (69.7% dup); SPATIAL_RELATION 25 raw -> 25 unique (0% dup)
- median raw/unit 19.5, mean 23.9, max 63; **no unit reached the 64 cap**
- pilot-wide: 548 mentions -> only **60 distinct place names** (9.1 mentions/name)
  - top: 斗气大陆 x113, 乌坦城 x69, 中州 x52, 加玛帝国 x49, 天墓 x34

## Dense units (raw >= 32)
| ordinal | raw | unique | note |
|---|---|---|---|
| 5 | 63 | 29 | world-intro window; worker reported ~83 direct candidates, omitted ~20 not machine-recorded |
| 4 | 52 | 17 | |
| 3 | 51 | 15 | |
| 7 | 38 | 6 | |
| 6 | 37 | 11 | |
| 1 | 36 | 14 | |

After in-response dedup every unit is far below 64 unique payloads.

## Emitted-span deciles (descriptive only)
0-10%:50 | 10-20:47 | 20-30:55 | 30-40:48 | 40-50:57 | 50-60:48 | 60-70:106 | 70-80:68 | 80-90:47 | 90-100:47

## Reading (against the pre-registered decision table)
Matches the row: **">64 is mostly duplicate payloads -> in-unit consolidation"**.
Consolidation + unchanged 64 budget would have left headroom in all 24 observed units.

## Caveats
- 3.6% novel-prefix sample; not random; early chapters are world-setting dense.
- Worker answers under prioritization instructions bias raw emission downward on dense units.
- Ordinal-5 omitted candidates are not in artifacts -> overflow manifest is missing from the answer ABI (supports the 65th-record probe design).
- These numbers cannot clear the whole work: dense-window gold experiments across the book remain required.
