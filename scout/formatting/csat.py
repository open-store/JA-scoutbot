"""Slack formatting."""

from scout.formatting._common import _no_data_message
from queries.csat_themes import _extract_themes_from_subjects

def format_csat(data: dict) -> str:
    """Format CSAT results for Slack."""
    current = data.get("current", {})
    previous = data.get("previous", {})
    distribution = data.get("distribution", [])
    by_channel = data.get("by_channel", [])
    low_csat = data.get("low_csat_samples", [])
    timeframe = data.get("timeframe_label", "")
    days = data.get("days", 7)

    csat_pct = current.get("CSAT_PCT")
    total_rated = current.get("TOTAL_RATED", 0)
    total_convos = current.get("TOTAL_CONVERSATIONS", 0)
    prev_csat = previous.get("CSAT_PCT")

    # Headline
    if csat_pct is not None and total_rated > 0:
        change_str = ""
        if prev_csat is not None and prev_csat > 0:
            change = float(csat_pct) - float(prev_csat)
            direction = "up" if change > 0 else "down" if change < 0 else "flat"
            change_str = f" ({'+' if change > 0 else ''}{change:.1f} pts vs. previous {days} days)"
            if direction == "down":
                headline = f"*:chart_with_downwards_trend: CSAT is down over the last {days} complete days.*"
            elif direction == "up":
                headline = f"*:chart_with_upwards_trend: CSAT improved over the last {days} complete days.*"
            else:
                headline = f"*:bar_chart: CSAT is flat over the last {days} complete days.*"
        else:
            headline = f"*:bar_chart: CSAT for the last {days} complete days.*"
            change_str = ""
    else:
        return _no_data_message("CSAT", timeframe, "Richpanel via Snowflake")

    lines = [headline, ""]

    # Metric summary
    lines.append("*Metric summary*")
    lines.append(f"• *CSAT:* {csat_pct}%")
    lines.append(f"• *Responses:* {total_rated}")
    lines.append(f"• *Total conversations:* {total_convos:,}")
    response_rate = round(total_rated / total_convos * 100, 1) if total_convos > 0 else 0
    lines.append(f"• *Response rate:* {response_rate}%")
    lines.append(f"• *Timeframe:* {timeframe}")
    lines.append(f"• *Source:* Richpanel via Snowflake")
    if prev_csat is not None and change_str:
        lines.append(f"• *Change:* {change_str.strip(' ()')}")
    lines.append("")

    # Rating distribution
    if distribution:
        lines.append("*Rating breakdown*")
        for d in distribution:
            rating = d.get("SATISFACTION_RATING", "?")
            text = d.get("SATISFACTION_RATING_TEXT", "")
            cnt = d.get("CNT", 0)
            lines.append(f"• *{text}* ({rating}/5): {cnt} responses")
        lines.append("")

    # By channel
    if by_channel:
        lines.append("*CSAT by channel*")
        for ch in by_channel:
            channel = ch.get("CHANNEL", "unknown")
            ch_csat = ch.get("CSAT_PCT", "N/A")
            ch_rated = ch.get("TOTAL_RATED", 0)
            if ch_rated > 0:
                lines.append(f"• *{channel}:* {ch_csat}% ({ch_rated} responses)")
        lines.append("")

    # Low-CSAT themes
    if low_csat:
        lines.append("*Low-CSAT ticket themes* (from subject lines)")
        themes = _extract_themes_from_subjects([s.get("SUBJECT", "") for s in low_csat])
        for theme, count in themes[:5]:
            lines.append(f"• *{theme}:* {count} tickets")
        lines.append("")

    # Caveat
    if total_rated < 30:
        lines.append("*Caveat*")
        lines.append(f"• Low sample size ({total_rated} responses). Results are directional only.")
        lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if low_csat:
        top_theme = _extract_themes_from_subjects([s.get("SUBJECT", "") for s in low_csat])
        if top_theme:
            lines.append(f"• *CX + Ops:* Investigate `{top_theme[0][0]}` tickets — the leading low-CSAT driver this period.")
        else:
            lines.append("• *CX:* Review low-CSAT tickets for emerging patterns.")
    else:
        lines.append("• No low-CSAT tickets in this period — maintain current approach.")

    return "\n".join(lines)

