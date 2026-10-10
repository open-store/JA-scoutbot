"""Seam for future MCP-backed apps.

Richpanel, OutroSignal, and Okendo may later be reached through MCP.
This protocol is the interface those clients would implement. It does
not open a connection, call a tool, or name a question type.

Today the live clients are the modules next to this file
(``richpanel``, ``okendo``, ``knocommerce``, ``snowflake``). Command
code keeps importing those. A later MCP client would satisfy
``McpApp`` without changing slash-command dispatch.
"""

from typing import Protocol


class McpApp(Protocol):
    """Read-only access to one external app via MCP."""

    name: str

    def configured(self) -> bool:
        """Whether this process has what it needs to talk to the app."""
        ...
