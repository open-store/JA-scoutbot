# Natural-language routing

`scout/routing/nl_router.py` turns a free-text question into a slash-style command. A future question-type router replaces `route_natural_language`; see [question router](question-router.md). `scout_bot.execute_natural_language` is the only caller on the live path. It runs for @mentions and direct messages.

## Flow

1. Slack text arrives. Mention tokens are stripped.
2. `route_natural_language` calls `gpt-4.1-mini` (`temperature=0`, `max_tokens=200`, `response_format=json_object`).
3. The model returns JSON. `build_command_from_routing` turns it into a string such as `/VOC L30 product:"anytime crewneck"`.
4. `execute_scout_command` parses that string and runs the normal query and formatter.
5. If `confidence` is `low`, Scout prepends a note that it guessed, then still returns the report.

If the OpenAI call throws, or `OPENAI_API_KEY` is missing, `_keyword_fallback` runs instead. The client is cached on the first successful construction. The SDK also reads `OPENAI_BASE_URL` when that variable is set.

## Contract

```json
{
  "command": "VOC",
  "timeframe": "L30",
  "filters": {"product": "anytime crewneck"},
  "confidence": "high",
  "reasoning": "short explanation"
}
```

Allowed `command` values in the prompt: `CSAT`, `VOC`, `Errors`, `NPS`, `PPS`, `Attribution`, `Returns`, `Reviews`, `Help`.

Allowed `timeframe` values: `L7`, `L30`, `L180`. The prompt tells the model to default to `L30` when the user gives no window. Slash commands are different: `scout.routing.command_parser` defaults to `L7`.

`confidence` is `high`, `medium`, or `low`. Only `low` changes the Slack reply.

Filters the prompt asks for:

- `product` on product-specific VOC questions.
- `segment: returning` on returning-customer PPS questions.
- `segment: new` on new-customer PPS questions. That runs the same report as `/pps_new`. Acquisition questions stay on `Attribution`.

`Daily` is not a routable command.

## Keyword fallback

`_keyword_fallback` checks phrases in this order: new-customer PPS, attribution, returning PPS, NPS, returns, reviews, CSAT, errors, help. Anything else becomes VOC. A new-customer PPS phrase that also matches attribution stays on attribution.

CSAT and the VOC default use `L30` when the text contains "month" or "30", and `L7` otherwise. Attribution, PPS, NPS, returns, and reviews are hardcoded to `L30`. Errors and help use `L7`.

The fallback does not read product names, so a product question answered by the fallback loses the `product` filter.

## Gaps that are still true

1. **No schema check on model JSON.** The router `json.loads` the content and passes it through. A missing field or an unknown command string is not normalised. `response_format=json_object` only guarantees a JSON object.
2. **The keyword fallback does not extract a product filter**, and its timeframe rules are the coarse checks above.
3. **There is no golden-case test suite** for `route_natural_language`, `build_command_from_routing`, or `_keyword_fallback`.

`NPS`, `PPS`, `Attribution`, `Returns`, and `Reviews` are all routable on the live bot. `Daily` is not.
