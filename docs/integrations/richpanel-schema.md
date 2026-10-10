# Richpanel schema in Snowflake

Database `FIVETRAN_TEST_DATABASE`, schema `RICHPANEL_CONNECTOR`. The connector has three tables: `CONVERSATIONS`, `MESSAGES`, and `CUSTOMERS`. There is no separate tags table.

`/csat`, `/voc`, and `/errors` query `CONVERSATIONS` with the fully qualified name. The product-feedback repository queries unqualified `CONVERSATIONS` and `MESSAGES`, so the Snowflake session schema has to be this one.

## CONVERSATIONS

| Column | Type | Notes |
|---|---|---|
| `ID` | TEXT | Primary key |
| `CONVERSATION_NO` | NUMBER | Sequential number |
| `SUBJECT` | TEXT | Used by `/errors` keyword matching and auto-reply exclusion |
| `STATUS` | TEXT | For example CLOSED, SNOOZED, OPEN |
| `ASSIGNEE_ID` | TEXT | Agent id |
| `ORGANIZATION_ID` | TEXT | Tenant id |
| `CUSTOMER_ID` | TEXT | Joined to message `AUTHOR_ID` for customer-authored text |
| `CUSTOMER_EMAIL` | TEXT | Customer address. Do not copy values into docs or tickets |
| `CHANNEL` | TEXT | email, messenger, and other channel names |
| `PRIORITY` | TEXT | |
| `RECIPIENT` | TEXT | Support inbox address |
| `FROM_ADDRESS` | TEXT | |
| `TO_ADDRESS` | TEXT | |
| `TAGS` | TEXT | JSON array of tag UUIDs, nullable |
| `SATISFACTION_RATING` | NUMBER | Stored as text in comparisons (`'1'` through `'5'`) |
| `SATISFACTION_RATING_TEXT` | TEXT | Rating label |
| `SATISFACTION_COMMENT` | TEXT | |
| `SATISFACTION_RATED_AT` | TIMESTAMP_TZ | |
| `SATISFACTION_AGENT_ID` | TEXT | |
| `CREATED_AT` | TIMESTAMP_TZ | Window column for CSAT, VOC, and errors |
| `UPDATED_AT` | TIMESTAMP_TZ | |
| `_FIVETRAN_SYNCED` | TIMESTAMP_TZ | |
| `_FIVETRAN_DELETED` | BOOLEAN | Excluded when true |

## MESSAGES

| Column | Type | Notes |
|---|---|---|
| `ID` | TEXT | Primary key |
| `CONVERSATION_ID` | TEXT | FK to `CONVERSATIONS.ID` |
| `CONVERSATION_NO` | NUMBER | |
| `AUTHOR_ID` | TEXT | Customer detection. See below |
| `BODY` | TEXT | Plain text, sometimes with leftover HTML |
| `HTML_BODY` | TEXT | Full HTML |
| `ATTACHMENTS` | TEXT | |
| `CREATED_AT` | TIMESTAMP_TZ | |
| `_FIVETRAN_SYNCED` | TIMESTAMP_TZ | |
| `_FIVETRAN_DELETED` | BOOLEAN | |

There is no `MESSAGE_TYPE`, `DIRECTION`, `SENDER_TYPE`, or `IS_FROM_CUSTOMER` column.

## Customer-authored messages

The strict rule in `scout/repositories/product_feedback_repository.py` is:

```sql
m.AUTHOR_ID = c.CUSTOMER_ID
```

A broader filter also exists in that file. It excludes known agent and system author ids. Those ids stay in code. Do not copy them into documentation.

## Tags

`TAGS` is a JSON array of UUID strings. Resolution to names is the dict `TAG_ID_TO_NAME` in `tag_mapping.py`. Scope groups (product feedback, shipping, and the rest) are tag names in `scout/taxonomy/tags.py`.

Frequency queries flatten the array:

```sql
SELECT f.value::STRING AS tag_uuid, COUNT(*) AS cnt
FROM CONVERSATIONS c,
     LATERAL FLATTEN(input => PARSE_JSON(c.TAGS)) f
WHERE f.value::STRING IN ('...')
GROUP BY tag_uuid
```

## Message bodies

`BODY` is the column synthesis uses. It can still contain HTML fragments, links, CSAT survey boilerplate, agent signatures, and auto-replies. `evidence_service.py` cleans that before the model sees it. Auto-reply and out-of-office subjects are dropped in SQL.
