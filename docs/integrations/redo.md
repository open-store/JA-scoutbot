# Redo

Redo is not integrated. `/returns` reads Snowflake only. The bot does not send the `REDO_API_KEY` variable anywhere. That name is reserved for a later client. Credentials are not in the repo, and a store id is not stored in code.

## What a future client would call

From the integration notes that were already in the repo (no secret values):

- Base URL: `https://api.getredo.com/v2.2`
- Auth: Bearer token
- Store-scoped path: `/stores/{storeId}/returns`
- Pagination: cursor (`X-Page-Next`, `page-continue`)
- An MCP endpoint exists at `https://mcp.getredo.com/mcp` and is unused

Fields Scout would want on each return, to enrich `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS`:

- Customer comments
- Primary reason
- Secondary reason

## Decision

Skip the live API until credentials exist. Keep reporting volume and mix from Snowflake. A later job can sync Redo into Snowflake. Calling Redo on every `/returns` invocation is the option that was rejected.

The line-item return rate, once all orders are in the join, is `qty_returned / gross_quantity`. The returns export alone cannot produce it. See [decisions](../architecture/decisions.md).
