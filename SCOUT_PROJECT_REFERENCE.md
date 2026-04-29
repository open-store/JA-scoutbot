# Scout — Project Reference

This document is the canonical reference for the Scout Internal VOC & CX Data Agent project. It combines the system prompt specification, Snowflake schema findings, implementation details, and deployment notes for continuity across all future tasks.

---

## 1. Project Overview

**Scout** is a Slack-based internal Voice of Customer and CX data agent for Jack Archer / OpenStore. It surfaces customer insights from support data, NPS, returns, and product reviews — delivered directly in Slack via slash commands.

| Attribute | Value |
|---|---|
| Project | Scout (Slack app) |
| Manus Project ID | 7KGxjTAz3TtjWrkWoeUjRs |
| Primary owner | Yankho Trumble (U06H2LUAXNZ) |
| Workspace | openstore-workspace.slack.com |
| Status | Core commands live (CSAT, VOC, Errors); NPS/Returns/Reviews pending API connections |

---

## 2. System Prompt (Canonical Spec)

Scout's identity, routing rules, command parsing, output formatting, and failure handling are defined in the refined specification at `/home/ubuntu/scout_spec_refined.md`. The key principles are:

**Identity:** Senior CX Data Architect — analytical, concise, commercially aware. Never invents data. Always surfaces the "so what."

**Default behavior per response:**
1. Key metric or finding
2. Main drivers
3. Business implication
4. Recommended next action
5. Caveats about data availability or definitions

---

## 3. Data Source Routing

| Command | Primary Source | Fallback |
|---|---|---|
| `/CSAT` | Snowflake (RICHPANEL_CONNECTOR) | Richpanel API |
| `/VOC` | Snowflake (RICHPANEL_CONNECTOR) | Richpanel API |
| `/Errors` | Snowflake (RICHPANEL_CONNECTOR) | Richpanel API |
| `/NPS` | KnoCommerce API | — |
| `/Returns` | Redo API | — |
| `/Reviews` | Okendo API | — |

---

## 4. Snowflake Connection Details

| Parameter | Value |
|---|---|
| Account | XMA73190-FFA94113 |
| User | YANKHO@JACKARCHER.COM |
| Warehouse | MODE |
| Database | FIVETRAN_TEST_DATABASE |
| Schema | RICHPANEL_CONNECTOR |
| Role | ANALYST |
| Auth method | RSA key pair |

**Credential storage:** RSA private key at `/home/ubuntu/scout/RSAKeyYankhot.p8`, passphrase in `/home/ubuntu/scout/.env` as `SNOWFLAKE_RSA_KEY_PASSPHRASE`. Never include credentials in responses, logs, or prompts.

**Primary table:** `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS`

---

## 5. Schema Notes (CONVERSATIONS Table)

Key columns discovered from live schema inspection:

| Column | Type | Notes |
|---|---|---|
| `CONVERSATION_ID` | VARCHAR | Primary key |
| `CREATED_AT` | TIMESTAMP_TZ | Use for all date filtering |
| `UPDATED_AT` | TIMESTAMP_TZ | — |
| `STATUS` | VARCHAR | OPEN, CLOSED, SNOOZED |
| `CHANNEL` | VARCHAR | email, messenger, facebook_feed_comment, email_from_widget, instagram_comment, instagram_message, facebook_message, phone |
| `SUBJECT` | VARCHAR | Ticket subject line — used for theme extraction |
| `SATISFACTION_RATING` | VARCHAR | CSAT score: '1'–'5' (stored as string) |
| `SATISFACTION_RATING_TEXT` | VARCHAR | Amazing, Great, Okay, Bad, Terrible |
| `TAGS` | VARCHAR | JSON array of UUID strings — NOT human-readable names |
| `ASSIGNED_AGENT_ID` | VARCHAR | Agent ID |
| `CUSTOMER_EMAIL` | VARCHAR | PII — never expose in responses |
| `_FIVETRAN_DELETED` | BOOLEAN | Always filter: `_FIVETRAN_DELETED = FALSE` |

**Critical implementation notes:**

- Tags are stored as UUID arrays (e.g., `["741f3ff0-08ea-4a63-90da-62a09bee0915"]`), not human-readable names. A tag name mapping table does not yet exist in Snowflake. Theme extraction currently relies on subject-line keyword analysis.
- CSAT response rate is very low (~4% L30, ~2% L7). Always flag low sample sizes.
- No FRT (First Response Time) or RT (Resolution Time) columns were found in the current schema. These may be in a separate table or not yet synced.
- SATISFACTION_RATING is stored as VARCHAR, not INTEGER. Use `IN ('4', '5')` for positive CSAT, not `>= 4`.
- Use `LATERAL FLATTEN(input => PARSE_JSON(TAGS)) f` to unnest tag arrays.

