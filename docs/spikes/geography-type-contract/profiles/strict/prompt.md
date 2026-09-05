Extract only explicit geography observations from the supplied source text.

Allowed payloads:

1. `PLACE_MENTION`: a named place that the text itself mentions. `explicit_type` may be included only when the cited text explicitly calls it a city, continent, empire, mountain, sect location, secret realm, road, region, or another place type.
2. `SPATIAL_RELATION`: an explicit relation between two named places. `relation` must be one of `LOCATED_IN`, `PART_OF`, `NEAR`, `OUTSIDE`, or `CONNECTED_TO`.

Do not infer a hierarchy from fame, political ownership, travel sequence, common knowledge, or unstated geography. Do not resolve aliases. Do not merge same-name places. Do not create transitive relations. A character, organization, technique, race, item, or title is not a place unless the cited wording explicitly uses it as one.

Unit-local unique-payload rule:

- Inside this unit only, emit each exact canonical payload at most once.
- Two payloads are the same only when their canonical JSON bytes are identical.
- Do not merge aliases, synonyms, same-name distinct sites, or cross-unit facts.
- `PLACE_MENTION` rows that differ only by `explicit_type` remain distinct.
- Cite one smallest sufficient evidence set for the emitted unique payload.

Completion ABI:

Return `completion.status` as exactly one of:

- `COMPLETE`: you believe every eligible unique payload in this unit has been emitted. This is an unverified executor assertion, not a proof of semantic completeness.
- `OVERFLOW`: you know at least one additional eligible unique payload was omitted. Still an unverified executor assertion.
- `UNCERTAIN`: you cannot reliably judge completeness.

Do not treat a self-reported total count as gold evidence. Zero records plus `COMPLETE` is valid when the unit contains no eligible geography.

Type assertion decision rule:
- A type is an exact continuous source expression used to classify this place in the cited passage. Preserve that expression; do not normalize it to a synonym.
- A suffix inside a place name is not by itself a classification assertion. In "他来到了乌坦城", emit the place without a type. In "乌坦城是一座城市", the explicit type is "城市".
- Emit neither multiple synonymous types nor an extra untyped variant from the same mention occurrence. Different occurrences may explicitly assert different types; retain those different assertions with their own evidence.
- Include the classification clause in the evidence, not just an isolated type word or the name. Bind /explicit_type directly to an exact span containing the type expression.
- Distinguish organization actions from locative use in this passage. A name used only as an organization is excluded; locative use can support a named place. Generic descriptions without a named place are excluded.
