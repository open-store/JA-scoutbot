#!/usr/bin/env python3
"""
Scout — Internal VOC & CX Data Agent
Main orchestrator: parses commands, routes to data sources, formats and returns results.
"""

import sys
import os

# Add scout directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from command_parser import parse_command, ParsedCommand
from formatters import format_csat, format_voc, format_errors, format_help, format_not_available
from queries.csat import run_csat
from queries.voc import run_voc
from queries.errors import run_errors


def execute_command(raw_input: str) -> str:
    """
    Parse and execute a Scout command.
    Returns a formatted Slack message string.
    """
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

    return f"Unknown command: `{cmd.command}`. Try `/Help` for available commands."


def main():
    """CLI entry point for testing."""
    if len(sys.argv) < 2:
        print("Usage: python scout.py '<command>'")
        print("Example: python scout.py '/CSAT L7'")
        sys.exit(1)

    raw_input = " ".join(sys.argv[1:])
    print(f"Processing: {raw_input}")
    print("-" * 60)
    result = execute_command(raw_input)
    print(result)


if __name__ == "__main__":
    main()
