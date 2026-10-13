# KnoCommerce

`/nps`, `/pps`, `/pps_new`, and `/attribution` read KnoCommerce surveys from Snowflake. They do not call the KnoCommerce HTTP API. Nothing in the current Python sources builds an OAuth request or pages an HTTP survey response.

The intended direct-API credential names are `KNOCOMMERCE_CLIENT_ID` and `KNOCOMMERCE_SECRET`. The running bot does not read either variable. Do not put values in the repo.

## Snowflake schema

Database `ANALYTICS`, schema `KNOCOMMERCE__NPS___SURVEYS_`.

Tables the queries use:

| Table | Role |
|---|---|
| `SURVEY` | Survey title, joined to responses |
| `RESPONSE` | One submission. Window column is `CREATED_AT` |
| `RESPONSE_ANSWER` | One answer. `TYPE`, `LABEL`, and `VALUE` |

`SURVEY_QUESTION` exists in the schema comment in `queries/nps.py` and is not queried.

`COMPLETED_AT` is null on many recent responses, so every time filter uses `RESPONSE.CREATED_AT`. Rows with `_FIVETRAN_DELETED = TRUE` are dropped.

`RESPONSE_ANSWER.VALUE` is a VARIANT. Scalar answers often arrive as JSON strings (a quoted number or a quoted label). Checkbox answers arrive as JSON arrays. `queries/pps.py` parses both.

## Surveys by name

`queries/nps.py` keeps four live surveys. Ids live in the `SURVEY_IDS` dict in that file and in `queries/pps.py`. They are not repeated here.

| Survey title | Display name | Command |
|---|---|---|
| NPS Survey | NPS Survey | `/nps` |
| NPS Survey Copy | NPS Survey (merged with the row above) | `/nps` |
| Returning Customer PPS | Returning Customer PPS | `/nps` and `/pps` |
| PPS - New Customers | New Customer PPS | `/nps`, `/pps_new`, and `/attribution` |

NPS is calculated per survey, then the two NPS Survey titles are summed before the score is computed. The other two surveys stay on their own rows. Scores are never averaged across surveys into one number.

## How NPS is calculated

For answers with `TYPE = 'NPS'` and `LABEL` containing `recommend`:

- Promoter: numeric value >= 9
- Passive: 7 or 8
- Detractor: <= 6

`NPS = round((promoters - detractors) / total * 100, 1)`.

`/nps` also classifies open-text answers (`TYPE` in `Text`, `TextArea`) into the complaint and praise theme lists declared in `queries/nps.py`.

`/pps` reads the returning-customer survey: the recommend score, the "come back" checkbox, the "stop you" yes/no, and open text. `/pps_new` reads the new-customer survey: the recommend score and open text. `/attribution` reads the new-customer survey: how they heard about the brand (normalised to a fixed channel list), what brought them, consideration window, and who the purchase was for.
