# Customer-health report (general `/voc`)

General `/voc` (no `product:` filter) returns a customer-health report: a multi-source scorecard, cited cross-source themes, ticket volume, and a versioned weighted score. Product `/voc` is unchanged; see [product VOC](product-voc.md).

```
voc_service.run_voc_query
  ├─ queries/voc.run_voc                       ticket volume, channels, tags, status
  └─ services/health_service.build_health_report
        runs every source concurrently (120 s timeout each)
        ├─ health/sources.py      one scorecard entry per source
        ├─ health/score.py        versioned composite
        └─ health/themes.py       cited cross-source themes
  formatting/voc_health.py        Slack reply
```

A source that fails or times out becomes an unavailable entry with its reason. It never fails the reply and is never turned into a zero.

## Scorecard

Each entry reports its source, population, definition, sample and eligible counts, window, freshness, comparison basis, and availability. Entries reuse the existing modules and never recompute their metrics.

| Entry | Module | Population and definition | Window | In score |
|---|---|---|---|---|
| Support CSAT | `queries/csat.run_csat` | Tickets rated 4–5 ÷ rated tickets × 100, all channels. Same number as `/csat`; not the `/daily` Richpanel API score | Complete days, America/New_York | Yes |
| NPS — NPS Survey | `queries/nps.get_nps_scores`, `get_nps_trend` | % promoters (9–10) − % detractors (0–6). NPS Survey and NPS Survey Copy merged | UTC dates, end excluded | Yes |
| Okendo reviews | `queries/reviews.run_reviews` | Mean stars of approved reviews. A star rating, not CSAT | Last N days to now, UTC | Yes |
| Returns — unit cohort item return rate | `queries/returns.run_return_rates` | Redo returned units ÷ eligible delivered Shopify units. This is not the period order return rate. Exchange rate and exchange share are shown, not scored | Mature delivery cohorts | Yes, when validated |
| Returning Customer PPS | `queries/pps.get_returning_pps` | Recommend NPS within this survey, top reason for coming back, share almost stopped | UTC dates | No |
| New Customer PPS | `queries/pps.get_new_pps` | Recommend NPS within this survey | UTC dates | No |
| Attribution | `queries/pps.get_attribution` | Top first-heard channels among New Customer PPS respondents | UTC dates | No |

The three NPS-type surveys are separate populations and are never averaged. Windows differ by source, as listed in [decisions](decisions.md); each entry prints its own. Returns refer to earlier delivery cohorts than tickets or surveys.

With `channel:`, `tag:`, or `agent:`, the filters apply to ticket volume and ticket evidence only. Every other entry says it covers all customers. CSAT says the filters are not applied.

Freshness is the latest `_FIVETRAN_SYNCED` on Richpanel conversations and KnoCommerce responses (`scout/repositories/health_repository.py`). A sync older than 48 hours, or none in 30 days, makes those entries stale. Okendo is read live. Returns freshness comes from the returns contract.

## Health score (`customer-health/0.1`, provisional)

Configured in `scout/health/score.py` (`DEFAULT_CONFIG`). Every weight, minimum, and limit below is a proposed default. No Jack Archer target or historical baseline is configured, and no industry benchmark is used. The score is not validated just because it produces a number.

| Component | Weight | Normalization (raw → 0–100) | Direction | Minimum sample |
|---|---|---|---|---|
| NPS (NPS Survey) | 30% | −100 to +100 → 0 to 100 | Higher is better | 30 responses |
| Support CSAT | 25% | 0% to 100% → 0 to 100 | Higher is better | 30 rated tickets |
| Okendo rating | 20% | 1★ to 5★ → 0 to 100 | Higher is better | 20 reviews |
| Unit cohort item return rate | 25% | 0% to 100% → 100 to 0 | Lower is better | 500 eligible delivered units |

Normalization is linear over each metric's full scale, so a component says where a value sits on its own scale, not whether it beats a target. Exchange metrics are not scored: a higher exchange share is not treated as better or worse sentiment. Only the NPS Survey is scored, because the PPS surveys are different populations.

Missing-data rules:

- A component is included only if its entry is available, its data is not stale, and it meets its minimum sample. Only a validated return rate counts; a provisional rate is shown and excluded.
- Excluded components are listed with the reason. They are never scored as zero or imputed.
- The score needs at least 3 included components covering at least 70% of the weight. Otherwise it is unavailable, and the scorecard is the answer.
- With partial coverage, weights are rescaled over the covered weight. The reply says so, names what was excluded, and gives the range the full score could take for any value of the missing components.
- A previous-window score is shown only when every included component has a prior value meeting its minimum sample. Otherwise the reply says why there is no trend.

Until Redo and the Shopify order lines are verified, returns are unavailable. The score covers 75% of the weight and is labelled partial.

To change the score, edit `DEFAULT_CONFIG` and bump `HEALTH_SCORE_VERSION`. A future version should calibrate normalization to Jack Archer's own trailing baselines or agreed targets.

## Themes

`scout/health/themes.py` reads evidence from each source separately:

| Source | Evidence | Cap |
|---|---|---|
| Support tickets | Opening customer-authored message of tickets in the window, spread across the window by a stable hash, cleaned and deduplicated | 120 |
| NPS Survey | Open-text answers with response ID and score | 60 |
| Returning / New Customer PPS | Open-text answers with response ID and score | 40 each |
| Okendo reviews | Most recent 1–3★ and 4–5★ reviews with text | 30 + 30 |

The model gets numbered items tagged by source. It returns labels, a summary, an action, and the item numbers behind each theme. Code computes everything countable from those citations:

- distinct tickets, responses, or reviews per source, and the share of that source's analysed items;
- how many sources a theme appears in, and the products named in cited items;
- a quote, kept only when it appears in a cited item, with personal details scrubbed.

Counts are never added across sources, because one customer can file a ticket, answer a survey, and leave a review. Themes rank by source count, then by share in the theme's best-sampled source. A label such as "inquiry" or "general feedback" is rejected, so an inquiry theme has to name its subject. The reply reports eligible, retrieved, and analysed counts for each source, and keeps model-written text separate from computed counts.

## What waits on live data

- Snowflake access, to check every query above, the freshness reads, and the ticket sample against live tickets.
- `OPENAI_API_KEY`, to check the synthesis prompt on real evidence.
- The returns contract: `ANALYTICS.RETURN_REDO` profiled, order lines verified, and the maturity rule set. Until then, returns stay unavailable.
- Business targets or trailing baselines, before the score can move past provisional.
