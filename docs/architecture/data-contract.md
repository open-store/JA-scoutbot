# CX/VOC data contract (`cx-voc-contract/1`)

This contract defines the CX/VOC metrics once, so that Scout and a future Rocky dashboard tab report the same numbers in the same way. It covers the returns metrics (`returns-metrics/1`) and the `/voc` customer-health scorecard (`customer-health/0.1`).

**Today nothing is validated.** Every metric reports `validation: not_validated`. The period order return rate is provisional. Unit cohort return rates and Redo reason summaries are unavailable. The scorecard numbers come from production queries but have not been reconciled.

| File | Role |
|---|---|
| `scout/contract/spec.py` | One `MetricSpec` per metric. Built from `scout.returns` and `scout.health.score`, so it cannot drift from the code |
| `scout/contract/payload.py` | `contract_payload(returns_data, health_report)` turns `run_returns` and `build_health_report` output into contract instances. It never recomputes a metric |
| `docs/contracts/cx-voc-contract.v1.json` | The spec as JSON, for Rocky. Regenerate it with `python -m scout.contract > docs/contracts/cx-voc-contract.v1.json`. A test fails if the file is out of date |

## Metrics

| `metric_id` | Kind | Numerator ÷ denominator (source) | Date basis | Health weight |
|---|---|---|---|---|
| `returns.period_order_return_rate_l{7,30,180}` | `rate_pct` | Default: distinct orders with a Redo return submitted in the window, excluding rejected and deleted, ÷ distinct orders placed on `OS_DT` via America/New_York, excluding replacement types, test tags, voided, and cancelled | Complete ET days. Not a cohort. All channels | — |
| `returns.exchange_share_of_submitted_returns` | `rate_pct` | Exchanged submitted returns ÷ submitted returns. Unavailable: outcome is not one column | Same windows | — |
| `returns.item_return_rate` | `rate_pct` | Redo returned units (`ANALYTICS.RETURN_REDO`) ÷ eligible delivered units (`JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES` + `FCT_SHOPIFY__ORDERS`) | Delivery cohort, ET, mature only | 0.25, validated only |
| `returns.item_exchange_rate` | `rate_pct` | Redo exchanged units ÷ eligible delivered units | Same | — |
| `returns.exchange_share_of_returned_units` | `rate_pct` | Redo exchanged units ÷ Redo returned units | Same | — |
| `returns.item_refund_rate` | `rate_pct` | Redo refunded units ÷ eligible delivered units | Same | — |
| `returns.refund_lines` | `count` | Lines in the Shopify refund export (`EXPORT_CSX__RETURNS`) | Refund date, ET | — |
| `returns.refund_line_share_{exchange,cancellation,straight}` | `share_pct` | Lines of that type ÷ refund lines. Cancellation takes precedence | Refund date, ET | — |
| `returns.reason_share` | `distribution` | Redo units per primary reason ÷ all Redo returned units, including those with no reason | Return requested | — |
| `returns.comment_themes` | `themes` | Distinct returns per theme, computed from citations | Return requested | — |
| `returns.products_to_watch` | `ranking` | Excess returned units over the overall rate | Delivery cohort | — |
| `health.support_csat` | `rate_pct` | Tickets rated 4–5 ÷ rated tickets (Richpanel via Snowflake) | Ticket created, ET complete days | 0.25 |
| `health.nps_survey` | `nps` | % promoters − % detractors, NPS Survey (KnoCommerce via Snowflake) | Response created, UTC | 0.30 |
| `health.okendo_rating` | `stars` | Mean stars of approved reviews (Okendo API, live) | Review date, UTC | 0.20 |
| `health.returning_pps_nps`, `health.new_pps_nps` | `nps` | Recommend NPS within each survey. The surveys are separate populations and are never averaged | Response created, UTC | — |
| `health.attribution` | `distribution` | Respondents per first-heard channel ÷ respondents | Response created, UTC | — |
| `health.customer_health_score` | `score_0_100` | Weighted normalized components (`customer-health/0.1`, provisional) | Each component's own | — |
| `health.cross_source_themes` | `themes` | Per-source distinct counts and shares. These are never summed across sources | Each source's own | — |

The period order return rate is `100 * COUNT(DISTINCT orders_with_return) / NULLIF(COUNT(DISTINCT orders_placed), 0)`. The four unit cohort rates carry their SQL: `100 * SUM(numerator) / NULLIF(SUM(denominator), 0)`, written over the cohort-units columns in [returns data](returns-data.md#metric-contract). The refund-line shares carry theirs too.

## Fields on each instance

| Field | Meaning |
|---|---|
| `metric_id`, `label`, `family` | Stable identity. A new meaning gets a new ID and a contract version bump |
| `value`, `value_kind`, `scale` | `null` when unavailable. Percentages are already multiplied by 100, so 12.5 means 12.5%. Enrichment columns such as `RETURN_RATE_PCT` use fractions (1.0 means 100%) and are never copied in |
| `numerator`, `denominator` | Each has a value, description, source relation or API, `source_status`, and SQL expression. `source_status` is `verified`, `named_unverified`, `production_query`, or `live_api` |
| `date_basis`, `window`, `maturity` | What the dates mean, the window this instance covers, and the cohort maturity rule |
| `filters` | Supported dimensions, filters actually applied, and what the filters do not reach |
| `enrichment` | What enrichment applies and its coverage: matched, ambiguous, and unmatched units. The returns enrichment join is `not_built` |
| `freshness` | `fresh`, `stale`, `unknown`, or `live`, with the last sync time when known |
| `status.availability` | `available`, `provisional`, `low_sample`, `stale`, or `unavailable` |
| `status.validation` | `validated` only when `VALIDATION_RECORDS` has a record of a live reconciliation for the metric and the instance is available. The registry is empty |
| `status.reasons` | Why the metric is unavailable or provisional, in words |
| `evidence` | Population, comparison, coverage, themes with citations, or ranked products, depending on the metric |
| `in_health_score`, `health_weight` | Whether and how much the metric enters the composite score |

## Changing the contract

- Add a `ValidationRecord` (date, method, link to the reconciliation) only after a live reconciliation. Don't add one for fixture tests.
- Bump `CONTRACT_VERSION` when an ID, scale, or meaning changes. Bump `METRICS_VERSION` or `HEALTH_SCORE_VERSION` when those calculations change.
- Regenerate the JSON file in the same commit.

## For Rocky

Rocky reads Snowflake under its own role. It should read the same relations and SQL as Scout, not call Scout:

- Returns rates from a shared cohort-units view, once it is approved and created.
- The scorecard from the same source tables, with the same definitions.

The dashboard brief describes the proposed views. No warehouse object has been created.
