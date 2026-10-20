# Redo

Redo is the authoritative source for return records, quantities, statuses, exchange and refund outcomes, structured reasons, and customer comments. Scout reads Redo data from Snowflake only. It does not call the Redo API.

## Warehouse table

`ANALYTICS.RETURN_REDO`, as named by the business owner. Whether it is a schema of tables or a single table, its exact casing, its grain (return, item, or event), and its columns have not been read from Snowflake metadata. `python -m scout.returns.discovery` resolves the name and profiles each relation. See [returns data](../architecture/returns-data.md).

`/returns` still reads `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS`. That is a Shopify refund export, not the Redo table. The same doc explains how the two tables differ.

## APIs

- **REST.** `https://api.getredo.com/v2.2`, Bearer token, store-scoped `/stores/{storeId}/returns`, cursor pagination (`X-Page-Next`, `page-continue`). `REDO_API_KEY` is a reserved name and is not read by the code.
- **MCP.** `https://mcp.getredo.com/mcp` uses OAuth authorization code with PKCE. A bot cannot log in from a cold start. It is useful for interactive reconciliation, not for `/returns`.

## Decision

Report from the warehouse. Calling Redo on every `/returns` invocation was rejected. If a needed Redo field is missing from `ANALYTICS.RETURN_REDO`, add it to the warehouse sync rather than calling the API from Slack.
