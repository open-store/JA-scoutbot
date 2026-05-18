# Snowflake Schema Verification — Product Feedback Pipeline

**Date:** 2026-05-14
**Branch:** `feature/product-feedback-pipeline`
**Database:** `FIVETRAN_TEST_DATABASE`
**Schema:** `RICHPANEL_CONNECTOR`

---

## Tables

Only 3 tables exist in the `RICHPANEL_CONNECTOR` schema:

| Table | Purpose |
|---|---|
| `CONVERSATIONS` | Support tickets / conversations |
| `MESSAGES` | Individual messages within conversations |
| `CUSTOMERS` | Customer records |

**No separate `CONVERSATION_TAGS` or `TAGS` table exists.** Tags are stored as a JSON array of UUIDs in `CONVERSATIONS.TAGS` (TEXT column, nullable).

---

## CONVERSATIONS Table

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `ID` | TEXT | NO | Primary key (email message-id or UUID) |
| `CONVERSATION_NO` | NUMBER | YES | Sequential conversation number |
| `SUBJECT` | TEXT | YES | Ticket subject line |
| `STATUS` | TEXT | YES | CLOSED, SNOOZED, OPEN |
| `ASSIGNEE_ID` | TEXT | YES | Agent UUID |
| `ORGANIZATION_ID` | TEXT | YES | Always `jackarcher229` |
| `CUSTOMER_ID` | TEXT | YES | Customer UUID |
| `CUSTOMER_EMAIL` | TEXT | YES | Customer email address |
| `CHANNEL` | TEXT | YES | email, instagram_comment, messenger, etc. |
| `PRIORITY` | TEXT | YES | LOW, etc. |
| `RECIPIENT` | TEXT | YES | Usually `hello@jackarcher.com` |
| `FROM_ADDRESS` | TEXT | YES | Sender email |
| `TO_ADDRESS` | TEXT | YES | Recipient email |
| `TAGS` | TEXT | YES | **JSON array of tag UUIDs** e.g. `["uuid1", "uuid2"]` |
| `SATISFACTION_RATING` | NUMBER | YES | CSAT score |
| `SATISFACTION_RATING_TEXT` | TEXT | YES | |
| `SATISFACTION_COMMENT` | TEXT | YES | |
| `SATISFACTION_RATED_AT` | TIMESTAMP_TZ | YES | |
| `SATISFACTION_AGENT_ID` | TEXT | YES | |
| `CREATED_AT` | TIMESTAMP_TZ | YES | |
| `UPDATED_AT` | TIMESTAMP_TZ | YES | |
| `_FIVETRAN_SYNCED` | TIMESTAMP_TZ | YES | |
| `_FIVETRAN_DELETED` | BOOLEAN | YES | |

---

## MESSAGES Table

| Column | Type | Nullable | Notes |
|---|---|---|---|
| `ID` | TEXT | NO | Message hash ID |
| `CONVERSATION_ID` | TEXT | YES | FK → CONVERSATIONS.ID |
| `CONVERSATION_NO` | NUMBER | YES | |
| `AUTHOR_ID` | TEXT | YES | **Key field for customer-authored detection** |
| `BODY` | TEXT | YES | Plain text body (may contain HTML fragments) |
| `HTML_BODY` | TEXT | YES | Full HTML body |
| `ATTACHMENTS` | TEXT | YES | |
| `CREATED_AT` | TIMESTAMP_TZ | YES | |
| `_FIVETRAN_SYNCED` | TIMESTAMP_TZ | YES | |
| `_FIVETRAN_DELETED` | BOOLEAN | YES | |

**No `MESSAGE_TYPE`, `DIRECTION`, `SENDER_TYPE`, or `IS_FROM_CUSTOMER` column exists.**

---

## Customer-Authored Message Detection

The only indicator is `AUTHOR_ID`. Based on data analysis:

| AUTHOR_ID Pattern | Category | Count | Detection Rule |
|---|---|---|---|
| Matches `CONVERSATIONS.CUSTOMER_ID` | **Customer** | 35,389 | `m.AUTHOR_ID = c.CUSTOMER_ID` |
| `hello@jackarcher.com` | Agent | 31,102 | Known agent email |
| `operator` | Automation/System | 17,199 | System-generated |
| `jackarcher229%` prefix | System email | 42,620 | Fivetran system IDs |
| `jack.archer` | Agent | 199 | Known agent |
| Other UUIDs | **Mixed** | 96,069 | Could be agents or customers |

