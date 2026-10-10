"""Slack formatting."""

from scout.formatting._common import _no_data_message

def format_errors(data: dict) -> str:
    """Format Errors results for Slack."""
    current = data.get("current", {})
    previous = data.get("previous", {})
    categories = data.get("categories", [])
    csat_comp = data.get("csat_comparison", [])
    samples = data.get("samples", [])
    timeframe = data.get("timeframe_label", "")
    days = data.get("days", 7)

    error_count = current.get("ERROR_CONVERSATIONS", 0)
    total = current.get("TOTAL_CONVERSATIONS", 0)
    error_rate = current.get("ERROR_RATE_PCT", 0)
    prev_error_rate = previous.get("ERROR_RATE_PCT", 0)

    if total == 0:
        return _no_data_message("Errors", timeframe, "Richpanel via Snowflake")

    # Change
    rate_change = ""
    if prev_error_rate is not None and prev_error_rate > 0:
        change = float(error_rate or 0) - float(prev_error_rate)
        rate_change = f" ({'+' if change > 0 else ''}{change:.1f} pts vs. previous {days} days)"

    headline = f"*:warning: Error-related tickets represented {error_rate}% of support volume over the last {days} complete days.*"

    lines = [headline, ""]

    # Error summary
    lines.append("*Error summary*")
    lines.append(f"• *Total error-related tickets:* {error_count}")
    lines.append(f"• *Error rate:* {error_rate}% of conversations")
    if rate_change:
        lines.append(f"• *Change:* {rate_change.strip(' ()')}")
    lines.append(f"• *Timeframe:* {timeframe}")
    lines.append(f"• *Source:* Richpanel via Snowflake")
    lines.append("")

    # Top categories
    if categories:
        lines.append("*Top categories*")
        for cat in categories:
            name = cat.get("ERROR_CATEGORY", "Unknown")
            cnt = cat.get("CNT", 0)
            lines.append(f"• *{name}:* {cnt} tickets")
        lines.append("")

    # CSAT impact
    error_csat = None
    non_error_csat = None
    for c in csat_comp:
        if c.get("TICKET_TYPE") == "error":
            error_csat = c.get("CSAT_PCT")
        elif c.get("TICKET_TYPE") == "non_error":
            non_error_csat = c.get("CSAT_PCT")

    if error_csat is not None and non_error_csat is not None:
        diff = float(error_csat) - float(non_error_csat)
        lines.append("*CSAT impact*")
        lines.append(f"• *Error ticket CSAT:* {error_csat}%")
        lines.append(f"• *Non-error ticket CSAT:* {non_error_csat}%")
        lines.append(f"• *Gap:* {diff:+.1f} pts")
        lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if categories:
        top_cat = categories[0].get("ERROR_CATEGORY", "error tickets")
        lines.append(f"• *Growth/Ops:* Investigate `{top_cat}` — the largest error category this period.")
        if len(categories) > 1:
            second_cat = categories[1].get("ERROR_CATEGORY", "")
            lines.append(f"• *Engineering:* Review `{second_cat}` if the trend persists.")
    else:
        lines.append("• No clear error categories detected. Review ticket subjects manually.")

    # Caveat
    lines.append("")
    lines.append("*Caveat*")
    lines.append("• Error detection is based on subject-line keyword matching (e.g., error, bug, checkout, payment, discount code). Formal error tags are stored as UUIDs and require mapping for precise classification.")

    return "\n".join(lines)

