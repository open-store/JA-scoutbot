"""Slack formatting."""

def format_help() -> str:
    """Format the /Help response using Slack mrkdwn."""
    lines = [
        "*:mag: Scout — Internal VOC & CX Data Agent*",
        "",
        "Scout surfaces customer insights from support data, NPS, returns, and reviews.",
        "",
        "*Available slash commands*",
        "• `/csat L7` — CSAT performance for the last 7 days",
        "• `/csat L30` — CSAT performance for the last 30 days",
        "• `/voc L7` — Top customer themes & contact reasons",
        "• `/voc L30` — Voice of Customer summary (last 30 days)",
        "• `/errors L7` — Error-related ticket analysis",
        "• `/errors L30` — Error trends & CSAT impact",
        "• `/nps L30` — NPS scores by survey (NPS Survey, Returning PPS, New Customer PPS)",
        "• `/pps L30` — Returning Customer PPS: why they came back, friction, themes",
        "• `/pps new L30` — New Customer PPS: recommend score and open-text themes",
        "• `/attribution L30` — New customer attribution: how they found us, what brought them",
        "• `/daily` — Yesterday's CS KPI report: tickets closed, FRT, RT, CSAT by agent",
        "• `/returns L30` — Return volume, top products, and type breakdown",
        "• `/reviews L30` — Product review analysis (Okendo)",
        "• `/scout-help` — This message",
        "",
        "*Timeframes*",
        "• `L7` = last 7 complete days",
        "• `L30` = last 30 complete days",
        "• `L180` = last 180 complete days",
        "• Default: `L7` if omitted",
        "",
        "*Filters* (optional)",
        "• `tag:shipping` — filter by tag",
        "• `channel:email` — filter by channel",
        "• `product:\"Everyday Hoodie\"` — filter by product",
        "",
        "*Natural language* — just ask me anything:",
        "• _@Scout what is our CSAT this week?_",
        "• _@Scout show me top complaints last month_",
        "• _@Scout what are customers saying about the Clubhouse Polo?_",
        "",
        "*Source:* Richpanel via Snowflake · KnoCommerce · Redo · Okendo",
    ]
    return "\n".join(lines)



def format_not_available(command: str) -> str:
    """Format response for commands not yet implemented."""
    return (
        f"*`/{command.upper()}` is coming soon.*\n\n"
        f"This data source integration is planned but not yet connected. "
        f"In the meantime, try `/csat`, `/voc`, or `/errors` for Richpanel-based insights.\n\n"
        f"*Source:* Not yet connected."
    )

