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
  csat.py       → Snowflake                  voc_service.py          ← product VOC coordinator
  voc.py        → Snowflake                  product_feedback_service.py
  nps.py        → KnoCommerce API            tag_scope_service.py
  returns.py    → Snowflake                  evidence_service.py
  reviews.py    → Okendo API
  errors.py     → Snowflake
       │
  snowflake_client.py   ← shared Snowflake connection (RSA key auth)
       │
  formatters.py         ← all Slack message formatting
```

**Key architectural rules:**
- `voc.py` does NOT import `product_feedback_service`. `voc_service.py` is the only coordinator between them.
- `tag_mapping.py` at root is imported by `scout/taxonomy/tags.py`, `queries/voc.py`, `scout/repositories/product_feedback_repository.py`, and `scout/services/tag_scope_service.py`. This is a known tech debt item — it should be consolidated into `scout/taxonomy/`.
- `bot_server.py` and `scout.py` at root are **dead code** — superseded by `scout_bot.py`. Do not modify or reference them.
- `product_feedback.py` at root is a thin wrapper — logic lives in `scout/services/`.

---

## Deployment

**Platform:** Google Compute Engine VM (Container-Optimized OS)
**Project:** `ja-scout-yt`
**VM:** `scout-bot-vm`, zone `us-east1-c`
**Machine type:** `e2-micro`
**Image registry:** `us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest`

**How to redeploy after a code change:**

```bash
# 1. Build and push new image (run from repo root in sandbox)
sudo docker build -t us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest .

# Re-authenticate Docker if needed (token expires)
ACCESS_TOKEN=$(gcloud auth print-access-token)
echo "$ACCESS_TOKEN" | sudo docker login -u oauth2accesstoken \
  --password-stdin https://us-east1-docker.pkg.dev

sudo docker push us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest

# 2. On the VM: authenticate (uses GCE metadata server — one-time setup)
gcloud compute ssh scout-bot-vm --zone=us-east1-c --project=ja-scout-yt \
  --command="docker-credential-gcr configure-docker --registries=us-east1-docker.pkg.dev"

# 3. Pull new image and restart
gcloud compute ssh scout-bot-vm --zone=us-east1-c --project=ja-scout-yt \
  --command="docker pull us-east1-docker.pkg.dev/ja-scout-yt/scout-repo/scout-bot:latest && sudo systemctl restart scout.service"
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

**Health check:** A lightweight HTTP server runs on port 8080 inside the container (background thread). This was added for Cloud Run compatibility and is harmless on the VM.

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
| `SNOWFLAKE_RSA_KEY_CONTENT` | Full PEM content of RSA key (newlines as `\n`) — used in production |
| `SNOWFLAKE_RSA_KEY_PATH` | Path to `.p8` file — used in local dev only |
| `KNOCOMMERCE_CLIENT_ID` | KnoCommerce OAuth client ID |
| `KNOCOMMERCE_SECRET` | KnoCommerce OAuth client secret |
| `OKENDO_SUBSCRIBER_ID` | Okendo subscriber ID |
| `OKENDO_API_KEY` | Okendo API key |
| `REDO_API_KEY` | Redo API key (not yet used in production) |
| `OPENAI_API_KEY` | OpenAI API key — required for NL routing (gpt-4.1-mini) |

**Note on RSA key:** `snowflake_client.py` tries `SNOWFLAKE_RSA_KEY_CONTENT` first (production), then falls back to `SNOWFLAKE_RSA_KEY_PATH` (local dev). Do not hardcode file paths.

**Note on OpenAI:** `nl_router.py` and `scout/services/product_feedback_service.py` both call `OpenAI()` which reads `OPENAI_API_KEY` from the environment. If this key is missing, NL routing silently falls back to keyword matching — commands still work but natural language classification is degraded.

---

## Supported Commands

| Slash command | Data source | Status |
|---|---|---|
| `/csat [L7\|L30\|L180]` | Snowflake — `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS` | Live |
| `/voc [L7\|L30\|L180] [product:"name"]` | Snowflake — `CONVERSATIONS` | Live |
| `/errors [L7\|L30\|L180]` | Snowflake — `CONVERSATIONS` | Live |
| `/nps [L7\|L30\|L180]` | KnoCommerce API | Live |
| `/returns [L7\|L30\|L180] [product:"name"]` | Snowflake — `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS` | Live (volume/mix only — see note) |
| `/reviews [L7\|L30\|L180]` | Okendo API | Live |
| `/scout <natural language>` | Routes to above | Live |
| `/scout-help` / `/help` | — | Live |
| `/csat-details` | — | **Not registered** — remove from docs |

**Returns note:** `EXPORT_CSX__RETURNS` only contains return rows, not all orders. True return rate requires joining with `OS_ALL_ORDERS`. Current implementation reports volume/mix/top-products/top-refund-notes and flags this to the user.

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

Valid commands the router can emit: `CSAT`, `VOC`, `Errors`, `NPS`, `Returns`, `Reviews`, `Help`.

