# Natural-language routing

`scout/routing/nl_router.py` turns a free-text question into a slash-style command. A future question-type router replaces `route_natural_language`; see [question router](question-router.md). `scout_bot.execute_natural_language` is the only caller on the live path. It runs for @mentions and direct messages.

## Flow

1. Slack text arrives. Mention tokens are stripped.
2. `route_natural_language` calls `gpt-4.1-mini` (`temperature=0`, `max_tokens=200`, `response_format=json_object`).
3. The model JSON is validated, then `_finalise` applies the shared contract below.
4. `build_command_from_routing` turns the result into a string such as `/VOC L30 product:"Atlas Short" topic:fit`.
5. `execute_scout_command` parses that string and runs the normal query and formatter.
6. If `confidence` is `low`, Scout prepends a note that it guessed, then still returns the report.

If the OpenAI call throws, `OPENAI_API_KEY` is missing, or the model output fails validation, `_keyword_fallback` runs instead. It ends in the same `_finalise` step. The client is cached on the first successful construction. The SDK also reads `OPENAI_BASE_URL` when that variable is set.

## Contract

```json
{
  "command": "VOC",
  "timeframe": "L30",
  "filters": {"product": "Atlas Short", "topic": "fit"},
  "confidence": "high",
  "reasoning": "short explanation",
  "router": "llm"
}
```

`router` is `llm` or `keyword_fallback`.

Validation:

- `command` must be one of `CSAT`, `VOC`, `Errors`, `NPS`, `PPS`, `Attribution`, `Returns`, `Reviews`, `Help` (case-insensitive). Anything else falls back to keywords.
- `timeframe` must look like `L<days>`. Otherwise the per-command default applies.
- Only `product`, `segment`, `channel`, `tag`, and `topic` filters are kept. Non-string values are dropped.
- `confidence` outside `high` / `medium` / `low` becomes `medium`.

`_finalise`, for both paths:

- **Timeframe.** An explicit window in the question wins over the routed value: `L45`, "last 180 days", "past 6 months", "two weeks", "this month", "last quarter", "past year". With no window, the default is `L30`, except `Errors` and `Help`, which use `L7`. That matches the model prompt.
- **Product.** `VOC`, `Returns`, and `Reviews` keep a product named in the question. A product found in the catalog (`taxonomy.products.find_product_mentions`) wins over the model's string, so a model that shortened "Atlas Short" to "atlas" is corrected. When the question names a product-like phrase that is not in the catalog ("the Mystery Pant"), the phrase is kept, and `/voc` answers that it is not in the catalog. Resolved names are canonicalised, which also helps `/returns` and `/reviews` match product titles.
- **CSAT about a product** becomes product VOC, which reports CSAT for that product's tickets instead of dropping the product.
- **Topic.** Product VOC keeps the aspect the question asks about (fit, sizing, sleeve length, inseam, waistband, fabric, quality, and similar) as `topic`.
- Commands that cannot use a product drop `product` and `topic`.

The catalog is loaded only when a question could carry a product.

## Keyword fallback

`_keyword_fallback` checks phrases in this order: new-customer PPS, attribution, returning PPS, NPS, returns, reviews, CSAT, a named product, errors, help. Anything else becomes VOC at `low` confidence. A new-customer PPS phrase that also matches attribution stays on attribution. "Returning customers" does not count as a returns question. A named product goes to VOC before the error and help keywords, so "how do customers like the Atlas Short" is not a help request.

## Gaps that are still true

1. A question that names several products is scoped to the first one, and the reasoning says so.
2. `Daily` is not routable.

Golden cases live in `scout/tests/test_nl_routing.py`, including model failure, invalid model output, and the catalog being unavailable.
