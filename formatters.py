"""
Scout Slack Formatters
Formats query results into Slack-ready messages using Slack's native mrkdwn syntax.
NOTE: Slack uses *bold* (single asterisk), not **bold** (double asterisk).
      Italic uses _text_, strikethrough uses ~text~, code uses `text`.
"""

from typing import Optional
from theme_classifier import summarize_subject_themes


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
        theme_summary = summarize_subject_themes([s.get("SUBJECT", "") for s in low_csat])
        themes = theme_summary["themes"]
        for theme, count in themes[:5]:
            lines.append(f"• *{theme}:* {count} tickets")
        lines.append("")

    # Caveat
    if low_csat:
        lines.append(f"• Theme coverage (low-CSAT sample): {theme_summary['coverage_pct']}% classified, {theme_summary['unclassified_pct']}% unclassified.")
        lines.append("")

    if total_rated < 30:
        lines.append("*Caveat*")
        lines.append(f"• Low sample size ({total_rated} responses). Results are directional only.")
        lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if low_csat:
        top_theme = summarize_subject_themes([s.get("SUBJECT", "") for s in low_csat])["themes"]
        if top_theme:
            lines.append(f"• *CX + Ops:* Investigate `{top_theme[0][0]}` tickets — the leading low-CSAT driver this period.")
        else:
            lines.append("• *CX:* Review low-CSAT tickets for emerging patterns.")
    else:
        lines.append("• No low-CSAT tickets in this period — maintain current approach.")

    return "\n".join(lines)


def format_voc(data: dict) -> str:
    """Format VOC results for Slack."""
    volume = data.get("volume", {})
    prev_volume = data.get("prev_volume", {})
    by_channel = data.get("by_channel", [])
    tags = data.get("tags", [])
    prev_tags = data.get("prev_tags", [])
    subjects = data.get("subjects", [])
    status = data.get("status", [])
    timeframe = data.get("timeframe_label", "")
    days = data.get("days", 7)

    total = volume.get("TOTAL_CONVERSATIONS", 0)
    prev_total = prev_volume.get("TOTAL_CONVERSATIONS", 0)
    csat_pct = volume.get("CSAT_PCT")
    filter_label = data.get("filter_label", "")

    if total == 0:
        filter_note = f" (filtered by {filter_label})" if filter_label else ""
        return _no_data_message("VOC", timeframe + filter_note, "Richpanel via Snowflake")

    # Volume change
    vol_change = ""
    if prev_total > 0:
        pct_change = round((total - prev_total) / prev_total * 100, 1)
        vol_change = f" ({'+' if pct_change > 0 else ''}{pct_change}% vs. previous {days} days)"

    # Extract themes from subjects
    theme_summary = summarize_subject_themes([s.get("SUBJECT", "") for s in subjects])
    themes = theme_summary["themes"]

    # Headline — include filter context if active
    filter_context = f" (filtered by {filter_label})" if filter_label else ""
    if themes:
        headline = f"*:speech_balloon: Top customer theme over the last {days} complete days{filter_context}: {themes[0][0]}.*"
    else:
        headline = f"*:speech_balloon: Customer voice summary for the last {days} complete days{filter_context}.*"

    lines = [headline, ""]

    # Metric summary
    lines.append("*Metric summary*")
    lines.append(f"• *Total conversations:* {total:,}{vol_change}")
    if csat_pct is not None:
        lines.append(f"• *Overall CSAT:* {csat_pct}%")
    lines.append(f"• *Timeframe:* {timeframe}")
    if filter_label:
        lines.append(f"• *Filter:* {filter_label}")
    lines.append(f"• *Source:* Richpanel via Snowflake")
    lines.append("")

    # Channel breakdown
    if by_channel:
        lines.append("*Volume by channel*")
        for ch in by_channel[:6]:
            channel = ch.get("CHANNEL", "unknown")
            cnt = ch.get("CNT", 0)
            pct = round(cnt / total * 100, 1) if total > 0 else 0
            lines.append(f"• *{channel}:* {cnt:,} ({pct}%)")
        lines.append("")

    # Top themes
    if themes:
        lines.append("*Top customer themes* (from subject lines)")
        for theme, count in themes[:5]:
            pct = round(count / len(subjects) * 100, 1) if subjects else 0
            lines.append(f"• *{theme}:* ~{count} conversations ({pct}% of sampled)")
        lines.append("")

    # Tag analysis
    if tags:
        lines.append("*Top tags* (by UUID — tag names pending mapping)")
        prev_tag_map = {t["TAG_UUID"]: t["CNT"] for t in prev_tags}
        for t in tags[:7]:
            uuid = t.get("TAG_UUID", "?")
            cnt = t.get("CNT", 0)
            prev_cnt = prev_tag_map.get(uuid, 0)
            change = ""
            if prev_cnt > 0:
                pct_change = round((cnt - prev_cnt) / prev_cnt * 100, 1)
                change = f" ({'+' if pct_change > 0 else ''}{pct_change}% vs. prior)"
            lines.append(f"• `{uuid[:8]}...`: {cnt} conversations{change}")
        lines.append("")

    # So what
    lines.append("*So what*")
    if themes:
        lines.append(f"• The dominant customer contact theme is *{themes[0][0]}*, representing the largest share of recent conversations.")
    if vol_change:
        lines.append(f"• Overall support volume is {total:,} conversations{vol_change}.")
    lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if themes:
        lines.append(f"• *CX + Ops:* Prioritize review of `{themes[0][0]}` conversations for macro/process improvements.")
    else:
        lines.append("• *CX:* Review recent ticket subjects for emerging patterns.")

    # Caveat
    lines.append("")
    lines.append("*Caveat*")
    lines.append("• Theme extraction is based on deterministic subject-line keyword analysis with single-theme assignment per subject.")
    lines.append(f"• Theme coverage: {theme_summary['coverage_pct']}% classified, {theme_summary['unclassified_pct']}% unclassified.")
    lines.append("• Tags are displayed as UUIDs pending Richpanel tag name mapping.")

    return "\n".join(lines)


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
        "• `/nps L30` — Net Promoter Score (KnoCommerce) _(coming soon)_",
        "• `/returns L30` — Return reasons and trends _(coming soon)_",
        "• `/reviews L30` — Product review analysis (Okendo) _(coming soon)_",
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


def _no_data_message(command: str, timeframe: str, source: str) -> str:
    """Standard no-data response."""
    return (
        f"*No {command} data found for {timeframe}.*\n\n"
        f"No matching records were found in the requested timeframe.\n\n"
        f"• *Source checked:* {source}\n"
        f"• *Suggestion:* Try a wider timeframe (e.g., `L30` or `L180`)."
    )


