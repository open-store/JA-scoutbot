# Scout

Scout is a Slack bot for the Jack Archer team. It answers operational questions about customer experience by querying Snowflake and a few third-party APIs, then posting the result back in Slack.

## What Scout can do

Timeframes are `L7`, `L30`, and `L180`. Slash commands default to `L7` when the timeframe is omitted. Filters look like `product:"anytime crewneck"`, `tag:shipping`, or `channel:email`.

| Command | What it returns | Data source |
|---|---|---|
| `/csat [L7\|L30\|L180]` | CSAT %, rated-conversation count, and change versus the prior window | Richpanel conversations in Snowflake |
| `/voc [L7\|L30\|L180] [product:"name"]` | Contact themes, volume, and channel mix. A product filter adds synthesised product feedback | Richpanel conversations in Snowflake |
| `/errors [L7\|L30\|L180]` | Share of tickets whose subjects match error keywords, plus CSAT on those tickets | Richpanel conversations in Snowflake |
| `/nps [L7\|L30\|L180]` | NPS by survey, with promoter, passive, and detractor mix | KnoCommerce surveys in Snowflake |
| `/pps [L7\|L30\|L180]` | Why returning customers came back, and what almost stopped them | KnoCommerce surveys in Snowflake |
| `/attribution [L7\|L30\|L180]` | How new customers found Jack Archer, and what brought them to the site | KnoCommerce surveys in Snowflake |
| `/returns [L7\|L30\|L180] [product:"name"]` | Return volume, type mix, top products, and refund notes. This is not a true return rate | Snowflake returns export |
| `/reviews [L7\|L30\|L180] [product:"name"]` | Review rating, sentiment, and sample quotes | Okendo API |
| `/daily` | Today's CS KPIs in New York time: tickets closed, first response, resolution, CSAT | Richpanel API |
| `/scout-help` or `/help` | Command reference | — |

`/scout` accepts the same command text, for example `/scout csat L30`.

## Ask in plain English

In a channel where Scout is present, @mention it. In a direct message, just type the question.

```
@Scout what are customers saying about the Clubhouse polo?
@Scout show me CSAT for the last 30 days
@Scout how are people finding us?
```

Scout classifies the question, runs the matching command, and replies in the thread. When it is not sure, it still runs its best guess and says so, so you can rephrase or use a slash command. Details are in [natural-language routing](docs/architecture/nl-routing.md).

## How it works

```
Slack (Socket Mode)
        │
 scout_bot.py          slash commands, @mentions, and DMs
        │
 scout/routing/        slash text, or plain English → the same command
        │
 queries/              command queries (returns stays its own module)
 scout/integrations/   Snowflake, Okendo, KnoCommerce, Richpanel
        │
 scout/formatting/     Slack message
```

The product-VOC path has one extra rule: `voc_service.py` is the only module that joins product-feedback scope to VOC aggregates. See [architecture overview](docs/architecture/overview.md).

## Local development

Production runs on Python 3.11. Tests must pass on 3.11. Python 3.12 is tolerated locally.

```bash
pip install -r requirements-dev.txt
cp .env.example .env
python scout_bot.py
```

Fill in `.env` locally. Never commit it.

## Configuration

Variable names and what each one is for are in [configuration](docs/operations/configuration.md). That page has no secret values.

## Tests

```bash
python -m pytest scout/tests/ -v
```

Mock Snowflake, Slack, and every other external service. Do not add tests that call live systems.

## Deployment

Scout runs on the Google Compute Engine VM `scout-bot-vm` under systemd (`scout.service`). Redeploy by building and pushing the container image, then pulling it on the VM and restarting the service. The full procedure is in [deployment](docs/operations/deployment.md).

## Project layout

```
scout_bot.py          Slack entry point (scout.py is not used)
scout/routing/        Slash parsing and natural-language routing
scout/formatting/     Slack messages, one module per command
scout/integrations/   Snowflake, Okendo, KnoCommerce, Richpanel; MCP is interface only
scout/taxonomy/       Tag groups and tag UUID map
queries/              Command queries, including queries/returns.py
scout/                Product-feedback models, repository, services, tests
docs/                 Documentation
```

## Documentation

The index is [docs/README.md](docs/README.md).

## Security

Report vulnerabilities privately. See [SECURITY.md](SECURITY.md).
