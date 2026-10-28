# Decisions and metric definitions

These are the definitions the current code implements.

## CSAT

`/csat` reads `SATISFACTION_RATING` on Richpanel conversations in Snowflake.

CSAT % is the count of ratings `'4'` and `'5'` divided by the count of conversations with a non-null rating, times 100, rounded to one decimal. Ratings `'1'`, `'2'`, and `'3'` are the negative group used for low-CSAT subject themes.

The slash-command window takes its dates from `America/New_York`: the end is today's date there, and the start is that date minus N days. The prior window is the N days before that. The query passes those bounds without an offset, and Scout's Snowflake session is UTC, so the counted tickets fall on complete UTC days. Whether to align this with ET is an open decision.

Short windows can have very few ratings. The report still returns the percentage. It does not hide a small sample.

`/daily` CSAT is a different number. It comes from the Richpanel reports API (`csat_score_percentage`), not from this Snowflake formula. See [Richpanel API](../integrations/richpanel-api.md).

## Errors

Yankho's definition, 2026-10-28: the error rate is the share of tickets carrying a tag from one Issue sub-category. `/errors` shows it as the support error rate (`scout.health.error_rate`), labeled with its group and marked provisional.

| Piece | Default |
|---|---|
| Numerator | Distinct conversations whose `TAGS` hold a UUID of the error group's tags |
| Error group | `scout.taxonomy.issue_tags.ERROR_RATE_GROUP`, default `Storefront / UX` (`storefront-experience`, `web_issues`, the same tags as the `web_errors` scope). It can be any Issue sub-category or all of `Issue`. Which one counts as an error is not decided |
| Tag names | Each sub-category lists the live Jack Archer tags and the legacy names from the support taxonomy's legacy mappings. No legacy name has a UUID in `TAG_ID_TO_NAME`, and the warehouse has no tag table, so legacy names match no ticket |
| Denominator | All conversations created in the window, `_FIVETRAN_DELETED = FALSE`, all channels |
| Windows | `/errors LN` uses the command window. The contract uses `L7`, `L30`, and `L180`, the period-rate windows. Both are complete `America/New_York` days, bound with their UTC offsets |

The rest of `/errors` still uses subject keywords (`ERROR_KEYWORDS` in `queries/errors.py`): total error-related tickets, the categories (discount, checkout, login, subscription, website, other), and the CSAT comparison. Those keyword queries pass bounds without an offset, so the UTC session reads them as UTC days. The keyword share is not the error rate.

## Returns are not a return rate

`EXPORT_CSX__RETURNS` contains return rows only. Dividing quantity returned by gross quantity inside that table is not a return rate, because orders that were never returned are absent.

`/returns` reports volume and mix instead:

- Cancellations (`IS_CANCELLATION`).
- Exchanges that are not cancellations (`IS_EXCHANGE`).
- Straight returns (neither flag).

A row can have more than one flag. Cancellation wins, then exchange, then straight return, so the three counts do not overlap. `IS_REDO_RETURN` is a count of Redo-processed rows inside this table, not a rate against all orders. Top products and top refund notes come from the same rows.

The export is a Shopify refund export, not the Redo returns table. Its shares are shares of refund lines, and `/returns` labels them that way.

## Period order return rate

Yankho's choice, 2026-10-27: drop the delivered-unit denominator. The return rate is a period rate, not a cohort rate. Returns submitted in the window can come from orders placed earlier.

`/returns` shows one default, labeled as the default until the open choices are settled. Comparison figures are not in Slack.

