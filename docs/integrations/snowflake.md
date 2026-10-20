# Snowflake

`scout/integrations/snowflake.py` opens one short-lived connection per query. Authentication is an RSA private key. The passphrase is `SNOWFLAKE_RSA_KEY_PASSPHRASE` (empty if the key is not encrypted).

Key loading order:

1. `SNOWFLAKE_RSA_KEY_CONTENT` — PEM text. Literal `\n` sequences are turned into newlines. This is the production mode.
2. `SNOWFLAKE_RSA_KEY_PATH` — path to a key file on the local machine. Used only when the content variable is unset.

Do not hardcode a key path in code or docs.

Session settings, all required:

| Variable | Purpose |
|---|---|
| `SNOWFLAKE_ACCOUNT` | Account identifier |
| `SNOWFLAKE_USER` | Service user |
| `SNOWFLAKE_WAREHOUSE` | Warehouse |
| `SNOWFLAKE_DATABASE` | Default database |
| `SNOWFLAKE_SCHEMA` | Default schema |
| `SNOWFLAKE_ROLE` | Role |

Most queries ignore the session default and use a fully qualified name.

## Tables by command

| Command | Relation |
|---|---|
| `/csat`, `/voc`, `/errors` | `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS` |
| Product feedback messages | `CONVERSATIONS` and `MESSAGES` in the session schema (unqualified) |
| `/returns` | `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS` |
| `/nps`, `/pps`, `/pps_new`, `/attribution` | `ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_` tables `SURVEY`, `RESPONSE`, `RESPONSE_ANSWER` |
| Product catalog for product questions | `JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE`, distinct styles only, one bounded query cached for six hours. Read access from Scout's role is not yet confirmed; without it Scout falls back to its alias list |

`/reviews` and `/daily` do not query Snowflake. See [Okendo](okendo.md) and [Richpanel API](richpanel-api.md).

Column notes for the Richpanel connector are in [richpanel-schema.md](richpanel-schema.md). Survey columns are in [knocommerce.md](knocommerce.md).

Deleted Fivetran rows are excluded with `_FIVETRAN_DELETED = FALSE` on the conversation and survey queries.
