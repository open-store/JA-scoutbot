# CLAUDE.md — Scout Slackbot

Agent rules for working on Scout. Read this before changing code. Deployment, configuration, data sources, and the roadmap live under [docs/](docs/README.md).

Scout is a Slack bot for the Jack Archer team. The live entry point is `scout_bot.py` (Socket Mode). Users run slash commands or ask in natural language in DMs and @mentions.

## Architecture contracts

```
Slack (Socket Mode)
        │
 scout_bot.py
        │
 command_parser.py → nl_router.py
        │
 queries/                         scout/services/
   csat, voc, errors, returns       voc_service.py
   nps, pps                         product_feedback_service.py
   reviews, richpanel               tag_scope_service.py
                                    evidence_service.py
        │
 snowflake_client.py
        │
 formatters.py
```

- `queries/voc.py` does not import `product_feedback_service`. `scout/services/voc_service.py` is the only coordinator between VOC aggregates and product feedback. Do not break that boundary.
- Root `tag_mapping.py` holds `TAG_ID_TO_NAME`. It is imported by `queries/voc.py`, `scout/repositories/product_feedback_repository.py`, and `scout/services/tag_scope_service.py`. `scout/taxonomy/tags.py` groups tag names and does not import `tag_mapping`. Consolidating the UUID map into `scout/taxonomy/` is planned tech debt. Do not change tag-group membership in a cleanup, because that changes VOC output.
- `bot_server.py`, `scout.py`, and `product_feedback.py` are dead entry points. Do not modify them or wire new behaviour through them.
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

1. `queries/X.py` — Snowflake SQL, or the third-party call if it is not in Snowflake yet.
2. `formatters.py` — `format_X()`.
3. `command_parser.py` — accept the command name.
4. `scout_bot.py` — slash handler that calls the query and formatter.

Keep third-party HTTP clients in `queries/` for now.

## Code style

- Production image is Python 3.11. Tests must pass on 3.11. Python 3.12 is tolerated locally.
- No `type: ignore` comments. Fix the types.
- `requests` is imported by `queries/reviews.py` and `queries/richpanel.py` but is not pinned in `requirements.txt`. It is installed only because `snowflake-connector-python` depends on it. Pin it if you add new code that imports it.
- All formatters stay in `formatters.py` until a split lands.
- `nl_router.py` and `scout/services/product_feedback_service.py` construct `OpenAI()`, which reads `OPENAI_API_KEY`. If the key is missing, natural-language routing falls back to keywords. Slash commands still run.

## Tests

```bash
python -m pytest scout/tests/ -v
```

Tests live in `scout/tests/`. Mock Snowflake, Slack, and every HTTP API. Do not commit tests that need live credentials.

## Secrets

Environment variables only. Production reads `/etc/scout.env` on the VM. Local development uses a gitignored `.env`. Never commit keys, tokens, customer data, or internal IDs. If a secret leaks, follow [SECURITY.md](SECURITY.md).
