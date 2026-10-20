# Return reasons, customer comments, and product recommendations

Status as of 2026-10-20. Every piece below runs on fixtures only. Nothing is validated.

## Source map: what fills each "reason" section today

| Section a user sees | What fills it | Kind |
|---|---|---|
| `/returns` "Internal refund notes" | `REFUND_NOTE` in `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS`: the five most common notes in the window, excluding `Redo` and `Order canceled` | Internal or operational note. Written by staff or an app on the Shopify refund, not by the customer |
| `/returns` "Customer reasons and products to watch" | Nothing yet. Each line is unavailable and names the unverified Redo fields | Will hold structured reasons, customer comments, and derived themes |
| `/voc` "Top customer themes" | Richpanel ticket tags (`CONVERSATIONS.TAGS`). The `returns` tag group is `return/exchange_request`, `return-refund-other-status`, `return-logistics`, and `misc-refund-exchange` | Support contact reason. It is not a return reason |
| `/voc product:` "What customers are saying" | Model themes over customer-authored Richpanel messages, with evidence cited and counted in code | Derived themes from support tickets |
| `/pps` "Why did they come back?" | KnoCommerce returning-customer survey, the "come back" question | Why a customer bought again. This is about returning customers, not returned items |
| `/nps` themes | KnoCommerce open text against the fixed `COMPLAINT_THEMES` list. "Returns / exchange friction" is one of those themes | Survey theme |

A question like "why are people returning X" routes to `/returns` (`nl_router` returns keywords). Until Redo is profiled, the only reason-like text there is the internal refund note, so the report labels it "not customer reasons".

The four kinds the request separates, and where each will come from:

1. **Structured primary and secondary reasons:** Redo `primary_reason` and `secondary_reason`.
2. **The customer's own comment:** Redo `customer_comment`.
3. **Internal notes:** Redo `internal_note`, plus Shopify `REFUND_NOTE` today.
4. **Derived themes:** `scout/returns/comments.py`, from the customer's comment only.

## Redo field map

`scout/returns/redo_fields.py` is the only place Scout names a Redo column. Each logical field has a meaning, a column of `None`, and `verified=False` until discovery profiles `ANALYTICS.RETURN_REDO`:

- `return_id`, `return_item_id`
- `shopify_order_id`, `shopify_line_item_id`
- `sku`, `quantity`
- `status`, `outcome`, `exchange_order_id`
- `primary_reason`, `secondary_reason`, `customer_comment`, `internal_note`
- `requested_at`, `received_at`, `settled_at`

Code calls `missing_fields(...)` and reports itself unavailable instead of guessing a column. No SQL reads Redo yet.

## Structured reasons

`scout/returns/reasons.py` summarizes Redo returned items.

- **Counting:** units per primary reason, distinct returns per reason, and secondary reasons within each primary.
- **Shares:** the denominator is all returned units in the population. Items without a reason stay in it as "No reason recorded".
- **Coverage:** units and returns with a reason, with a comment, and with neither.
- **Deduplication:** items are deduplicated by return and item ID before counting, so a join to comments or events cannot multiply units. The number of dropped rows is reported.
- **Status:** the summary is provisional at best and unavailable for an empty population.

## Comment synthesis

`scout/returns/comments.py`:

1. Scrubs emails, phone numbers, order numbers, and long numbers from each comment before it reaches the model (`scrub_personal_details`).
2. Collapses repeats within one return (`collapse_repeats`). The same words from two returns stay separate, because they are two customers' evidence.
3. Numbers up to 150 comments. If there are more, the result says the themes are sample-based and gives "N of M".
4. Asks the model for themes, each with a meaning (fit, quality, expectation gap, return process, or other), cited comment numbers, and a quote.
5. Links the themes with the product-feedback service's `_link_themes`. Themes without valid citations are dropped and counted. A quote is kept only if it appears verbatim in a cited comment.
6. Takes each theme's count from the citations: the number of distinct returns. The model's own counts are ignored. Affected products are counted the same way.
7. Labels a theme from a single return as an emerging signal, not a dominant theme.

A model failure leaves no themes and adds a caveat.

## Product recommendations

`scout/returns/recommendations.py` builds the monitoring view.

**Exclusions.** `RECOMMENDATION_EXCLUSIONS` holds `ExclusionRule("Jetsetter Tech Pants")`.

- The name is resolved with `scout.taxonomy.products.resolve_product` against the catalog, and it must land on one style. In the catalog fixture it resolves to "Jetsetter Tech Pant". Other Jetsetter styles are not excluded.
- A family name or an unknown name does not exclude by name, and the view says so.
- Rules can also match by exact SKU, or by subclass. A subclass rule applies only together with family and class, because a subclass like "Tech" spans products.
- Without a catalog, the name is matched exactly.
- To add a product, add a rule.
- Exclusions filter recommendations only. Official rates still include every eligible product, and explicit questions about an excluded product are still answered.

**Focus.** Products with a known season other than `Prior` are ranked. `Prior` and unknown seasons are listed separately. The caller sets the grain (style, style and season, or SKU), so a style that spans Prior and a new season can be split.

**Ranking.**

- A product with an unavailable item return rate is never ranked, and if the overall rate is unavailable nothing is ranked.
- A product is ranked by its excess returned units over the official overall rate: returned units minus the overall rate times its delivered units. This weighs how far the rate sits above the overall rate by how much the product sells, so a product isn't recommended just for having the most returned units.
- Ties go to more recurring customer evidence, then to the higher rate.
- The change from a comparable prior window is shown only when the two windows are comparable.
- Products under 100 delivered units are listed as "low sample, directional only". Products below the overall rate are listed separately.
- The view's status is the weakest status among its inputs, so it can't be validated before the rates are.

## What waits on the RETURN_REDO profile

- Column names and the status and outcome values in the Redo field map.
- The Redo reason and comment query.
- Per-product item return rates, through the cohort-units relation in [returns data](returns-data.md).
- Reconciliation of reason coverage against Redo's own totals.

Until then, `/returns` shows reasons, comments, and products to watch as unavailable.
