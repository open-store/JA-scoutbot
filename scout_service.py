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


def execute_natural_language(text: str) -> tuple[str, dict]:
    """Route NL text via LLM and execute the resulting Scout command."""
    routing = route_natural_language(text)
    command_str = build_command_from_routing(routing)
    result = execute_scout_command(command_str)
    return result, routing