| Piece | Default |
|---|---|
| Numerator | Distinct orders with at least one Redo `RETURN` whose `CREATED_AT` falls in the window, excluding status `rejected` or `deleted`. There is no submitted-at column. `CREATED_AT` is `TIMESTAMP_TZ`, converted to `America/New_York`. |
| Raw return count | Count of those default `RETURN` rows, labeled separately. It is not the rate. |
| Unmatched returns | Default returns whose order id is not in `OS_ALL_ORDERS`. They stay in the numerator. |
| Denominator | Distinct `SHOP_ORDER_ID` in `MAIN_OS.OS_ALL_ORDERS` placed in the same window. The order-date column is `OS_DT` (the UTC date of order `CREATED_AT`). The window converts that `CREATED_AT` from UTC to `America/New_York`. |
| Replacement orders | Excluded only when `SHOP_ORDER_ID` is some Redo return's `EXCHANGE_ORDER_EXTERNAL_ID`. `OS_ORDER_STATUS` `redo_exchange` and `loop_exchange` are not that set: 5,406 of 24,516 `redo_exchange` orders are exchange-order ids, and 0 of 40,974 `loop_exchange` orders are. |
| Test orders | Tags that are exactly `test` or `test order`. There is no test-order column. Experiment tags are not treated as test orders. |
| Voided and cancelled | Excluded in the default (`OS_ORDER_STATUS = 'voided'` or `ORDER_CANCELLED_AT` is not null). Every voided order is also cancelled. |
| Channel | All channels. `OS_ALL_ORDERS` has no sales-channel column. |
| Windows | `L7`, `L30`, and `L180`: that many complete days in `America/New_York`, ending before today. The same bounds are used on both sides. |
| Period exchange rate | Distinct orders with an exchanged item on a default return, divided by the same orders-placed denominator. Exchange means `EXCHANGE_ITEM_QUANTITY > 0`. |
| Exchange share of submitted returns | Returns with an exchanged item ÷ default returns submitted. Also reported: exchanged items ÷ returned items, and the item outcome mix (exchange, refund, store credit, none). |
| Item outcome | Requested outcome at submission. Exchange wins when `EXCHANGE_ITEM_QUANTITY > 0`; otherwise `REFUND_TYPE`. `RETURN_COMPENSATION_METHOD` is not used. |

The comparisons kept out of Slack are: denominator with replacement and test orders included; denominator with voided and cancelled orders included; numerator with rejected and deleted returns included.

`/returns` shows the default as provisional. It does not mark it validated.

Unit cohort rates stay unavailable and are labeled as unit cohort rates so they are not read as this period rate. `OS_ALL_ORDERS` is order-grain and its quantity column is empty, so it is not that denominator. Sources and the unit-cohort contract remain in [returns data](returns-data.md).

## NPS

NPS is `(promoters - detractors) / responses * 100`, rounded to one decimal.

On the 0–10 recommend question (`RESPONSE_ANSWER.TYPE = 'NPS'` and label containing `recommend`):

- Promoter: 9 or 10.
- Passive: 7 or 8.
- Detractor: 6 or below.

Surveys are not averaged into one company NPS. `NPS Survey` and `NPS Survey Copy` are merged under the display name `NPS Survey`. Returning Customer PPS and New Customer PPS stay separate. The four survey names and the Snowflake tables are in [KnoCommerce](../integrations/knocommerce.md).

The time filter is `RESPONSE.CREATED_AT`. `COMPLETED_AT` is often null, so it is not used.

## Customer-health score

General `/voc` shows a provisional composite, version `customer-health/0.1`. It weights NPS Survey NPS at 30%, Support CSAT at 25%, Okendo rating at 20%, and item return rate at 25%. Each is normalized linearly over its full scale. Excluded components are never scored as zero. The score needs 3 components and 70% of the weight. Partial coverage is disclosed with the possible full-score range. CSAT percentage, average star rating, and the `/daily` API score are three different measures and are labelled as such. Details are in [customer health](customer-health.md).

## Lookback windows do not match

| Path | Window |
|---|---|
| Slash parser (`ParsedCommand`) | Complete days ending at midnight `America/New_York` |
| `/nps`, `/pps`, and `/pps_new` | UTC now minus N days, as dates |
| `/reviews` | `datetime.utcnow()` minus N days |
| `/returns` refund volume | `CURRENT_DATE()` in Snowflake, minus N days |
| `/returns` period order return rate | Complete days in `America/New_York` for L7, L30, and L180. Not a delivery cohort |
| `/voc` health report | Each scorecard entry keeps its source's window above. The scored returns component is the unit cohort item return rate, which is unavailable |
| `/daily` | Today in `America/New_York` |

Aligning these would change reported numbers, so it is tracked separately and not part of a docs-only change.

## Help text versus code

`formatters.format_help` still says `/daily` is yesterday, lists Redo as a source, and describes CSAT in a way the Snowflake report does not print agent rows. The code behaviour is what the rest of these docs describe. Changing the help string is a separate change because users see it.
