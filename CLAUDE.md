# CLAUDE.md — Scout Slackbot

This file gives you everything you need to work on Scout without re-discovering context. Read it before making changes.

---

## What Scout Is

Scout is a Slack bot for the Jack Archer team. It answers operational questions about the business by querying Snowflake and third-party APIs, then formatting the results into Slack messages. Users interact via slash commands (`/csat`, `/voc`, `/returns`, etc.) or natural language in DMs and `@mentions`.

---

## Architecture Overview

```
Slack (Socket Mode)
       │
  scout_bot.py          ← single entry point; all slash commands + NL routing
       │
  command_parser.py     ← parses raw input → ParsedCommand
       │
  nl_router.py          ← LLM-based intent classification (gpt-4.1-mini)
       │
  ┌────┴────────────────────────────────────────────┐
  │                                                 │
queries/                                    scout/services/
  csat.py                                     voc_service.py          ← product VOC coordinator
  voc.py                                      product_feedback_service.py
  nps.py                                      tag_scope_service.py
  returns.py                                  evidence_service.py
  reviews.py
  errors.py
       │
  snowflake_client.py   ← shared Snowflake connection (RSA key auth)
       │
  formatters.py         ← all Slack message formatting
```

**Key architectural rules:**
- `voc.py` does NOT import `product_feedback_service`. `voc_service.py` is the only coordinator between them.
- `tag_mapping.py` at root is imported by `scout/taxonomy/tags.py` — this is a known tech debt item (should be consolidated into `scout/taxonomy/`).
- `bot_server.py` and `scout.py` at root are **dead code** — superseded by `scout_bot.py`. Do not modify or reference them.

---

## Deployment

**Platform:** Google Compute Engine VM  
**Project:** `ja-scout-yt`  
**VM:** `scout-bot-vm`, zone `us-east1-c`  
**Machine type:** `e2-micro`  
**Image registry:** `us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest`

**How to redeploy after a code change:**

```bash
# 1. Build and push new image (run from repo root)
sudo docker build -t us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest .
sudo docker push us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest

# 2. SSH into VM and restart service
gcloud compute ssh scout-bot-vm --zone=us-east1-c --project=ja-scout-yt \
  --command="sudo docker pull us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest && sudo systemctl restart scout.service"
```

**Service management on VM:**
```bash
# Check status
sudo systemctl status scout.service

# View live logs
sudo docker logs -f scout-bot

# Restart
sudo systemctl restart scout.service
```

The service is managed by systemd with `Restart=always` — it auto-recovers from crashes and starts on VM reboot.

---

## Environment Variables

All env vars live in `/etc/scout.env` on the VM (mode 600, not in git). The `.env` file in the repo is the local dev copy — **never commit real values**.

| Variable | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-` bot OAuth token |
| `SLACK_APP_TOKEN` | `xapp-` Socket Mode token (needs `connections:write` scope) |
| `SLACK_CLIENT_SECRET` | App client secret |
| `SLACK_SIGNING_SECRET` | Request verification |
| `SNOWFLAKE_ACCOUNT` | Snowflake account identifier |
| `SNOWFLAKE_USER` | Service account username |
| `SNOWFLAKE_WAREHOUSE` | Compute warehouse |
| `SNOWFLAKE_DATABASE` | `ANALYTICS` |
| `SNOWFLAKE_SCHEMA` | `DBT_EXPORTS_OS` |
| `SNOWFLAKE_ROLE` | Access role |
| `SNOWFLAKE_RSA_KEY_PASSPHRASE` | Passphrase for RSA private key |
| `SNOWFLAKE_RSA_KEY_CONTENT` | Full PEM content of RSA key (newlines as `\n`) |
| `KNOCOMMERCE_CLIENT_ID` | KnoCommerce OAuth client ID |
| `KNOCOMMERCE_SECRET` | KnoCommerce OAuth client secret |
| `OKENDO_SUBSCRIBER_ID` | Okendo subscriber ID |
| `OKENDO_API_KEY` | Okendo API key |
| `REDO_API_KEY` | Redo API key (not yet used in production) |

**Note on RSA key:** `snowflake_client.py` reads `SNOWFLAKE_RSA_KEY_CONTENT` and deserialises it at runtime. The `.p8` file is only used locally. Do not add file-path logic for production.

---

## Supported Commands

| Slash command | Aliases | Status |
|---|---|---|
| `/csat [L7\|L30\|L180]` | — | Live |
| `/voc [L7\|L30\|L180] [product:"name"]` | — | Live |
| `/nps [L7\|L30\|L180]` | — | Live |
| `/returns [L7\|L30\|L180] [product:"name"]` | — | Live (data only, no Redo comments yet) |
| `/reviews [L7\|L30\|L180]` | — | Live |
| `/errors [L7\|L30\|L180]` | — | Live |
| `/csat-details` | — | Live |
| `/scout <natural language>` | DM, @mention | NL routing via `nl_router.py` |
| `/scout-help` | — | Live |

---

## NL Routing

`nl_router.py` uses `gpt-4.1-mini` with a strict JSON contract to classify natural language into commands. The router outputs:

```json
{
  "command": "VOC",
  "timeframe": "L30",
  "filters": {"product": "anytime crewneck"},
  "confidence": "high",
  "reasoning": "..."
}
```

**Known gaps (see `NL_ROUTING_STATUS.md` for full details):**
- No schema validation on LLM output — malformed JSON can propagate
- Keyword fallback does not extract product filters
- Taxonomy is shallow — `Returns` and `KnoCommerce` are not yet routable NL targets
- No golden-case test suite for NL routing

---

## Data Sources

### Snowflake (primary)
Database: `ANALYTICS`, schema: `DBT_EXPORTS_OS`

| Table | Used by | Notes |
|---|---|---|
| `EXPORT_CSX__CSAT` | `queries/csat.py` | CSAT scores from Richpanel |
| `CONVERSATIONS` | `queries/voc.py`, `queries/errors.py` | Richpanel conversations; has `TAGS` (UUID array) and `SUBJECTS` |
| `EXPORT_CSX__NPS` | `queries/nps.py` | NPS survey responses |
| `EXPORT_CSX__RETURNS` | `queries/returns.py` | Returns from Redo/Shopify; only return rows (not all orders) |
| `EXPORT_CSX__REVIEWS` | `queries/reviews.py` | Okendo reviews |
| `OS_ALL_ORDERS` | (future) | Needed for true return rate calculation |

**Important returns note:** `EXPORT_CSX__RETURNS` only contains return rows, not all orders. Return rate (`qty_returned / gross_quantity`) at line-item level gives ~99% because denominator is only returned items. True return rate requires joining with `OS_ALL_ORDERS`. Current implementation reports volume/mix metrics instead and flags this to the user.

### KnoCommerce (post-purchase survey — not yet integrated)
- OAuth 2.0 client credentials flow
- Base URL: `https://app-api.knocommerce.com/api`
- Key endpoints: `/rest/surveys`, `/rest/responses`
- Cursor pagination, up to 250 per page
- Credentials: `KNOCOMMERCE_CLIENT_ID`, `KNOCOMMERCE_SECRET`
- See `knocommerce_api_notes.md` for full schema

