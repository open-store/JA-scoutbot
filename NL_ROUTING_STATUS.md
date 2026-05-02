# NL Routing Status Update

_Date: 2026-05-02_

## Current State

Scout currently supports natural-language (NL) questions via `@mentions` and DMs in both runtime entry points:
- `scout_bot.py` (Socket Mode)
- `bot_server.py` (FastAPI event endpoint)

Both runtimes route NL questions through `nl_router.py`, which calls an OpenAI chat-completions model (`gpt-4.1-mini`) with a strict JSON response contract.

## Routing Flow

1. User submits NL question in Slack mention or DM.
2. App strips mention tokens and passes text to `execute_natural_language(...)`.
3. `route_natural_language(...)` asks the model to classify:
   - command (`CSAT`, `VOC`, `Errors`, `NPS`, `Returns`, `Reviews`, `Help`)
   - timeframe (`L7`, `L30`, `L180`)
   - filters (including explicit product extraction)
   - confidence (`high`, `medium`, `low`)
4. `build_command_from_routing(...)` converts JSON → slash-style command string (e.g., `/VOC L30 product:"Anytime Crewneck"`).
5. Standard command execution path runs query + formatter.

## Strengths

- **Single NL router module** shared across runtimes keeps intent logic centralized.
- **Fallback behavior**: if LLM call fails, keyword fallback prevents total NL outage.
- **Product-aware prompting** explicitly instructs product extraction for VOC-like feedback requests.
- **Low-confidence UX guardrail** in Socket Mode warns users when routing is uncertain.

## Gaps / Risks

1. **Runtime capability mismatch**
   - Router can emit `NPS`, `Returns`, `Reviews`, but `bot_server.py` marks these as not available.
   - Result: valid NL intent may route to unavailable command on FastAPI runtime.

2. **Fallback does not preserve product filters**
   - `_keyword_fallback(...)` can detect broad intents but does not extract `product`.
   - Product-specific VOC questions may lose precision during fallback scenarios.

3. **Limited timeframe extraction in fallback**
   - Fallback only infers `L30` using simple `month/30` checks; otherwise often defaults to `L7`.

4. **No strict schema validation of model JSON**
   - The router trusts model output and directly loads JSON.
   - Missing fields or malformed values could propagate to execution.

## Recommended Next Steps

1. Add lightweight validation/normalization for router output:
   - enforce allowed command/timeframe enums
   - default missing fields safely
2. Expand fallback parser:
   - extract product mentions for common phrasing
   - improve timeframe extraction (week/month/quarter/last X days)
3. Align runtime support matrix:
   - either enable NPS/Returns/Reviews in `bot_server.py` or constrain router output there
4. Add NL routing tests:
   - golden cases for product questions
   - failure-mode tests for malformed LLM output and fallback parity
