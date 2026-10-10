# Scout

Scout is the Jack Archer Slack bot for customer-experience questions. Anyone in the workspace can ask for CSAT, what customers are talking about, returns, reviews, or survey results without pulling a report themselves.

It runs as a private Slack connection (Socket Mode) on the team's Google Compute Engine VM. It does not need a public webhook.

## What you can ask

Slash commands take a window of `L7`, `L30`, or `L180` (last 7, 30, or 180 days). If you leave the window off, Scout uses 7 days. You can also @mention Scout or DM it in plain English.

| Command | What you get |
|---|---|
| `/voc` | Top reasons customers write in. Add `product:"name"` for feedback on one product |
| `/csat` | Satisfaction score and how it moved versus the previous window |
| `/errors` | How much of the queue looks like site, checkout, or promo problems, and the CSAT on those tickets |
| `/returns` | How many returns, exchanges, and cancellations, and which products lead. This is volume, not the percent of orders returned |
| `/reviews` | Star rating and recent praise or complaints from product reviews |
| `/nps` | Net Promoter Score for each live survey |
| `/pps` | Why returning customers came back, and what almost stopped them |
| `/attribution` | How new customers heard about the brand |
| `/daily` | Today's support KPIs: tickets closed, speed, and CSAT |
| `/scout-help` | The short command list inside Slack |

Examples:

```
@Scout what are customers saying about the Clubhouse polo?
@Scout show me CSAT for the last 30 days
@Scout how are people finding us?
```

If Scout is unsure, it says so and still answers with its best match.

## Where the numbers come from

| Question | System |
|---|---|
| CSAT, VOC, errors | Support conversations stored in Snowflake |
| NPS, returning-customer survey, attribution | Post-purchase surveys stored in Snowflake |
| Returns | Returns table in Snowflake |
| Reviews | Live product-review API |
| Daily support KPIs | Live support-platform API |

Survey scores are not fetched by calling the survey vendor on each question. They are already in Snowflake.

## Roadmap

Shipped since the first version: NPS, the returning-customer survey, new-customer attribution, and the daily support snapshot.

Still open:

- Richer return reasons and customer comments, once return-platform credentials exist.
- A real return rate that includes orders which were not returned.
- Steadier answers when someone asks in plain English and the language model is unavailable.

The working list for the team that builds Scout is in [the roadmap](../roadmap.md).
