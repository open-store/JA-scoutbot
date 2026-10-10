# Roadmap

Status of the work that used to live in the root README and in the agent notes. Updated from the code on `main` after the history rewrite.

## Shipped

| Item | Where it lives |
|---|---|
| NPS by survey | `/nps`, `queries/nps.py`. Reads Snowflake, not the KnoCommerce HTTP API |
| Returning-customer post-purchase survey | `/pps`, `queries/pps.py` |
| New-customer attribution | `/attribution`, `queries/pps.py` |
| Daily CS KPIs | `/daily`, `scout/integrations/richpanel.py` |
| Natural-language routing for NPS, PPS, attribution, returns, and reviews | `scout/routing/nl_router.py` |
| Product VOC with a shared conversation scope | `scout/services/voc_service.py` |

A separate `/kno` or `/survey` command is not needed. Those questions are `/nps`, `/pps`, and `/attribution`.

## Still open

### Returns detail

`/returns` reports volume, the cancellation / exchange / straight-return mix, top products, and top refund notes. It does not report a true return rate.

- Redo primary reason, secondary reason, and customer comments are not ingested. `REDO_API_KEY` is unused. Leave the API behind a feature flag until credentials exist. See [Redo](integrations/redo.md).
- True line-item return rate needs a join from `EXPORT_CSX__RETURNS` to all orders (`OS_ALL_ORDERS`).

### Natural-language quality

- Validate router JSON: allowed commands, allowed timeframes, default missing fields.
- Teach the keyword fallback to pick up a product name.
- Add a golden-case test module for the router. There is no `tests/test_nl_routing.py` yet.
- Make `Daily` routable if plain-English "today's CS report" should hit `/daily`.

### Taxonomy and structure

- Tag UUIDs live in `scout/taxonomy/tag_ids.py` (`TAG_ID_TO_NAME` and one `TAG_NAME_TO_UUIDS`). Do not change which tag names sit in which group; that changes VOC output.
- `scout_bot.py` is the only root Python module. `scout.py` is deleted. Formatters are `scout/formatting/`. Moving the rest of `queries/` under `scout/` is still optional.
- Product aliases are maintained in `scout/taxonomy/products.py`. A Shopify or CSV catalog is not wired up.

### Known behavioural gaps (do not "fix" silently)

These change the numbers users see:

- Lookback windows differ across Snowflake commands, reviews, and the slash parser. See [decisions](architecture/decisions.md).
- `format_help` still says `/daily` is yesterday and lists Redo as a live source.
