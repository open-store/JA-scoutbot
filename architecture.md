# Scout Architecture Design

## Approach

Given the available tools (Slack MCP for messaging, Snowflake for data), Scout will be implemented as a Python-based agent that:

1. Receives commands via Slack (through the MCP integration)
2. Parses the command (CSAT, VOC, Errors, NPS, Returns, Reviews, Help)
3. Routes to the appropriate data source
4. Executes queries and formats results
5. Posts formatted responses back to Slack via MCP

## Architecture

```
User (Slack) --> Scout Agent (Python) --> Data Sources
                                          ├── Snowflake (Richpanel conversations)
                                          ├── KnoCommerce API (NPS) [future]
                                          ├── Redo API (Returns) [future]
                                          └── Okendo API (Reviews) [future]
                    |
                    v
              Slack MCP (post response)
```

## Module Structure

scout/
├── .env                    # Credentials (gitignored)
├── scout.py                # Main entry point / orchestrator
├── snowflake_client.py     # Snowflake connection and query execution
├── command_parser.py       # Parse slash commands and natural language
├── formatters.py           # Format results for Slack
├── queries/
│   ├── csat.py             # CSAT query logic
│   ├── voc.py              # VOC query logic
│   └── errors.py           # Errors query logic
└── tag_mapper.py           # Map tag UUIDs to human-readable names

## Key Design Decisions

1. Tags are UUIDs - need to build a tag frequency analysis and attempt to map via Richpanel API or subject-line analysis
2. CSAT uses SATISFACTION_RATING (1-5 scale) and SATISFACTION_RATING_TEXT (Amazing/Great/Okay/Bad/Terrible)
3. CSAT % = (ratings of 4 or 5) / (total rated conversations) * 100
4. L7 CSAT data is sparse (21 responses in current L7) - need to flag low sample sizes
5. Will use Slack MCP slack_send_message to post results
