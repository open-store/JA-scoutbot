# Returns data: sources, lineage, and metric map

Status as of 2026-10-20. "Verified" means confirmed in production code or Snowflake metadata. "Named" means a person or another codebase supplied the name, but Scout has not read its metadata yet.

## Source roles

| Role | Relation | Status |
|---|---|---|
| Authoritative returns: records, quantities, statuses, outcomes, reasons, comments | `ANALYTICS.RETURN_REDO` (Redo) | Named by the business owner. It is a two-part name, so it could be a schema or a table. Casing, grain, and columns are unverified |
| Delivered-unit denominator and order context | `JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES` and `JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS` | Named. The Rocky dashboard reads them under its own role. Scout's role access is unverified |
| Enrichment: channel, customer status, cohort, product, season, lifecycle | `JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE` | Named. Schema, grain, maturity rule, and refresh are unverified |
| Current `/returns` volume and mix | `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS` | Verified. `/returns` has queried it since April 2026 |

`scout/returns/sources.py` records the same table in code. A metric that depends on a source that is not verified reports itself unavailable. It does not show a percentage.

The enrichment table never replaces Redo outcomes. Its return, exchange, refund, and rate columns are comparison values. A disagreement is reported, not resolved by switching sources.

## What `EXPORT_CSX__RETURNS` is

The columns used by the code and by the earlier exploration scripts are `REFUND_ID`, `PRODUCT_TITLE`, `SKU`, `QTY_RETURNED`, `GROSS_QUANTITY`, `REFUND_NOTE`, `RESTOCK_TYPE`, `RETURN_PLATFORM`, `IS_EXCHANGE`, `IS_REDO_RETURN`, `IS_CANCELLATION`, `DIVISION`, `DEPARTMENT`, `CATEGORY`, `REFUND_DATE_ET`, `REFUND_DATE_UTC`, and `DAYS_TO_RETURN`.

- Each row is a Shopify refund line, dated by refund date. It is not a Redo return record.
- Rows from Redo and from other paths sit side by side. `IS_REDO_RETURN` and `RETURN_PLATFORM` tell them apart.
- Cancellations (refunds before fulfillment) are included.
- Returns that are open but not yet refunded are absent.
- An exchange that produced no Shopify refund row may be missing. That has to be checked against Redo, so `IS_EXCHANGE` here is not Redo's exchange count.

The relationship to `ANALYTICS.RETURN_REDO` is unverified. To confirm it:

1. Match Redo returns to export rows on the Shopify order and refund identifiers that Redo stores.
2. Count Redo returns with no export row: open returns, and exchanges without a refund.
3. Count export rows with no Redo return: manual refunds and cancellations.

Until that is done, the export stays a refund-volume report.

## Order data: Snowflake, not the Shopify API

Denominators, reconciliation, and product attribution read Shopify orders from Snowflake through `scout/integrations/snowflake.py`, using bounded, date-filtered aggregates. Joins use Shopify order and line identifiers, not email or SKU alone. Eligibility should reuse the flags Rocky already applies: `is_dtc_demand_eligible`, `is_reportable_sales_order`, `not is_test_order`, and `not is_gwp_line`. That way Scout and Rocky count the same population.

The Shopify Admin API is not on the Slack request path. No Shopify credential exists in Scout's configuration. A later single-order lookup by order ID, behind a flag and with a credential in `/etc/scout.env`, is the only API use under consideration. See [Shopify](../integrations/shopify.md).

## Metric map

All rates are unit-weighted ratios of summed counts, times 100, computed once. A ratio with a missing source or a zero denominator is unavailable. A verified zero numerator over a positive denominator is 0%.

