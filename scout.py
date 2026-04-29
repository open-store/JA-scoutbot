#!/usr/bin/env python3
"""
Scout — Internal VOC & CX Data Agent
Main orchestrator: parses commands, routes to data sources, formats and returns results.
"""

import sys
import os

# Add scout directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scout_service import execute_scout_command


def execute_command(raw_input: str) -> str:
    """Backwards-compatible wrapper for shared Scout executor."""
    return execute_scout_command(raw_input)


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