**Data scale (as of April 28, 2026):**
- Total conversations: 62,295 (May 2024 – present)
- L7 conversations: ~1,100
- L30 conversations: ~4,400

---

## 6. Codebase Structure

All Scout code lives at `/home/ubuntu/scout/`:

```
scout/
├── .env                        # Credentials (never commit)
├── scout.py                    # Main orchestrator / CLI entry point
├── command_parser.py           # Slash command and NL parsing
├── snowflake_client.py         # Snowflake connection and query execution
├── formatters.py               # Slack response formatters
├── queries/
│   ├── __init__.py
│   ├── csat.py                 # CSAT query logic
│   ├── voc.py                  # VOC query logic
│   └── errors.py               # Errors query logic
├── architecture.md             # Architecture design notes
├── schema_notes.md             # Snowflake schema exploration findings
└── slack_channels.md           # Key Slack channel IDs
```

**Usage:**
```bash
cd /home/ubuntu/scout
python3 scout.py '/CSAT L30'
python3 scout.py '/VOC L7'
python3 scout.py '/Errors L30'
python3 scout.py '/Help'
```

---

## 7. Slack Integration

**MCP Server:** `slack` (Manus MCP integration)

**Key channel IDs:**

| Channel | ID | Notes |
|---|---|---|
| #cx-heads | C08CLU0UBEX | Private — externally shared (Slack Connect restricted for API posting) |
| #jackarcher-analytics | C09E27PNB6H | Public — analytics channel |
| #all-things-jack | C07D12T3H7F | Public — cross-functional |
| #cx-updates | C02K3H1MUMC | Public — CX updates |
| Yankho DM | U06H2LUAXNZ | Direct message to Yankho |

**Posting restriction:** `#cx-heads` is an externally shared (Slack Connect) channel. The Slack API blocks bot messages to Slack Connect channels. Use `#jackarcher-analytics` or DM for testing.

**Message format:** Slack mrkdwn — use `*bold*`, `_italic_`, bullet `•`, backtick for code. Limit 5,000 chars per message element.

---

## 8. Tested Commands & Live Results (April 28, 2026)

| Command | Result |
|---|---|
| `/CSAT L7` | 100% CSAT, 21 responses, 1,102 conversations, +5.9 pts vs. prior L7 |
| `/CSAT L30` | 91.6% CSAT, 178 responses, 4,386 conversations, -2.8 pts vs. prior L30 |
| `/VOC L30` | 4,386 conversations (+16.7%), top theme: Order Issues, 6 channels |
| `/Errors L7` | 0.5% error rate, 6 tickets, top: Discount/Promo Code Issues |
| `/Errors L30` | 1.6% error rate, 68 tickets, error CSAT 6 pts below non-error |
| `/Help` | Full command reference displayed |
| `/NPS L30` | "Coming soon" message |
| `/Unknown L7` | Graceful error with command list |

---

## 9. Open Items & Next Steps

| Item | Priority | Owner |
|---|---|---|
| Tag UUID → name mapping (requires Richpanel API or manual mapping table) | High | Yankho / Engineering |
| KnoCommerce API connection for `/NPS` | Medium | Engineering |
| Redo API connection for `/Returns` | Medium | Engineering |
| Okendo API connection for `/Reviews` | Medium | Engineering |
| FRT / Resolution Time columns — confirm table location in Snowflake | Medium | Data / Engineering |
| Access control list (approved Slack user IDs / channels) | Medium | Yankho |
| Async response pattern for Slack 3-second timeout (production deploy) | High | Engineering |
| Production deployment (Slack app registration, slash command webhooks) | High | Engineering |

---

## 10. Key Decisions & Rationale

- **Snowflake-first routing:** Richpanel data is already replicated into Snowflake, making it faster and more reliable than the live API for aggregate queries. The API is reserved for real-time or ticket-level lookups.
- **Subject-line theme extraction:** Since tags are UUIDs without a name mapping, VOC themes are currently derived from subject-line keyword matching. This is directional and should be replaced with proper tag mapping once available.
- **CSAT definition:** Positive CSAT = ratings of 4 or 5 ("Great" or "Amazing"). CSAT % = positive ratings / total rated conversations × 100.
- **Low sample size caveat:** CSAT response rates are ~2–4%. All CSAT outputs automatically flag low sample sizes when rated conversations < 30.
