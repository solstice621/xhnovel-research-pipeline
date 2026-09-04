Extract only explicit secret-realm observations from the supplied source text.

A secret realm is a bounded site that the cited wording itself presents as a distinct special spatial domain, such as a 秘境, 洞府, 遗迹, 禁地, 小世界, 古界, 古墓, 秘库, 传承之地, or a tower interior or sealed space. The classification must come from the cited wording, never from genre convention, fame, danger, rarity, or common knowledge. An ordinary city, mountain range, country, or residence is not a secret realm unless the text explicitly presents it as a bounded special site.

Allowed payloads:

1. `REALM_MENTION`: a named bounded site that the text mentions. `explicit_type` may be included only when the cited text explicitly names its type, such as `秘境`, `洞府`, `遗迹`, `禁地`, `小世界`, `古界`, `古墓`, `秘库`, or `传承之地`.
2. `REALM_ACCESS`: an explicit opening, entry, exit, closing, or sealing of a named bounded site. `access` must be one of `OPENED`, `ENTERED`, `EXITED`, `CLOSED`, or `SEALED`. `actor_name` may be included only when the cited text explicitly names who performs or undergoes the access; emit one record per explicitly named actor, and omit `actor_name` when the text names none. Emit an access record only when the text states the access as an actual occurrence, including narrated past events; a plan, rumor, prediction, or speculation is not an access.
3. `REALM_STAKE`: a named treasure, inheritance, hazard, or restriction that the text explicitly connects to a named bounded site. `stake_kind` must be one of `TREASURE`, `INHERITANCE`, `HAZARD`, or `RESTRICTION`, and `item_name` must be the name the text itself uses for the treasure, inheritance, hazard, or restriction.

Do not infer an access or a stake from a travel sequence, ownership, power level, or expectation. Do not resolve aliases. Do not merge same-name sites. A character, organization, technique, item outside a realm connection, or title is not a site. Zero records is a valid successful answer.
