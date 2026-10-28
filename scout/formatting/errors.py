"""Slack formatting."""

from scout.formatting._common import _no_data_message
from scout.health.error_rate import ErrorRateReport, rate_line_label
from scout.taxonomy.issue_tags import ERROR_RATE_GROUP

def format_errors(data: dict) -> str:
    """Format Errors results for Slack."""
    current = data.get("current", {})
    categories = data.get("categories", [])
    csat_comp = data.get("csat_comparison", [])
    samples = data.get("samples", [])
    timeframe = data.get("timeframe_label", "")
    days = data.get("days", 7)

    error_count = current.get("ERROR_CONVERSATIONS", 0)
    total = current.get("TOTAL_CONVERSATIONS", 0)

    if total == 0:
        return _no_data_message("Errors", timeframe, "Richpanel via Snowflake")

    report = data.get("error_rate")
    group = report.group if isinstance(report, ErrorRateReport) else ERROR_RATE_GROUP
    rate = report.rate("current") if isinstance(report, ErrorRateReport) else None
    prior = report.rate("previous") if isinstance(report, ErrorRateReport) else None
    error_rate = rate.value_pct if rate else None
    prev_error_rate = prior.value_pct if prior else None

    # Change
    rate_change = ""
    if error_rate is not None and prev_error_rate is not None and prev_error_rate > 0:
        change = error_rate - prev_error_rate
        rate_change = f" ({'+' if change > 0 else ''}{change:.1f} pts vs. previous {days} days)"

    if error_rate is None:
        headline = f"*:warning: The support error rate is unavailable for the last {days} complete days.*"
    else:
        headline = f"*:warning: Error-related tickets represented {error_rate:.1f}% of support volume over the last {days} complete days.*"

    lines = [headline, ""]

    # Error summary
    lines.append("*Error summary*")
    lines.append(f"• *Total error-related tickets:* {error_count}")
    if rate and error_rate is not None:
        lines.append(f"• *{rate_line_label(group)}:* {error_rate:.1f}% of conversations "
                     f"({rate.tagged:,} of {rate.total:,})")
    else:
        reason = rate.reason if rate else "not computed"
        lines.append(f"• *{rate_line_label(group)}:* unavailable ({reason})")
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
    lines.append(f"• The error rate counts tickets carrying {group} taxonomy tags. Total error-related tickets, categories, and CSAT impact use subject-line keyword matching (e.g., error, bug, checkout, payment, discount code).")

    return "\n".join(lines)

