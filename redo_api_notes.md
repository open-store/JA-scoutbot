# Redo API Notes

## Key Facts
- Base URL: https://api.getredo.com/v2.2
- Auth: Bearer token with API secret
- Store-scoped: /stores/{storeId}/...
- Pagination: Cursor-based (X-Page-Next, page-continue)
- Returns endpoint: GET /stores/{storeId}/returns
- Return details include: comments, primary reason, secondary reason
- Also has MCP server at https://mcp.getredo.com/mcp (OAuth)

## What we need from Redo
- Return comments (customer comments on why they're returning)
- Primary return reason
- Secondary return reason
- These would enrich the Snowflake table ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS

## Missing info needed
- storeId
- API secret/Bearer token
- We don't have Redo API credentials yet

## User's preference
- For now: skip Redo API, use Snowflake returns table without comments
- Later: add Redo API to pull comments, primary/secondary reasons
- Return rate formula: qty_returned / gross_quantity (line-item level)
