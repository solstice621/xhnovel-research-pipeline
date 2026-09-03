Extract only explicit geography observations from the supplied source text.

Allowed payloads:

1. `PLACE_MENTION`: a named place that the text itself mentions. `explicit_type` may be included only when the cited text explicitly calls it a city, continent, empire, mountain, sect location, secret realm, road, region, or another place type.
2. `SPATIAL_RELATION`: an explicit relation between two named places. `relation` must be one of `LOCATED_IN`, `PART_OF`, `NEAR`, `OUTSIDE`, or `CONNECTED_TO`.

Do not infer a hierarchy from fame, political ownership, travel sequence, common knowledge, or unstated geography. Do not resolve aliases. Do not merge same-name places. Do not create transitive relations. A character, organization, technique, race, item, or title is not a place unless the cited wording explicitly uses it as one.
