"""Shared Slack mrkdwn helpers."""

def _no_data_message(command: str, timeframe: str, source: str) -> str:
    """Standard no-data response."""
    return (
        f"*No {command} data found for {timeframe}.*\n\n"
        f"No matching records were found in the requested timeframe.\n\n"
        f"• *Source checked:* {source}\n"
        f"• *Suggestion:* Try a wider timeframe (e.g., `L30` or `L180`)."
    )



def _bar(pct: float, width: int = 20) -> str:
    """Render a simple text bar proportional to pct (0-100)."""
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)



def _nps_emoji(nps: float) -> str:
    if nps >= 50:
        return ":large_green_circle:"
    elif nps >= 0:
        return ":large_yellow_circle:"
    else:
        return ":red_circle:"



def _delta_str(delta) -> str:
    if delta is None:
        return "n/a"
    sign = "▲" if delta > 0 else ("▼" if delta < 0 else "")
    return f"{sign}{abs(delta):.1f}"

