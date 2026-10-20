# Roadmap

Status of the work that used to live in the root README and in the agent notes. Updated from the code on `main` after the history rewrite.

## Shipped

| Item | Where it lives |
|---|---|
| NPS by survey | `/nps`, `queries/nps.py`. Reads Snowflake, not the KnoCommerce HTTP API |
| Returning-customer post-purchase survey | `/pps`, `queries/pps.py` |
| New-customer post-purchase survey | `/pps_new`, `queries/pps.py` `get_new_pps` |
| New-customer attribution | `/attribution`, `queries/pps.py` |
| Daily CS KPIs | `/daily`, `scout/integrations/richpanel.py` |
| Natural-language routing for NPS, PPS, attribution, returns, and reviews | `scout/routing/nl_router.py` |
| Product VOC with a shared conversation scope | `scout/services/voc_service.py` |

A separate `/kno` or `/survey` command is not needed. Those questions are `/nps`, `/pps`, `/pps_new`, and `/attribution`.

## Still open

### Returns detail

`/returns` reports volume, the cancellation / exchange / straight-return mix, top products, and top refund notes. It does not report a true return rate.

- Profile `ANALYTICS.RETURN_REDO` with `python -m scout.returns.discovery`. Redo is the authority for returns, outcomes, reasons, and comments. See [returns data](architecture/returns-data.md).
- Return and exchange rates need Redo units over eligible delivered units from `JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES`, joined on Shopify order and line IDs. Both relations are named, not yet verified for Scout's role.

### Natural-language quality

Router validation, product/timeframe/topic preservation through the keyword fallback, and golden cases (`scout/tests/test_nl_routing.py`) are in. See [natural-language routing](architecture/nl-routing.md).

- Make `Daily` routable if plain-English "today's CS report" should hit `/daily`.
- Confirm Scout's Snowflake role can read the product catalog source (`JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE`). Until it can, product names are matched as typed, with a caveat. See [product VOC](architecture/product-voc.md).
- Turn on order-linked and inferred attribution (`SCOUT_ORDER_LOOKUP=snowflake`) after Scout's role can read `JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS` and `FCT_SHOPIFY__ORDER_LINES` and the unverified column names in `scout/repositories/order_repository.py` are confirmed. Confirmed and unresolved attribution work without it.

### Taxonomy and structure

- Tag UUIDs live in `scout/taxonomy/tag_ids.py` (`TAG_ID_TO_NAME` and one `TAG_NAME_TO_UUIDS`). Do not change which tag names sit in which group; that changes VOC output.
- `scout_bot.py` is the only root Python module. `scout.py` is deleted. Formatters are `scout/formatting/`. Moving the rest of `queries/` under `scout/` is still optional.
- Products resolve against a catalog read from Snowflake (`scout/services/product_catalog_service.py`). Explicit aliases stay in `scout/taxonomy/products.py`.

### Known behavioural gaps (do not "fix" silently)

These change the numbers users see:

- Lookback windows differ across Snowflake commands, reviews, and the slash parser. See [decisions](architecture/decisions.md).
- `format_help` still says `/daily` is yesterday and lists Redo as a live source.