### Okendo (reviews — integrated via Snowflake)
- Reviews already in Snowflake via `EXPORT_CSX__REVIEWS`
- Direct API available if needed: `OKENDO_SUBSCRIBER_ID`, `OKENDO_API_KEY`
- See `okendo_api_notes.md`

### Redo (returns comments — not yet integrated)
- Base URL: `https://api.getredo.com/v2.2`
- Endpoint: `GET /stores/{storeId}/returns`
- Has primary reason, secondary reason, customer comments
- **Credentials not yet obtained** — `REDO_API_KEY` is a placeholder
- Decision: skip for now, add later to enrich Snowflake returns data
- See `redo_api_notes.md`

---

## Tag Taxonomy

`scout/taxonomy/tags.py` maps tag names to business scopes. Tags are stored as UUID arrays in `CONVERSATIONS.TAGS` and resolved via `tag_mapping.TAG_ID_TO_NAME` (root-level file — tech debt).

Current scopes: `product_feedback`, `returns`, `shipping`, `pricing_promos`, `order_status`, `loyalty`, `checkout`, `general_inquiry`.

The `tag_scope_service.py` resolves scope → UUID set → SQL `ARRAY_CONTAINS` filter.

---

## Pending Work

### 1. Returns capture logic
**Goal:** Surface richer returns intelligence in the `/returns` command.
- Current state: `queries/returns.py` queries `EXPORT_CSX__RETURNS` and returns volume/mix/top-products/top-refund-notes. `formatters.py` has `format_returns()`.
- What's missing: Redo API integration (primary reason, secondary reason, customer comments). Credentials not yet available — build the integration scaffold but leave it behind a feature flag until credentials are provided.
- Also consider: true return rate via `OS_ALL_ORDERS` join.

### 2. KnoCommerce capture logic
**Goal:** Add a `/kno` (or `/survey`) command that pulls post-purchase survey responses from KnoCommerce and surfaces top themes.
- Auth flow documented in `knocommerce_api_notes.md`
- Credentials available: `KNOCOMMERCE_CLIENT_ID`, `KNOCOMMERCE_SECRET`
- Pattern to follow: similar to `queries/reviews.py` — fetch from API, aggregate, format
- NL routing: extend `nl_router.py` system prompt to include `KnoCommerce` as a routable command

### 3. NL routing refinement + taxonomy deepening
**Goal:** Make NL routing more accurate and cover more intents.
- Add schema validation/normalisation for LLM output (enforce allowed enums, default missing fields)
- Expand keyword fallback to extract product mentions
- Add golden-case test suite (`tests/test_nl_routing.py`)
- Deepen tag taxonomy: review current tag groups against actual Richpanel tag data, add missing tags
- Consider adding `Returns` and `KnoCommerce` as NL-routable targets once those commands are built

---

## Security Notes (Outstanding)

The following files contain real credentials and are committed to the public GitHub repo. **History rewrite is pending:**

- `knocommerce_api_notes.md` — real KnoCommerce client ID and OAuth secret
- `okendo_api_notes.md` — real Okendo API key
- `kno_auth_debug.md` — KnoCommerce auth debug notes
- `slack_channels.md` — real Slack channel IDs

These will be removed from git history using `git-filter-repo` and the credentials rotated. Do not add new credentials to any tracked file.

---

## Running Tests

```bash
python3.11 -m pytest scout/tests/ -v
```

All 36 tests should pass. Tests live in `scout/tests/`. Do not add tests that require live Snowflake or Slack connections — mock them.

---

## Code Style

- Python 3.11
- No type: ignore comments — fix types properly
- All queries go in `queries/` (Snowflake) or a new `integrations/` directory (third-party APIs)
- All formatters go in `formatters.py`
- New commands follow the pattern: `queries/X.py` → `formatters.py:format_X()` → `command_parser.py` → `scout_bot.py` handler
- Keep `voc_service.py` as the only coordinator between VOC and product feedback — do not break this contract
