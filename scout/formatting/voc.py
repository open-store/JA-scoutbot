"""Slack formatting."""

from scout.formatting._common import _no_data_message

def format_voc(data: dict) -> str:
    """Format VOC results for Slack using resolved tag names for themes."""
    volume = data.get("volume", {})
    prev_volume = data.get("prev_volume", {})
    by_channel = data.get("by_channel", [])
    tags = data.get("tags", [])
    prev_tags = data.get("prev_tags", [])
    status = data.get("status", [])
    timeframe = data.get("timeframe_label", "")
    days = data.get("days", 7)

    total = volume.get("TOTAL_CONVERSATIONS", 0)
    prev_total = prev_volume.get("TOTAL_CONVERSATIONS", 0)
    csat_pct = volume.get("CSAT_PCT")
    filter_label = data.get("filter_label", "")
    product_feedback = data.get("product_feedback")

    # When a product filter is active and the new pipeline found conversations,
    # use the pipeline count as the authoritative total — the subject-line query
    # may return 0 even when the tag+body pipeline finds real data.
    if total == 0 and product_feedback and product_feedback.get("total_conversations", 0) > 0:
        total = product_feedback["total_conversations"]

    if total == 0:
        filter_note = f" (filtered by {filter_label})" if filter_label else ""
        return _no_data_message("VOC", timeframe + filter_note, "Richpanel via Snowflake")

    # Volume change
    vol_change = ""
    if prev_total > 0:
        pct_change = round((total - prev_total) / prev_total * 100, 1)
        vol_change = f" ({'+' if pct_change > 0 else ''}{pct_change}% vs. previous {days} days)"

    # Build previous tag lookup for period-over-period comparison
    prev_tag_map = {t["TAG_UUID"]: t["CNT"] for t in prev_tags}

    # Top theme from resolved tags
    top_theme = tags[0].get("TAG_NAME", "unknown") if tags else None

    # Headline — include filter context if active
    filter_context = f" (filtered by {filter_label})" if filter_label else ""
    if top_theme:
        headline = f"*:speech_balloon: Top customer theme over the last {days} complete days{filter_context}: {top_theme}.*"
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

    # Top themes from Richpanel tags (resolved names)
    if tags:
        lines.append("*Top customer themes* (from Richpanel tags)")
        for t in tags[:8]:
            tag_name = t.get("TAG_NAME", "unknown")
            cnt = t.get("CNT", 0)
            uuid = t.get("TAG_UUID", "")
            prev_cnt = prev_tag_map.get(uuid, 0)
            change = ""
            if prev_cnt > 0:
                pct_chg = round((cnt - prev_cnt) / prev_cnt * 100, 1)
                change = f" ({'+' if pct_chg > 0 else ''}{pct_chg}% vs. prior)"
            pct_of_total = round(cnt / total * 100, 1) if total > 0 else 0
            lines.append(f"• *{tag_name}:* {cnt:,} conversations ({pct_of_total}%){change}")
        lines.append("")

    # Product feedback synthesis (only when product filter is active)
    if product_feedback and product_feedback.get("sample_count", 0) > 0:
        pf_headline = product_feedback.get("headline", "")
        pf_themes = product_feedback.get("themes", [])
        pf_count = product_feedback.get("sample_count", 0)
        pf_convos = product_feedback.get("total_conversations", 0)
        pf_low_sample = product_feedback.get("low_sample", False)
        pf_caveats = product_feedback.get("caveats", [])

        lines.append(f"*:mag: What customers are saying*")
        if pf_headline:
            lines.append(f"_{pf_headline}_")
        lines.append("")
        lines.append(f"• *Sample:* {pf_count} customer messages from {pf_convos} product-related tickets")
        lines.append(f"• *Timeframe:* Last {days} complete days")
        lines.append(f"• *Source:* {product_feedback.get('source', 'Richpanel tickets via Snowflake')}")
        lines.append("")

        if pf_low_sample:
            lines.append(f"_:warning: Low sample size ({pf_count} messages). Results are directional — try a longer timeframe (e.g. L180) for more data._")
            lines.append("")

        if pf_themes:
            lines.append("*Top themes*")
            for theme in pf_themes:
                label = theme.get("label", "")
                count = theme.get("mention_count", "")
                quote = theme.get("representative_quote", "")
                count_str = f" — {count} mentions" if count else ""
                quote_str = f"  \"_{quote}_\"" if quote else ""
                lines.append(f"• *{label}*{count_str}{quote_str}")
            lines.append("")

        if pf_caveats:
            for caveat in pf_caveats:
                lines.append(f"_{caveat}_")
            lines.append("")
    elif product_feedback is not None:
        lines.append("*:mag: What customers are saying*")
        pf_headline = product_feedback.get("headline", "")
        if pf_headline:
            lines.append(f"_{pf_headline}_")
        else:
            lines.append("_No substantive customer messages found for this product in this period. Auto-replies and marketing noise have been excluded. Try a longer timeframe (e.g. L180)._")
        lines.append("")

    # Status breakdown
    if status:
        lines.append("*Ticket status*")
        for s in status[:5]:
            st = s.get("STATUS", "unknown")
            cnt = s.get("CNT", 0)
            pct = round(cnt / total * 100, 1) if total > 0 else 0
            lines.append(f"• *{st}:* {cnt:,} ({pct}%)")
        lines.append("")

    # So what
    lines.append("*So what*")
    if product_feedback and product_feedback.get("so_what"):
        lines.append(f"• {product_feedback['so_what']}")
    elif top_theme:
        lines.append(f"• The dominant customer contact theme is *{top_theme}*, representing the largest share of tagged conversations.")
    if vol_change:
        lines.append(f"• Overall support volume is {total:,} conversations{vol_change}.")
    lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if product_feedback and product_feedback.get("recommended_action"):
        lines.append(f"• {product_feedback['recommended_action']}")
    elif product_feedback and product_feedback.get("themes"):
        top_pf_theme = product_feedback["themes"][0].get("label", "")
        lines.append(f"• *Product:* Top customer feedback theme is *{top_pf_theme}* — consider for V2 design decisions.")
    elif top_theme:
        lines.append(f"• *CX + Ops:* Prioritize review of `{top_theme}` conversations for macro/process improvements.")
    else:
        lines.append("• *CX:* Review recent ticket subjects for emerging patterns.")

    return "\n".join(lines)