**Recommended customer-authored filter:**
```sql
m.AUTHOR_ID = c.CUSTOMER_ID
```

This is the most reliable indicator. The "other_uuid" category (96K) includes both agent UUIDs and customer UUIDs that don't match `CUSTOMER_ID` (possibly due to multi-channel conversations where the customer ID changed). For safety, we should also consider:

```sql
m.AUTHOR_ID NOT IN ('hello@jackarcher.com', 'operator', 'jack.archer')
AND m.AUTHOR_ID NOT LIKE 'jackarcher229%'
```

This broader filter catches more customer messages but may include some agent messages from UUID-based agents.

---

## Tag Storage & Resolution

Tags are stored as a JSON array of UUIDs in `CONVERSATIONS.TAGS`:
```json
["0d2af8c7-bd46-48e4-9efe-f3f69313d345", "d2f8bc0e-fa61-45ef-96b6-c3a2108e40c8"]
```

Resolution is done via `tag_mapping.py` which contains a hardcoded `TAG_ID_TO_NAME` dict.

**To query conversations by tag, use FLATTEN:**
```sql
SELECT c.*
FROM CONVERSATIONS c,
     LATERAL FLATTEN(input => PARSE_JSON(c.TAGS)) f
WHERE f.value::STRING IN ('uuid1', 'uuid2', ...)
```

---

## Tag Taxonomy — Product Feedback Scope

From `tag_mapping.py`, the following tags belong to the `product_feedback` scope:

| UUID | Tag Name |
|---|---|
| `4f457947-00d9-4298-a4fc-ef08644670a7` | product-quality |
| `bcd036f6-3db7-4eb8-8901-a1dee3fb17e9` | product-defective-damage |
| `36f8b1fc-7702-4319-8eb9-c8e2cf108812` | product-size-fit-issue |
| `0d2af8c7-bd46-48e4-9efe-f3f69313d345` | product-spec-other |
| `bd8f3e72-2f4e-4323-9f0a-66f7db4841a3` | product_inquiry |
| `f32bc94f-54e2-449d-b571-cfe04e5d4b57` | product_inquiry |
| `23bcc805-a6d2-491a-83c9-d2a8fa01153a` | product_recommendations |
| `71899cb9-38d7-4e38-8e88-211be218ab21` | product_availability |
| `dac0b5b5-77cd-4285-a85a-54156719d442` | product-inventory |
| `dfc72c6a-930f-47dc-9e02-9fd2ca74ac58` | product-suggestion |
| `dd4b7b37-dab2-40d8-a427-869100dc6343` | product-reviews |
| `e5c92b1e-4a7a-471d-a5e9-d28fa116ba67` | variant-mismatch |
| `bbcf0046-674f-4d63-9dfc-52f45a9443ee` | size/fit_assistance |
| `d6108437-a037-409d-8657-b60f84b3a6d2` | damaged_items |
| `8c8312d8-8016-427c-8fe6-6779c90d27cd` | product_comparison |
| `160b3fc9-6e05-4365-85e8-3491da1d3023` | product_reviews_and_ratings |
| `34ed536d-c825-4dbe-8016-7895e0c26d80` | product_setup/installation |
| `418555a9-beaf-407f-b17b-d74595daba63` | product_setup/installation |
| `d419986b-3c0b-4385-aafc-cbaa20b56dd1` | product_restocking_requests |

---

## HTML in Message Bodies

Both `BODY` and `HTML_BODY` columns exist. `BODY` is the plain-text version but can still contain:
- HTML fragments (not fully stripped)
- `<a>` tags with URLs
- `<p>` and `<br>` tags
- CSAT survey templates ("WE VALUE YOUR FEEDBACK!")
- Agent signatures ("Thanks again, Max\nJack Archer")
- Auto-reply content ("I am currently out of the office")

**Evidence cleaning must handle all of these.**

---

## Key Implementation Decisions

1. **Customer-authored filter:** Use `m.AUTHOR_ID = c.CUSTOMER_ID` as strict, with fallback to excluding known agent/system IDs
2. **Tag matching:** Use `LATERAL FLATTEN(PARSE_JSON(c.TAGS))` to match against product-feedback tag UUIDs
3. **Body source:** Use `BODY` column (plain text), apply HTML stripping for residual tags
4. **No separate tag join table** — tags are embedded in the conversations row