| Metric | Numerator | Denominator | Date basis | Status |
|---|---|---|---|---|
| Item return rate | Redo returned units that qualify. The definition is pending the Redo status values | Eligible delivered Shopify units, same scope | Delivery cohort | Unavailable: Redo schema and order-line delivery columns unverified |
| Item exchange rate | Redo exchanged units | Same eligible delivered units | Delivery cohort | Unavailable: same |
| Exchange share of returned units | Redo exchanged units | Redo returned units, same population | Delivery cohort | Unavailable: Redo schema unverified |
| Item refund rate | Redo refunded units | Eligible delivered units | Delivery cohort | Unavailable: same |
| Refund volume and mix | Export lines and units | Share of export lines, not a rate | Refund date (ET) | Live |

## Metric contract

`scout/returns/metrics.py` (version `returns-metrics/1`) defines each rate as warehouse SQL over a cohort-units relation:

```sql
100 * SUM(redo_returned_units)  / NULLIF(SUM(eligible_delivered_units), 0) AS item_return_rate
100 * SUM(redo_exchanged_units) / NULLIF(SUM(eligible_delivered_units), 0) AS item_exchange_rate
100 * SUM(redo_exchanged_units) / NULLIF(SUM(redo_returned_units), 0)      AS exchange_share_of_returned_units
100 * SUM(redo_refunded_units)  / NULLIF(SUM(eligible_delivered_units), 0) AS item_refund_rate
```

The relation has one row per delivery cohort date, channel group, channel, maturity flag, customer status, and SKU. Its measures are `eligible_delivered_units`, `redo_returned_units`, `redo_exchanged_units`, `redo_refunded_units`, `redo_store_credit_units`, and `redo_open_units`. Redo measures count only returns within the observation horizon of delivery.

`rollup_query` builds the SQL with bound parameters for any cohort window, channel, and SKU list. Rocky can run the same SQL against the same relation, so the dashboard never needs its own returns calculation. The relation does not exist yet. Its SQL joins Redo items to Shopify order lines, and it is written only after `ANALYTICS.RETURN_REDO` is profiled. A shared warehouse view needs sign-off before anyone creates it.

Each rate carries a status:

| Status | When |
|---|---|
| Unavailable | A source isn't verified, the maturity rule is unknown, a count is missing, the denominator is zero, or the numerator exceeds the denominator. No percentage is shown |
| Provisional | Counts exist, but the cohort is still open or the numbers haven't been reconciled against live data |
| Validated | Mature and reconciled. `/returns` never sets this: it always passes `reconciled=False` |

Outcomes are not assumed to add up. Returned units minus refunded, exchanged, store-credit, and open units are shown as "other or unexplained". A negative remainder marks the outcome definitions as inconsistent.

Cohort windows live in `scout/returns/cohorts.py`. A mature L*N* window covers the N delivery days ending `MATURITY_DAYS` before today in America/New_York. Two windows are comparable only if both are mature and share the same length, horizon, and timezone. `MATURITY_DAYS` is `None` until the enrichment model's maturity rule is read.

## Reporting scope

Official cohort reporting scope is `SALES_CHANNEL_GROUP = 'dtc' AND SALES_CHANNEL = 'online_store' AND IS_COHORT_MATURE = TRUE`. Both the numerator and the denominator get channel and maturity from order-level linkage, not from a SKU join to the aggregate table.

Units, order lines, distinct orders, and return requests are different counts. `DELIVERED_ORDER_LINES` is not an order count. Scout does not offer an order return rate.

## Discovery

`python -m scout.returns.discovery` prints a Markdown profile. It needs Snowflake credentials and runs only bounded read-only statements. It covers:

- How `ANALYTICS.RETURN_REDO` resolves, and for each Redo relation: columns, row count, `LAST_ALTERED`, distinct ID counts, values of low-cardinality status, outcome, and reason columns, date ranges, quantity sums, fill counts for comment columns, and JSON key paths.
- Columns and freshness of the Shopify order models, the export, and the enrichment table.
- Aggregate profiles of the export and the enrichment table, including the maturity boundary, the grain check, rate scale, and SKU attribute conflicts.
- Other relations whose names match returns, Redo, Shopify, or order patterns.

It prints no comment text, names, emails, or JSON values.
