# Shopify

Scout has no Shopify API client and no Shopify credential. Order data comes from Snowflake.

## Order models

| Relation | Use | Status |
|---|---|---|
| `JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS` | Order eligibility, customer, and channel | Named. The Rocky dashboard reads it with `is_dtc_demand_eligible`, `is_reportable_sales_order`, and `is_test_order` |
| `JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES` | Units per line, SKU, product, delivery | Named. Rocky reads it with `is_gwp_line` and `variant_title_imd` |

Scout's Snowflake role has not been checked for SELECT on `JA_ANALYTICS`. Delivery-date and unit columns have not been read. `python -m scout.returns.discovery` lists both.

## Why not the Admin API

- No credential exists in `.env.example`, [configuration](../operations/configuration.md), or code, so the granted scopes cannot be checked. Full history needs `read_all_orders`.
- A delivered-unit denominator for L180 would page through every order in the window on every request, under cost-based throttling.
- Redo returns and the enrichment table are in Snowflake. Joining there keeps one calculation.

A bounded lookup of a single order by ID may be added later for attribution of orders newer than the warehouse sync. It would sit behind a flag, keep the credential in `/etc/scout.env`, and never scan orders per request.
