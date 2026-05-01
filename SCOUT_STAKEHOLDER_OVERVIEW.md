# Scout: Internal VOC & CX Data Agent

**Prepared for:** Jack Archer CX & Analytics Team
**Date:** April 29, 2026

---

## Executive Summary

Scout is an internal Slack-based data agent designed to surface real-time Customer Experience (CX) and Voice of Customer (VOC) insights directly where the team works. By connecting directly to our Snowflake data warehouse and key CX platforms, Scout eliminates the friction of pulling reports manually, allowing any team member to query customer sentiment, return trends, and support metrics in seconds.

Scout operates entirely within Slack, supporting both structured slash commands for instant reporting and natural language queries for conversational exploration.

---

## Current Capabilities (v1.0)

Scout is currently live and deployed via a persistent Socket Mode connection, requiring no public endpoints or firewall exceptions. It supports the following core reporting functions:

### 1. Voice of Customer (`/voc`)
Scout analyzes support conversations to identify the top drivers of customer contact. 
- **How it works:** Queries the `FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR` schema in Snowflake.
- **Key feature:** Uses a deterministic mapping of 106 Richpanel tag UUIDs to human-readable themes (e.g., `order-delays`, `product-size-fit-issue`), automatically filtering out system noise like auto-closed social media tickets.
- **Filtering:** Supports filtering by specific products, tags, or channels.

### 2. Customer Satisfaction (`/csat`)
Scout tracks CSAT performance and identifies the specific themes driving negative experiences.
- **How it works:** Aggregates CSAT scores from Richpanel data in Snowflake.
- **Key feature:** Calculates period-over-period changes (e.g., L30 vs. prior L30) and isolates the root causes of low scores.

### 3. Return Trends (`/returns`)
Scout provides visibility into return volume and behavior.
- **How it works:** Queries the `ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS` table in Snowflake.
- **Key feature:** Accurately segments return behavior into three mutually exclusive buckets: Exchanges, Cancellations, and Straight Returns/Refunds. It also highlights the percentage of returns processed via Redo versus manual handling, and identifies the most frequently returned products.

### 4. Product Reviews (`/reviews`)
Scout summarizes product sentiment and highlights actual customer feedback.
- **How it works:** Connects directly to the Okendo Merchant REST API.
- **Key feature:** Aggregates average ratings, calculates positive sentiment percentage, and extracts verbatim positive and negative quotes from recent reviews.

### 5. Error Tracking (`/errors`)
Scout isolates support tickets related to operational or systemic errors.
- **How it works:** Filters Snowflake data for error-specific tags (e.g., `discount-promo-code-issue`, `lost-in-transit`).
- **Key feature:** Compares the CSAT of error-related tickets against the baseline CSAT to quantify the impact of operational failures on customer satisfaction.

### Natural Language Routing
In addition to slash commands, users can simply `@Scout` or DM the bot with natural language questions (e.g., *"What are customers saying about the Clubhouse Polo?"*). Scout uses an LLM to interpret the intent, map it to the correct underlying query, apply the relevant filters, and return the data.

---

## Architecture & Infrastructure

Scout is built for stability, security, and low latency:

| Component | Implementation |
|---|---|
| **Hosting** | Deployed 24/7 on Railway as a background worker process. |
| **Slack Integration** | Uses Slack Bolt with Socket Mode (WebSocket connection). No public webhooks required. |
| **Data Layer** | Primary queries execute against Snowflake. Okendo is queried via direct API. |
| **Intent Routing** | OpenAI-compatible LLM translates natural language into structured query parameters. |

---

## Roadmap & Expansion Potential

Scout's modular architecture makes it highly extensible. The following initiatives represent the immediate roadmap for expanding Scout's value to the organization.

### Phase 1: Near-Term Enhancements (Next 30 Days)

**1. KnoCommerce NPS Integration (`/nps`)**
- **Goal:** Surface Net Promoter Score trends and verbatim feedback.
- **Approach:** Resolve current API authentication blockers to enable direct querying of the KnoCommerce API, mirroring the `/reviews` functionality.

**2. Redo Return Reasons Enrichment**
- **Goal:** Add qualitative context to the `/returns` command by surfacing customer comments and primary/secondary return reasons.
- **Approach:** Since querying the Redo API live for every return is inefficient, we will implement a scheduled enrichment job (or leverage a Fivetran connector) to sync Redo API data into Snowflake. Scout will then query this enriched Snowflake table.

**3. True Line-Item Return Rate**
- **Goal:** Calculate the accurate return percentage across all orders, rather than just items that entered the return flow.
- **Approach:** Create a Snowflake View or materialized table that joins the current returns table (`EXPORT_CSX__RETURNS`) with the master orders table (`OS_ALL_ORDERS`). Scout will query this pre-aggregated view to maintain low latency.

### Phase 2: Strategic Expansion (Next 90 Days)

**1. Unified Snowflake Data Model**
- **Goal:** Eliminate direct API calls (Okendo, KnoCommerce) to reduce latency and simplify credential management.
- **Approach:** Utilize existing Fivetran budget to build connectors for Okendo and KnoCommerce, syncing all CX data into Snowflake. Scout's query modules will be updated to point exclusively to Snowflake.

**2. Advanced Natural Language Generation (NLG)**
- **Goal:** Move beyond structured metric outputs to conversational, synthesized insights.
- **Approach:** Instead of just using the LLM to *route* the query, pass the resulting Snowflake data back to the LLM to generate a plain-English summary (e.g., *"CSAT dropped 3 points this week, primarily driven by a spike in shipping delays for the Everyday Hoodie."*).

**3. Proactive Alerting**
- **Goal:** Shift Scout from a reactive query tool to a proactive monitoring agent.
- **Approach:** Implement scheduled background tasks where Scout monitors Snowflake for anomalies (e.g., a sudden 20% spike in `lost-in-transit` tags) and automatically pushes an alert to the `#cx-heads` channel.
