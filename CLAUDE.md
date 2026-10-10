# CLAUDE.md — Scout Slackbot

Agent rules for working on Scout. Read this before changing code. Deployment, configuration, data sources, and the roadmap live under [docs/](docs/README.md).

Scout is a Slack bot for the Jack Archer team. The live entry point is `scout_bot.py` (Socket Mode). Users run slash commands or ask in natural language in DMs and @mentions.

## Architecture contracts

```
Slack (Socket Mode)
        │
 scout_bot.py          live entry point (not scout.py)
        │
 scout/routing/        command_parser.py, nl_router.py
        │
 queries/              scout/integrations/     scout/services/
   csat, voc, errors     snowflake, okendo       voc_service.py
   nps, pps, reviews     knocommerce, richpanel  product_feedback_service.py
   returns (own module)  mcp.py is interface only
        │
 scout/formatting/
```

- `scout_bot.py` is the process entry point. `scout.py` was unused and is deleted. Do not add another root entry point.
- `queries/voc.py` does not import `product_feedback_service`. `scout/services/voc_service.py` is the only coordinator between VOC aggregates and product feedback. Do not break that boundary.
- `scout/taxonomy/tag_ids.py` holds `TAG_ID_TO_NAME` and the one `TAG_NAME_TO_UUIDS` map. `scout/taxonomy/tags.py` groups tag names and does not import that map. Do not change tag-group membership in a cleanup, because that changes VOC output.
- `queries/returns.py` stays its own module. A later returns-rate and returns-feedback feature belongs there. Do not build it as part of a layout change.
- `scout/integrations/mcp.py` is an interface only. Do not implement MCP clients until the apps exist.
- A future question-type router replaces `scout.routing.nl_router.route_natural_language`. Do not invent question types. See [question router](docs/architecture/question-router.md).
- Tag scopes in code are `product_feedback`, `returns`, `shipping`, `pricing_promos`, `order_status`, `web_errors`, and `customer_support_experience`. See [overview](docs/architecture/overview.md).

## Where to read more

| Topic | Doc |
|---|---|
| Request flow and module map | [docs/architecture/overview.md](docs/architecture/overview.md) |
| Natural-language routing | [docs/architecture/nl-routing.md](docs/architecture/nl-routing.md) |
| Product VOC | [docs/architecture/product-voc.md](docs/architecture/product-voc.md) |
| Metric definitions | [docs/architecture/decisions.md](docs/architecture/decisions.md) |
| Snowflake, Richpanel, KnoCommerce, Okendo, Redo | [docs/integrations/](docs/integrations/snowflake.md) |
| Deploy, env vars, Slack app | [docs/operations/deployment.md](docs/operations/deployment.md) |
| Pending work | [docs/roadmap.md](docs/roadmap.md) |
| Secrets | [SECURITY.md](SECURITY.md) |

## New commands

Add a command in this order:

1. `queries/X.py` — the command query. HTTP or Snowflake access goes through `scout/integrations/`.
2. `scout/formatting/` — `format_X()`.
3. `scout/routing/command_parser.py` — accept the command name.
4. `scout_bot.py` — slash handler that calls the query and formatter.

## Code style

- Production image is Python 3.11. Tests must pass on 3.11. Python 3.12 is tolerated locally.
- No `type: ignore` comments. Fix the types.
- `requests` is pinned in `requirements.txt`. Dev-only `pytest` is in `requirements-dev.txt`.
- Formatters live in `scout/formatting/`, one module per command.
- OpenAI calls use `scout.llm.get_client()` and `scout.llm.MODEL` (`gpt-4.1-mini`). If `OPENAI_API_KEY` is missing, natural-language routing falls back to keywords. Slash commands still run.

## Tests

```bash
python -m pytest scout/tests/ -v
```

Tests live in `scout/tests/`. Mock Snowflake, Slack, and every HTTP API. Do not commit tests that need live credentials.

## Secrets

Environment variables only. Production reads `/etc/scout.env` on the VM. Local development uses a gitignored `.env`. Never commit keys, tokens, customer data, or internal IDs. If a secret leaks, follow [SECURITY.md](SECURITY.md).
