# Scout documentation

| Doc | What it covers |
|---|---|
| [architecture/overview.md](architecture/overview.md) | Live request flow, module map, and the VOC boundary |
| [architecture/nl-routing.md](architecture/nl-routing.md) | How plain-English questions become commands, and the gaps that remain |
| [architecture/question-router.md](architecture/question-router.md) | Where a future question-type router plugs in. Types are not listed here |
| [architecture/product-voc.md](architecture/product-voc.md) | Product-feedback pipeline and retrieval strategy |
| [architecture/customer-health.md](architecture/customer-health.md) | General `/voc`: multi-source scorecard, cited themes, and the versioned health score |
| [architecture/decisions.md](architecture/decisions.md) | How CSAT, errors, returns, and NPS are defined |
| [architecture/returns-data.md](architecture/returns-data.md) | Returns sources, lineage, order-data path, and the return-rate metric map |
| [integrations/snowflake.md](integrations/snowflake.md) | Connection env vars and which table each command uses |
| [integrations/richpanel-schema.md](integrations/richpanel-schema.md) | Conversations and messages schema, and how tags are stored |
| [integrations/richpanel-api.md](integrations/richpanel-api.md) | `/daily`: Richpanel endpoints, agent filtering, business-hours metrics |
| [integrations/knocommerce.md](integrations/knocommerce.md) | KnoCommerce survey tables in Snowflake, and how NPS is calculated |
| [integrations/okendo.md](integrations/okendo.md) | Okendo reviews API as `scout/integrations/okendo.py` calls it |
| [integrations/redo.md](integrations/redo.md) | Redo as the returns authority, its warehouse table, and why Scout does not call the API |
| [integrations/shopify.md](integrations/shopify.md) | Shopify order models in Snowflake, and why Scout does not call the Admin API |
| [operations/deployment.md](operations/deployment.md) | Build, push, pull, and restart on the GCE VM |
| [operations/configuration.md](operations/configuration.md) | Environment variable names and purposes |
| [operations/slack-app-setup.md](operations/slack-app-setup.md) | Socket Mode app, slash commands, scopes, and events |
| [product/stakeholder-overview.md](product/stakeholder-overview.md) | Non-technical overview of what Scout does today |
| [roadmap.md](roadmap.md) | What has shipped and what is still open |

Start at the [repository README](../README.md). Security contact and secret handling are in [SECURITY.md](../SECURITY.md).
