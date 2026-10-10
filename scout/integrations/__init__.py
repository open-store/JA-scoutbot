"""External data access.

Import the client you need. This package does not connect on import.

- ``scout.integrations.snowflake`` — RSA connection and query helpers
- ``scout.integrations.okendo`` — Okendo reviews HTTP API
- ``scout.integrations.knocommerce`` — KnoCommerce survey tables in Snowflake
- ``scout.integrations.richpanel`` — Richpanel HTTP API (``/daily``)
- ``scout.integrations.mcp`` — interface only, for a future MCP client

Returns stay in ``queries/returns.py``. That module is the home for a
later returns-rate and returns-feedback feature.
"""
