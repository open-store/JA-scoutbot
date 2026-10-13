# Decisions and metric definitions

These are the definitions the current code implements.

## CSAT

`/csat` reads `SATISFACTION_RATING` on Richpanel conversations in Snowflake.

CSAT % is the count of ratings `'4'` and `'5'` divided by the count of conversations with a non-null rating, times 100, rounded to one decimal. Ratings `'1'`, `'2'`, and `'3'` are the negative group used for low-CSAT subject themes.

The slash-command window is complete days in `America/New_York`: the end is today at midnight, and the start is that midnight minus N days. The prior window is the N days before that.

Short windows can have very few ratings. The report still returns the percentage. It does not hide a small sample.

`/daily` CSAT is a different number. It comes from the Richpanel reports API (`csat_score_percentage`), not from this Snowflake formula. See [Richpanel API](../integrations/richpanel-api.md).

## Errors

`/errors` does not use an error tag. A conversation counts as an error when `SUBJECT` matches a keyword list in `queries/errors.py` (`error`, `bug`, `checkout`, `discount code`, and the rest of `ERROR_KEYWORDS`).

The error rate is error conversations divided by all conversations in the window. The report also groups matching subjects into categories (discount, checkout, login, subscription, website, other) and compares CSAT on error subjects with CSAT on the rest.

## Returns are not a return rate

`EXPORT_CSX__RETURNS` contains return rows only. Dividing quantity returned by gross quantity inside that table is not a return rate, because orders that were never returned are absent.

`/returns` reports volume and mix instead:

- Cancellations (`IS_CANCELLATION`).
- Exchanges that are not cancellations (`IS_EXCHANGE`).
- Straight returns (neither flag).

A row can have more than one flag. Cancellation wins, then exchange, then straight return, so the three counts do not overlap. `IS_REDO_RETURN` is a count of Redo-processed rows inside this table, not a rate against all orders. Top products and top refund notes come from the same rows.

A true line-item return rate would be `qty_returned / gross_quantity` after joining to all orders (`OS_ALL_ORDERS`). That join is not built. See [roadmap](../roadmap.md) and [Redo](../integrations/redo.md).

## NPS

NPS is `(promoters - detractors) / responses * 100`, rounded to one decimal.

On the 0–10 recommend question (`RESPONSE_ANSWER.TYPE = 'NPS'` and label containing `recommend`):

- Promoter: 9 or 10.
- Passive: 7 or 8.
- Detractor: 6 or below.

Surveys are not averaged into one company NPS. `NPS Survey` and `NPS Survey Copy` are merged under the display name `NPS Survey`. Returning Customer PPS and New Customer PPS stay separate. The four survey names and the Snowflake tables are in [KnoCommerce](../integrations/knocommerce.md).

The time filter is `RESPONSE.CREATED_AT`. `COMPLETED_AT` is often null, so it is not used.

## Lookback windows do not match

| Path | Window |
|---|---|
| Slash parser (`ParsedCommand`) | Complete days ending at midnight `America/New_York` |
| `/nps`, `/pps`, and `/pps_new` | UTC now minus N days, as dates |
| `/reviews` | `datetime.utcnow()` minus N days |
| `/returns` | `CURRENT_DATE()` in Snowflake, minus N days |
| `/daily` | Today in `America/New_York` |

Aligning these would change reported numbers, so it is tracked separately and not part of a docs-only change.

## Help text versus code

`formatters.format_help` still says `/daily` is yesterday, lists Redo as a source, and describes CSAT in a way the Snowflake report does not print agent rows. The code behaviour is what the rest of these docs describe. Changing the help string is a separate change because users see it.
