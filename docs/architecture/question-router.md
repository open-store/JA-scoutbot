# Where a question-type router would plug in

Yankho has a PRD for question types. That PRD is not in this repo, so this note does not name or invent types.

## Plug-in point

Plain-English questions (an @mention or a DM) go through one function:

`scout.routing.nl_router.route_natural_language`

`scout_bot.execute_natural_language` is the only live caller. It does three things:

1. `route_natural_language(text)` returns a routing dict (`command`, `timeframe`, `filters`, `confidence`, `reasoning`).
2. `build_command_from_routing` turns that dict into a slash-style string such as `/VOC L30`.
3. `execute_scout_command` runs that string the same way a slash command does.

A future question-type router replaces step 1. It should still return a routing dict that `build_command_from_routing` already understands, or it should become the thing `execute_natural_language` calls instead of `route_natural_language`. Slash commands do not go through this function. They call `scout.routing.command_parser.parse_command` from the handlers in `scout_bot.py`.

The keyword fallback (`_keyword_fallback`) stays behind the same function. It runs when the model call fails. Do not add a second router beside it.

## What not to change for that work

- `scout_bot.py` stays the process entry point (`python scout_bot.py`).
- Command handlers keep calling `execute_scout_command`.
- Do not fold the router into a query module or a formatter.
