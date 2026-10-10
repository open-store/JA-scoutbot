# Configuration

Copy `.env.example` to `.env` for local development. Production reads the same names from `/etc/scout.env` on the VM. This page lists names and purposes only.

## Slack

| Variable | Required | Purpose |
|---|---|---|
| `SLACK_BOT_TOKEN` | yes | Bot token for `slack_bolt.App` |
| `SLACK_APP_TOKEN` | yes | Socket Mode token passed to `SocketModeHandler` |
| `SLACK_SIGNING_SECRET` | yes | Request signing secret for the Bolt app |

`SLACK_CLIENT_SECRET` is not read by the current code.

## Snowflake

| Variable | Required | Purpose |
|---|---|---|
| `SNOWFLAKE_ACCOUNT` | yes | Account identifier |
| `SNOWFLAKE_USER` | yes | Service user |
| `SNOWFLAKE_WAREHOUSE` | yes | Warehouse |
| `SNOWFLAKE_DATABASE` | yes | Session database |
| `SNOWFLAKE_SCHEMA` | yes | Session schema |
| `SNOWFLAKE_ROLE` | yes | Role |
| `SNOWFLAKE_RSA_KEY_PASSPHRASE` | if the key is encrypted | Passphrase. Empty string if the key has none |
| `SNOWFLAKE_RSA_KEY_CONTENT` | production | PEM text of the RSA private key. Use this on the VM |
| `SNOWFLAKE_RSA_KEY_PATH` | local, if content is unset | Filesystem path of the key file |

`scout/integrations/snowflake.py` prefers `SNOWFLAKE_RSA_KEY_CONTENT` and falls back to `SNOWFLAKE_RSA_KEY_PATH`. There is no base64 private-key variable.

## Other services

| Variable | Required | Purpose |
|---|---|---|
| `RICHPANEL_API_KEY` | for `/daily` | Sent as `x-richpanel-key` |
| `OKENDO_SUBSCRIBER_ID` | for `/reviews` | Basic-auth username |
| `OKENDO_API_KEY` | for `/reviews` | Basic-auth password |
| `OPENAI_API_KEY` | for NL routing and product synthesis | Read by the OpenAI SDK. If it is missing, routing uses the keyword fallback |
| `OPENAI_BASE_URL` | no | Optional SDK base URL. `scout/routing/nl_router.py` does not read it directly; the SDK does |
| `PORT` | no | Health-check port. Default `8080` |

## Names the bot does not read

| Variable | Purpose |
|---|---|
| `KNOCOMMERCE_CLIENT_ID` | Reserved for a future direct KnoCommerce API client. Surveys currently come from Snowflake |
| `KNOCOMMERCE_SECRET` | Reserved, same as above |
| `REDO_API_KEY` | Reserved for a future Redo client. `/returns` does not call Redo |

See [KnoCommerce](../integrations/knocommerce.md) and [Redo](../integrations/redo.md).