**Known gaps (see `NL_ROUTING_STATUS.md` for full details):**
- No schema validation on LLM output — malformed JSON can propagate
- Keyword fallback does not extract product filters
- `KnoCommerce` (survey data) is not yet a routable NL target
- No golden-case test suite for NL routing

---

## Data Sources

### Snowflake
CSAT, VOC, Errors all query: `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS`
Returns queries: `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS`

| Table | Used by |
|---|---|
| `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS` | `queries/csat.py`, `queries/voc.py`, `queries/errors.py`, `scout/repositories/product_feedback_repository.py` |
| `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS` | `queries/returns.py` |

### KnoCommerce (NPS + post-purchase surveys)
- `queries/nps.py` calls KnoCommerce API directly (OAuth 2.0 client credentials)
- Base URL: `https://app-api.knocommerce.com`
- Auth: POST `/api/oauth2/token` with Basic auth (base64 of `url_encode(id):url_encode(secret)`)
- Key endpoints: `/api/rest/surveys`, `/api/rest/responses`
- Cursor pagination, up to 250 per page
- Credentials: `KNOCOMMERCE_CLIENT_ID`, `KNOCOMMERCE_SECRET`
- See `knocommerce_api_notes.md` for full schema

### Okendo (reviews)
- `queries/reviews.py` calls Okendo API directly
- Base URL: `https://api.okendo.io/enterprise`
- Credentials: `OKENDO_SUBSCRIBER_ID`, `OKENDO_API_KEY`
- Reviews are NOT in Snowflake — always fetched live from Okendo API

### Redo (returns comments — not yet integrated)
- Base URL: `https://api.getredo.com/v2.2`
- Endpoint: `GET /stores/{storeId}/returns`
- Has primary reason, secondary reason, customer comments
- **Credentials not yet obtained** — `REDO_API_KEY` is a placeholder
- Decision: skip for now, add later to enrich Snowflake returns data
- See `redo_api_notes.md`

---

## Tag Taxonomy

`scout/taxonomy/tags.py` maps tag names to business scopes. Tags are stored as UUID arrays in `CONVERSATIONS.TAGS` and resolved via `tag_mapping.TAG_ID_TO_NAME` (root-level file — tech debt, should move into `scout/taxonomy/`).

Current scopes: `product_feedback`, `returns`, `shipping`, `pricing_promos`, `order_status`, `loyalty`, `checkout`, `general_inquiry`.

`tag_scope_service.py` resolves scope → UUID set → SQL `ARRAY_CONTAINS` filter.

---

## Pending Work

### 1. Returns capture logic
**Goal:** Surface richer returns intelligence in the `/returns` command.
- Current state: `queries/returns.py` queries `EXPORT_CSX__RETURNS` and returns volume/mix/top-products/top-refund-notes. `formatters.py` has `format_returns()`.
- What's missing: Redo API integration (primary reason, secondary reason, customer comments). Credentials not yet available — build the integration scaffold but leave it behind a feature flag until credentials are provided.
- Also consider: true return rate via `OS_ALL_ORDERS` join.

### 2. KnoCommerce capture logic
**Goal:** Add a `/kno` (or `/survey`) command that pulls post-purchase survey responses from KnoCommerce and surfaces top themes.
- Auth flow already implemented in `queries/nps.py` — reuse `_get_token()` and `_api_get()` helpers
- Credentials available: `KNOCOMMERCE_CLIENT_ID`, `KNOCOMMERCE_SECRET`
- Pattern to follow: similar to `queries/reviews.py` — fetch from API, aggregate, format
- NL routing: extend `nl_router.py` system prompt to include `KnoCommerce` as a routable command

### 3. NL routing refinement + taxonomy deepening
**Goal:** Make NL routing more accurate and cover more intents.
- Add schema validation/normalisation for LLM output (enforce allowed enums, default missing fields)
- Expand keyword fallback to extract product mentions
- Add golden-case test suite (`tests/test_nl_routing.py`)
- Deepen tag taxonomy: review current tag groups against actual Richpanel tag data, add missing tags
- Add `Returns` and `KnoCommerce` as NL-routable targets once those commands are built

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

All 36 tests should pass. Tests live in `scout/tests/`. Do not add tests that require live Snowflake, Slack, or API connections — mock them.

---

## Code Style

- Python 3.11
- No `type: ignore` comments — fix types properly
- `requests` is not in `requirements.txt` but is available as a transitive dependency of `snowflake-connector-python`. Add it explicitly if adding new code that uses it.
- All Snowflake queries go in `queries/`; third-party API calls go in `queries/` for now (consider `integrations/` for future additions)
- All formatters go in `formatters.py`
- New commands follow the pattern: `queries/X.py` → `formatters.py:format_X()` → `command_parser.py` → `scout_bot.py` handler
- Keep `voc_service.py` as the only coordinator between VOC and product feedback — do not break this contract
