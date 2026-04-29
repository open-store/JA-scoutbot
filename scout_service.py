"""
Scout shared command execution service.
Centralizes parse -> query -> format orchestration for CLI and Slack entrypoints.
"""

from command_parser import parse_command
from formatters import format_csat, format_voc, format_errors, format_help, format_not_available
from queries.csat import run_csat
from queries.voc import run_voc
from queries.errors import run_errors
from nl_router import route_natural_language, build_command_from_routing

VALID_ROUTING_COMMANDS = {"CSAT", "VOC", "Errors", "NPS", "Returns", "Reviews", "Help"}
VALID_ROUTING_TIMEFRAMES = {"L7", "L30", "L180"}
FILTER_CAPABLE_COMMANDS = {"VOC"}


def execute_scout_command(raw_input: str) -> str:
    """Parse and execute a Scout command string. Returns formatted Slack text."""
    cmd = parse_command(raw_input)

    if not cmd.is_valid:
        return cmd.error_message

    if cmd.command == "help":
        return format_help()

    if cmd.command == "csat":
        data = run_csat(cmd)
        return format_csat(data)

    if cmd.command == "voc":
        data = run_voc(cmd)
        return format_voc(data)

    if cmd.command == "errors":
        data = run_errors(cmd)
        return format_errors(data)

    if cmd.command in ("nps", "returns", "reviews"):
        return format_not_available(cmd.command)

    return (
        f"Unknown command `{cmd.command}`.\n"
        "Try `/scout-help` to see what Scout can do."
    )


def _sanitize_routing(routing: dict | None) -> dict:
    """Normalize potentially malformed router output to a safe, valid structure."""
    if not isinstance(routing, dict):
        return {
            "command": "VOC",
            "timeframe": "L7",
            "filters": {},
            "confidence": "low",
            "reasoning": "router output invalid; defaulted",
        }

    command = routing.get("command", "VOC")
    if command not in VALID_ROUTING_COMMANDS:
        command = "VOC"

    timeframe = routing.get("timeframe", "L7")
    if timeframe not in VALID_ROUTING_TIMEFRAMES:
        timeframe = "L7"

    confidence = routing.get("confidence", "low")
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    filters = routing.get("filters", {})
    if not isinstance(filters, dict):
        filters = {}

    reasoning = routing.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""

    return {
        "command": command,
        "timeframe": timeframe,
        "filters": filters,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def _build_low_confidence_message(routing: dict) -> str:
    command = routing.get("command", "VOC")
    timeframe = routing.get("timeframe", "L7")
    return (
        "I want to make sure I route this correctly before I pull data.\n\n"
        f"Best guess: `/{command} {timeframe}` (confidence: low).\n\n"
        "Could you confirm one of these?\n"
        "• `/CSAT L7`\n"
        "• `/VOC L30`\n"
        "• `/Errors L7`\n"
        "• `/scout-help`"
    )


def _build_unsupported_filter_message(routing: dict) -> str:
    command = routing.get("command", "VOC")
    filters = routing.get("filters", {})
    active_filters = ", ".join(f"{k}:{v}" for k, v in filters.items())
    return (
        f"I picked `/{command}`, but this command doesn't support filters yet ({active_filters}).\n"
        "Right now filters are supported for `/VOC` only.\n"
        "Try again as `/VOC <timeframe> <filter>` or remove filters from your question."
    )


def execute_natural_language(text: str) -> tuple[str, dict]:
    """Route NL text via LLM and execute the resulting Scout command."""
    raw_routing = route_natural_language(text)
    routing = _sanitize_routing(raw_routing)

    # Disambiguate low-confidence routes before executing expensive queries.
    if routing.get("confidence") == "low":
        return _build_low_confidence_message(routing), routing

    # Make unsupported filter usage explicit instead of silently dropping intent.
    if routing.get("filters") and routing.get("command") not in FILTER_CAPABLE_COMMANDS:
        return _build_unsupported_filter_message(routing), routing

    command_str = build_command_from_routing(routing)
    result = execute_scout_command(command_str)
    return result, routing
