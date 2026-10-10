# Okendo

`/reviews` calls the Okendo merchant API from `scout/integrations/okendo.py`. `queries/reviews.py` only aggregates the rows. Reviews are not read from Snowflake.

Base URL: `https://api.okendo.io/enterprise`

## Auth

HTTP Basic. The username is `OKENDO_SUBSCRIBER_ID` and the password is `OKENDO_API_KEY`. The client base64-encodes `subscriber_id:api_key` and sends `Authorization: Basic ...`. Both variables are required. The process raises `KeyError` if either is missing. Do not put the values in the repo.

## List reviews

`GET /reviews`

Query parameters the client sends:

| Parameter | Value |
|---|---|
| `limit` | `100` |
| `orderBy` | `date desc` |
| `status` | `approved` |
| `lastEvaluated` | Cursor from the previous page's `nextUrl`, omitted on the first page |

Okendo does not accept a date filter on this call. The client walks pages newest-first and stops when `dateCreated` is older than the window, when `reviews` is empty, or when `nextUrl` is missing.

The current window stops after 20 pages (20 × 100 reviews). The prior window, used for the rating change, allows 40 pages and keeps reviews that fall in the previous N days.

## Fields Scout reads

| Field | Use |
|---|---|
| `dateCreated` | Window cutoff |
| `rating` | Average, distribution, positive sample (>= 4), negative sample (<= 2) |
| `sentiment` | Sentiment counts |
| `productName` | Top products and the optional product filter |
| `title` | Sample quotes and the product filter |
| `body` | Sample quotes (truncated) and the product filter |
| `tags` | Top tags |
| `helpfulCount` | Tie-break for the positive sample |

A `product:` filter keeps a review when the product name, title, or body contains the filter string, case-insensitive.

The prior period is the N days before the current window. `rating_change` is the difference in average rating. `volume_change_pct` is the relative change in review count.
