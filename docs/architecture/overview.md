# Architecture overview

Scout is one process: `scout_bot.py`. The Docker image starts it with `python scout_bot.py`. Slack connects over Socket Mode, so the bot does not expose a public webhook.

```
Slack (Socket Mode)
        │
 scout_bot.py
   slash handlers, app_mention, message (DMs)
        │
 scout.routing.command_parser.parse_command
   or scout.routing.nl_router.route_natural_language
        │
 execute_scout_command
        │
 queries/                         scout/integrations/
  csat, errors, voc, nps, pps       snowflake, okendo, knocommerce, richpanel
  returns, reviews
        │
 scout/formatting/
```

`route_natural_language` is where a future question-type router plugs in. See [question router](question-router.md). The PRD for those types is not in this repo.

`scout.routing.command_parser.ParsedCommand` is the shared shape: command name, lookback days, filters, and a source label. Slash text and natural language both end up there.

## What each command calls

| Command | Function | Source label |
|---|---|---|
| `/csat` | `queries/csat.py` `run_csat` | Snowflake |
| `/voc` without a product | `queries/voc.py` `run_voc` | Snowflake |
| `/voc` with `product:` | `scout/services/voc_service.py` `run_product_voc` | Snowflake |
| `/errors` | `queries/errors.py` `run_errors` | Snowflake |
| `/nps` | `queries/nps.py` `get_full_nps_report` | Snowflake (KnoCommerce tables) |
| `/pps` | `queries/pps.py` `get_returning_pps` | Snowflake (KnoCommerce tables) |
| `/pps_new` | `queries/pps.py` `get_new_pps` | Snowflake (KnoCommerce tables) |
| `/attribution` | `queries/pps.py` `get_attribution` | Snowflake (KnoCommerce tables) |
| `/returns` | `queries/returns.py` `run_returns` | Snowflake |
| `/reviews` | `queries/reviews.py` `run_reviews` | Okendo HTTP API |
| `/daily` | `scout/integrations/richpanel.py` `get_daily_report` | Richpanel HTTP API |
| `/help`, `/scout-help` | `scout.formatting.format_help` | none |

`/pps` always asks for the returning-customer survey. `/pps_new` asks for the new-customer survey (recommend score and open text). A natural-language question about new-customer PPS still reaches that report. New-customer acquisition is `/attribution`.

`/daily` does not take a timeframe. The report date is today in `America/New_York`.

## Module map

The process is `python scout_bot.py` with the repo root on `sys.path` (the image `WORKDIR` is `/app`). `pytest.ini` sets the same path for tests. There is no `sys.path` insert in the code.

| Path | Role |
|---|---|
| `scout_bot.py` | Live Slack entry point: Bolt app, command dispatch, health-check thread |
| `scout/routing/command_parser.py` | Slash text and the `ParsedCommand` window |
| `scout/routing/nl_router.py` | LLM classifier and keyword fallback |
| `scout/formatting/` | Slack mrkdwn, one module per command |
| `scout/integrations/snowflake.py` | RSA key auth and query helpers |
| `scout/integrations/okendo.py` | Okendo reviews HTTP API |
| `scout/integrations/knocommerce.py` | Shared KnoCommerce survey table helpers |
| `scout/integrations/richpanel.py` | Richpanel HTTP API for `/daily` |
| `scout/integrations/mcp.py` | Interface only, for a future MCP client |
| `scout/taxonomy/tag_ids.py` | `TAG_ID_TO_NAME` and `TAG_NAME_TO_UUIDS` |
| `queries/` | Command queries. `queries/returns.py` is its own module |
| `scout/taxonomy/` | Tag groups and product aliases |
| `scout/models/` | Product-feedback dataclasses |
| `scout/repositories/product_feedback_repository.py` | Product-feedback SQL |
| `scout/services/` | Scope discovery, synthesis, evidence cleaning, daily CSAT notes |
| `scout/llm.py` | Shared OpenAI client and model name |
| `scout/tests/` | Unit tests. They mock external calls |

`scout.py` (no `_bot`) was an unused entry point and is deleted. `scout_bot.py` is the one the image runs.

## VOC boundary

General VOC calls `run_voc()` directly.

Product VOC goes through `voc_service.run_product_voc`:

1. `product_feedback_service.get_candidate_scope` returns conversation IDs.
2. `queries/voc.run_voc` aggregates those IDs.
3. `product_feedback_service.get_product_feedback_for_scope` synthesises themes from the same IDs.

`queries/voc.py` does not import the product-feedback service. The product-feedback service does not import `voc.py`. Only `voc_service.py` imports both. The retrieval passes are described in [product VOC](product-voc.md).

## Tag scopes

`scout/taxonomy/tags.py` maps tag names to scopes. UUIDs live in `scout.taxonomy.tag_ids.TAG_ID_TO_NAME`. `tag_scope_service` turns a scope into a UUID set for SQL.

Current scopes: `product_feedback`, `returns`, `shipping`, `pricing_promos`, `order_status`, `web_errors`, `customer_support_experience`.

## Related

- [Natural-language routing](nl-routing.md)
- [Question-type router seam](question-router.md)
- [Metric decisions](decisions.md)
- [Snowflake tables](../integrations/snowflake.md)
- [Deployment](../operations/deployment.md)
- [Roadmap](../roadmap.md)
