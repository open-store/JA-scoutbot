# Scout — Customer Intelligence Slackbot for Jack Archer

Scout is a Slack-native customer intelligence tool that surfaces Voice of Customer (VOC) data, CSAT trends, error rates, and product feedback directly in Slack — powered by Richpanel, Snowflake, and OpenAI.

---

## What Scout Does

| Command | What it returns |
|---|---|
| `/voc L7` | Top customer themes, volume, CSAT, channel breakdown for the last 7 days |
| `/voc L30 product:"anytime crewneck"` | Product-specific VOC: tag-scoped conversations + synthesised customer themes |
| `/csat L30` | CSAT score, rated conversation count, trend vs. prior period |
| `/errors L7` | Error-rate themes (returns, quality, order issues) with % share |
| `/nps` | NPS score from KnoCommerce (if configured) |
| `/reviews` | Product review themes from Okendo |
| `/returns` | Return reason breakdown |
| `/scout-help` | Full command reference |

Scout also understands natural language. In any channel where Scout is added, you can @mention it:

```
@Scout what are customers saying about the Clubhouse polo?
@Scout show me CSAT for the last 30 days
@Scout what are our top complaints this week?
```

---

## Architecture

```
scout/
├── scout_bot.py                    # Slack Bolt app (Socket Mode), event handlers
├── command_parser.py               # Parses /slash and natural language into ParsedCommand
├── nl_router.py                    # LLM-powered natural language → command routing
├── formatters.py                   # Slack message formatting (VOC, CSAT, errors, etc.)
├── tag_mapping.py                  # Richpanel tag UUID → human-readable name mapping
├── snowflake_client.py             # Snowflake connection + query execution
├── product_feedback.py             # Legacy wrapper (deprecated — use voc_service)
│
├── queries/
│   ├── voc.py                      # VOC aggregate SQL (passive — accepts conversation_ids)
│   ├── csat.py                     # CSAT query
│   ├── errors.py                   # Error-rate query
│   ├── nps.py                      # NPS query (KnoCommerce)
│   ├── reviews.py                  # Reviews query (Okendo)
│   └── returns.py                  # Returns query
│
├── scout/
│   ├── taxonomy/
│   │   ├── tags.py                 # Tag group definitions (product_feedback, returns, etc.)
│   │   └── products.py             # Product alias resolver (canonical names + aliases)
│   │
│   ├── models/
│   │   └── product_feedback.py     # Typed dataclasses: Request, Scope, Result, Theme
│   │
│   ├── repositories/
│   │   └── product_feedback_repository.py  # All product-feedback SQL (strict + fallback)
│   │
│   ├── services/
│   │   ├── voc_service.py          # Coordinator: routes VOC queries, owns product orchestration
│   │   ├── product_feedback_service.py     # Scope discovery + synthesis (two-step API)
│   │   ├── evidence_service.py     # Message cleaning, dedup, product-relevance ranking
│   │   └── tag_scope_service.py    # Resolves tag group names → tag UUIDs
│   │
│   └── tests/
│       ├── test_voc_scope_boundary.py      # Architecture boundary tests (12 tests)
│       ├── test_tag_scope_service.py
│       ├── test_evidence_service.py
│       ├── test_products.py
│       └── test_formatters.py
│
└── docs/
    └── schema_verification.md      # Snowflake schema notes (tables, joins, column names)
```

---

## Product VOC Architecture (Key Design Decision)

Product-filtered VOC queries use a **two-step scope pattern** to ensure VOC metrics and synthesis always share the same conversation scope:

```
User: "@Scout what are customers saying about the Anytime Crewneck?"
         │
         ▼
   nl_router.py  →  /voc L30 product:"anytime crewneck"
         │
         ▼
   voc_service.run_voc_query(cmd)
         │
         ├── Step 1: product_feedback_service.get_candidate_scope(request)
         │           → ProductFeedbackScope(conversation_ids=[...], retrieval_mode="strict", ...)
         │
         ├── Step 2: queries/voc.run_voc(cmd, conversation_ids=scope.conversation_ids)
         │           → VOC aggregate metrics (volume, channels, tags, status)
         │           → Filtered by the SAME conversation_ids from Step 1
         │
         └── Step 3: product_feedback_service.get_product_feedback_for_scope(scope, request)
                     → Fetch customer messages from scope.conversation_ids
                     → Clean + rank evidence
                     → LLM synthesis → themes, headline, so_what
```

**Architecture contract:**
- `queries/voc.py` does **not** import `product_feedback_service`. It is passive.
- `scout/services/voc_service.py` is the **only** module that imports both.
- General (non-product) VOC queries bypass `voc_service` and call `run_voc()` directly.

---

## Retrieval Strategy (Product Queries)

The product-feedback repository uses a ranked retrieval strategy:

| Pass | Criteria | Retrieval Mode |
|---|---|---|
| **Strict** | Product-feedback tag + product alias in message body or subject | `strict` |
| **Fallback 1** | Product-feedback tag + relaxed/fuzzy alias match | `fallback_relaxed` |
| **Fallback 2** | Message body or subject mention only (no tag required) | `fallback_body_only` |

Auto-replies, out-of-office messages, and marketing blast responses are excluded at the SQL level in the repository.

---

## Setup

### Prerequisites

- Python 3.11+
- Snowflake account with Richpanel data
- Slack app with Socket Mode enabled
- OpenAI API key

### Environment Variables

Create a `.env` file in the project root:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
SNOWFLAKE_ACCOUNT=...
SNOWFLAKE_USER=...
SNOWFLAKE_PRIVATE_KEY_PATH=...
OPENAI_API_KEY=sk-proj-...
```

### Running Locally

```bash
pip install -r requirements.txt
python scout_bot.py
```

### Deployment (Railway)

The bot is deployed on Railway with Socket Mode — no public URL or webhook required. Push to `main` triggers an automatic redeploy.

Required Railway environment variables: `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_SIGNING_SECRET`, `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY_B64`, `OPENAI_API_KEY`.

---

## Running Tests

```bash
cd /path/to/scout
pip install pytest
python -m pytest scout/tests/ -v
```

---

## Slack App Configuration

Required **Bot Token Scopes:**
- `app_mentions:read`, `chat:write`, `commands`
- `channels:history`, `groups:history`, `im:history`, `mpim:history`
- `im:read`, `im:write`

Required **Event Subscriptions (Bot Events):**
- `app_mention` — responds to @Scout in channels
- `message.im` — responds to @Scout in DMs (requires Messages Tab enabled in App Home)

**App Home → Messages Tab:** Enable "Allow users to send Slash commands and messages from the messages tab" for DM support.

---

## Open Items / Future PRs

| Item | Branch | Status |
|---|---|---|
| Migrate SQL from `queries/*.py` into `repositories/` | `feature/sql-repository-migration` | Planned |
| Split `formatters.py` into `formatting/voc.py`, `formatting/product_feedback.py`, etc. | `feature/formatter-split` | Planned |
| Normalize connectors under `connectors/` (Snowflake, Okendo, KnoCommerce) | `feature/connector-normalization` | Planned |
| Product catalog from Shopify API or CSV | `feature/product-catalog` | Planned |
| Redo return enrichment (daily sync → Snowflake) | `feature/redo-returns` | Planned |
| KnoCommerce NPS fix (auth broken) | — | Blocked on new API credentials |
