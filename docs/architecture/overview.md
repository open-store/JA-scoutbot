# Architecture overview

Scout is one process: `scout_bot.py`. The Docker image starts it with `python scout_bot.py`. Slack connects over Socket Mode, so the bot does not expose a public webhook.

```
Slack (Socket Mode)
        │
 scout_bot.py
   slash handlers, app_mention, message (DMs)
        │
 command_parser.parse_command
   or nl_router.route_natural_language → build_command_from_routing
        │
 execute_scout_command
        │
 ┌──────┴───────────────────────────────────────────────┐
 │                                                      │
 queries/                                      scout/services/
  csat.py, errors.py, voc.py                    voc_service.py
  nps.py, pps.py                                product_feedback_service.py
  returns.py                                    tag_scope_service.py
  reviews.py                                    evidence_service.py
  richpanel.py
        │
 snowflake_client.get_connection
        │
 formatters.py
```

`command_parser.ParsedCommand` is the shared shape: command name, lookback days, filters, and a source label. Slash text and natural language both end up there.

## What each command calls

| Command | Function | Source label |
|---|---|---|
| `/csat` | `queries/csat.py` `run_csat` | Snowflake |
| `/voc` without a product | `queries/voc.py` `run_voc` | Snowflake |
| `/voc` with `product:` | `scout/services/voc_service.py` `run_product_voc` | Snowflake |
| `/errors` | `queries/errors.py` `run_errors` | Snowflake |
| `/nps` | `queries/nps.py` `get_full_nps_report` | Snowflake (KnoCommerce tables) |
| `/pps` | `queries/pps.py` `get_returning_pps` | Snowflake (KnoCommerce tables) |
| `/attribution` | `queries/pps.py` `get_attribution` | Snowflake (KnoCommerce tables) |
| `/returns` | `queries/returns.py` `run_returns` | Snowflake |
| `/reviews` | `queries/reviews.py` `run_reviews` | Okendo HTTP API |
| `/daily` | `queries/richpanel.py` `get_daily_report` | Richpanel HTTP API |
| `/help`, `/scout-help` | `formatters.format_help` | none |

`/pps` always asks for the returning-customer survey unless the slash text contains "new", "first", or "acquisition". That branch returns the not-available message. New-customer acquisition is `/attribution`.

`/daily` does not take a timeframe. The report date is today in `America/New_York`.

## Module map

Root modules are not a package. They are imported by path because the process runs with the repo root on `sys.path` (the image `WORKDIR` is `/app`).

| Path | Role |
|---|---|
| `scout_bot.py` | Slack Bolt app, command dispatch, health-check thread |
| `command_parser.py` | Slash text and the `ParsedCommand` window |
| `nl_router.py` | LLM classifier and keyword fallback |
| `formatters.py` | Slack mrkdwn for every command |
| `snowflake_client.py` | RSA key auth and query helpers |
| `tag_mapping.py` | `TAG_ID_TO_NAME` |
| `queries/` | SQL and HTTP clients |
| `scout/taxonomy/` | Tag groups and product aliases |
| `scout/models/` | Product-feedback dataclasses |
| `scout/repositories/product_feedback_repository.py` | Product-feedback SQL |
| `scout/services/` | Scope discovery, synthesis, evidence cleaning |
| `scout/tests/` | Unit tests. They mock external calls |

`bot_server.py`, `scout.py`, and `product_feedback.py` are leftover entry points. Nothing in the running bot imports them.

## VOC boundary

General VOC calls `run_voc()` directly.

Product VOC goes through `voc_service.run_product_voc`:

1. `product_feedback_service.get_candidate_scope` returns conversation IDs.
2. `queries/voc.run_voc` aggregates those IDs.
3. `product_feedback_service.get_product_feedback_for_scope` synthesises themes from the same IDs.

`queries/voc.py` does not import the product-feedback service. The product-feedback service does not import `voc.py`. Only `voc_service.py` imports both. The retrieval passes are described in [product VOC](product-voc.md).

## Tag scopes

`scout/taxonomy/tags.py` maps tag names to scopes. UUIDs live in `tag_mapping.TAG_ID_TO_NAME`. `tag_scope_service` turns a scope into a UUID set for SQL.

Current scopes: `product_feedback`, `returns`, `shipping`, `pricing_promos`, `order_status`, `web_errors`, `customer_support_experience`.

## Related

- [Natural-language routing](nl-routing.md)
- [Metric decisions](decisions.md)
- [Snowflake tables](../integrations/snowflake.md)
- [Deployment](../operations/deployment.md)
- [Roadmap](../roadmap.md)
