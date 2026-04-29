"""
Scout Slack Formatters
Formats query results into Slack-ready messages using Slack's native mrkdwn syntax.
NOTE: Slack uses *bold* (single asterisk), not **bold** (double asterisk).
      Italic uses _text_, strikethrough uses ~text~, code uses `text`.
"""

from typing import Optional


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
    if top_theme:
        lines.append(f"• The dominant customer contact theme is *{top_theme}*, representing the largest share of tagged conversations.")
    if vol_change:
        lines.append(f"• Overall support volume is {total:,} conversations{vol_change}.")
    lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if top_theme:
        lines.append(f"• *CX + Ops:* Prioritize review of `{top_theme}` conversations for macro/process improvements.")
    else:
        lines.append("• *CX:* Review recent ticket subjects for emerging patterns.")

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
        "• `/nps L30` — Net Promoter Score (KnoCommerce)",
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


def _no_data_message(command: str, timeframe: str, source: str) -> str:
    """Standard no-data response."""
    return (
        f"*No {command} data found for {timeframe}.*\n\n"
        f"No matching records were found in the requested timeframe.\n\n"
        f"• *Source checked:* {source}\n"
        f"• *Suggestion:* Try a wider timeframe (e.g., `L30` or `L180`)."
    )


def _extract_themes_from_subjects(subjects: list[str]) -> list[tuple[str, int]]:
    """
    Simple keyword-based theme extraction from subject lines.
    Returns list of (theme, count) sorted by count descending.
    """
    theme_keywords = {
        "Shipping/Delivery": ["shipping", "delivery", "tracking", "shipped", "transit", "usps", "ups", "fedex", "delayed", "lost package", "where is my order", "wismo"],
        "Returns/Exchanges": ["return", "exchange", "refund", "send back", "return label", "rma"],
        "Sizing/Fit": ["size", "sizing", "fit", "too big", "too small", "measurements", "length"],
        "Discount/Promo Codes": ["discount", "promo", "coupon", "code", "promotion"],
        "Order Issues": ["order", "cancel", "cancellation", "wrong item", "missing item", "incomplete"],
        "Product Quality": ["quality", "defect", "damaged", "broken", "stain", "hole", "tear", "fabric"],
        "Account/Login": ["account", "login", "password", "sign in", "email"],
        "Subscription/Billing": ["subscription", "billing", "charge", "charged", "recurring"],
        "General Inquiry": ["question", "inquiry", "info", "information", "help"],
    }

    theme_counts = {}
    for subject in subjects:
        if not subject:
            continue
        subject_lower = subject.lower()
        for theme, keywords in theme_keywords.items():
            if any(kw in subject_lower for kw in keywords):
                theme_counts[theme] = theme_counts.get(theme, 0) + 1

    sorted_themes = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_themes


def format_nps(data: dict) -> str:
    """Format NPS results for Slack."""
    nps = data.get("nps")
    total = data.get("total_responses", 0)
    promoters = data.get("promoters", 0)
    passives = data.get("passives", 0)
    detractors = data.get("detractors", 0)
    promoter_pct = data.get("promoter_pct", 0)
    passive_pct = data.get("passive_pct", 0)
    detractor_pct = data.get("detractor_pct", 0)
    period_start = data.get("period_start", "")
    period_end = data.get("period_end", "")
    prior_nps = data.get("prior_nps")
    change = data.get("change")

    if nps is None or total == 0:
        return _no_data_message("NPS", f"{period_start} – {period_end}", "KnoCommerce API")

    # Headline
    if change is not None:
        if change > 0:
            headline = f"*:chart_with_upwards_trend: NPS improved over the measured period.*"
        elif change < 0:
            headline = f"*:chart_with_downwards_trend: NPS declined over the measured period.*"
        else:
            headline = f"*:bar_chart: NPS is flat over the measured period.*"
    else:
        headline = f"*:bar_chart: NPS for the measured period.*"

    lines = [headline, ""]

    # Metric summary
    lines.append("*Metric summary*")
    lines.append(f"• *NPS:* {nps:+.1f}")
    lines.append(f"• *Total responses:* {total}")
    lines.append(f"• *Promoters (9–10):* {promoters} ({promoter_pct}%)")
    lines.append(f"• *Passives (7–8):* {passives} ({passive_pct}%)")
    lines.append(f"• *Detractors (0–6):* {detractors} ({detractor_pct}%)")
    lines.append(f"• *Timeframe:* {period_start} – {period_end}")
    lines.append(f"• *Source:* KnoCommerce API")
    if change is not None:
        lines.append(f"• *Change:* {'+' if change > 0 else ''}{change:.1f} pts vs. prior period")
    lines.append("")

    # Score distribution
    dist = data.get("score_distribution", {})
    if dist:
        lines.append("*Score distribution*")
        for score in range(10, -1, -1):
            count = dist.get(score, 0)
            if count > 0:
                bar = "█" * min(count, 30)
                lines.append(f"• *{score:2d}:* {bar} ({count})")
        lines.append("")

    # Interpretation
    lines.append("*Interpretation*")
    if nps >= 50:
        lines.append("• NPS of 50+ is considered *excellent*. Customers are highly likely to recommend.")
    elif nps >= 0:
        lines.append("• NPS of 0–50 is considered *good*. Room to convert passives into promoters.")
    else:
        lines.append("• NPS below 0 indicates *more detractors than promoters*. Investigate root causes.")
    lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if detractor_pct > 20:
        lines.append("• *CX:* High detractor rate — review low-score responses for common pain points.")
    elif passive_pct > 40:
        lines.append("• *CX:* Large passive segment — identify what would convert them to promoters.")
    else:
        lines.append("• *CX:* Maintain current approach. Monitor for shifts in the promoter/detractor mix.")

    return "\n".join(lines)


def format_reviews(data: dict) -> str:
    """Format Okendo Reviews results for Slack."""
    avg_rating = data.get("avg_rating")
    total = data.get("total_reviews", 0)
    rating_dist = data.get("rating_distribution", {})
    sentiment = data.get("sentiment_breakdown", {})
    top_products = data.get("top_products", [])
    top_tags = data.get("top_tags", [])
    sample_pos = data.get("sample_positive")
    sample_neg = data.get("sample_negative")
    period_start = data.get("period_start", "")
    period_end = data.get("period_end", "")
    rating_change = data.get("rating_change")
    volume_change = data.get("volume_change_pct")
    product_filter = data.get("product_filter")

    if total == 0:
        filter_note = f" (filtered by {product_filter})" if product_filter else ""
        return _no_data_message("Reviews", f"{period_start} – {period_end}{filter_note}", "Okendo API")

    # Headline
    filter_context = f" for *{product_filter}*" if product_filter else ""
    if rating_change is not None:
        if rating_change > 0:
            headline = f"*:star: Reviews are trending up{filter_context} — avg rating improved.*"
        elif rating_change < 0:
            headline = f"*:star: Reviews are trending down{filter_context} — avg rating declined.*"
        else:
            headline = f"*:star: Review ratings are stable{filter_context}.*"
    else:
        headline = f"*:star: Review summary{filter_context}.*"

    lines = [headline, ""]

    # Metric summary
    lines.append("*Metric summary*")
    lines.append(f"• *Average rating:* {avg_rating:.2f} / 5.00")
    lines.append(f"• *Total reviews:* {total}")
    if volume_change is not None:
        lines.append(f"• *Volume change:* {'+' if volume_change > 0 else ''}{volume_change}% vs. prior period")
    if rating_change is not None:
        lines.append(f"• *Rating change:* {'+' if rating_change > 0 else ''}{rating_change:.2f} vs. prior period")
    lines.append(f"• *Timeframe:* {period_start} – {period_end}")
    if product_filter:
        lines.append(f"• *Filter:* {product_filter}")
    lines.append(f"• *Source:* Okendo API")
    lines.append("")

    # Rating distribution
    if rating_dist:
        lines.append("*Rating distribution*")
        for stars in range(5, 0, -1):
            count = rating_dist.get(stars, 0)
            pct = round(count / total * 100, 1) if total > 0 else 0
            bar = "★" * stars + "☆" * (5 - stars)
            lines.append(f"• {bar}  {count} ({pct}%)")
        lines.append("")

    # Sentiment breakdown
    if sentiment:
        lines.append("*Sentiment breakdown*")
        for s_type in ["positive", "negative", "neutral", "mixed"]:
            count = sentiment.get(s_type, 0)
            if count > 0:
                pct = round(count / total * 100, 1)
                lines.append(f"• *{s_type.capitalize()}:* {count} ({pct}%)")
        lines.append("")

    # Top products
    if top_products:
        lines.append("*Top reviewed products*")
        for p in top_products[:5]:
            lines.append(f"• *{p['product']}:* {p['count']} reviews (avg {p['avg_rating']:.1f}★)")
        lines.append("")

    # Sample reviews
    if sample_pos:
        lines.append("*Highlighted positive review*")
        lines.append(f"> _{sample_pos['body']}_ — *{sample_pos['product']}* ({sample_pos['rating']}★)")
        lines.append("")

    if sample_neg:
        lines.append("*Highlighted negative review*")
        lines.append(f"> _{sample_neg['body']}_ — *{sample_neg['product']}* ({sample_neg['rating']}★)")
        lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    neg_count = sentiment.get("negative", 0) + sentiment.get("mixed", 0)
    if neg_count > total * 0.2:
        lines.append("• *Product/CX:* Over 20% of reviews are negative or mixed — investigate common complaints.")
    elif avg_rating and avg_rating < 4.0:
        lines.append("• *Product:* Average rating below 4.0 — review low-rated products for quality or expectation gaps.")
    else:
        lines.append("• Reviews are healthy. Continue monitoring for emerging product issues.")

    return "\n".join(lines)


def format_returns(data: dict) -> str:
    """Format Returns results for Slack."""
    total_lines = data.get("total_lines", 0)
    total_qty = data.get("total_qty_returned", 0)
    exchanges = data.get("exchanges", 0)
    exchange_qty = data.get("exchange_qty", 0)
    cancellations = data.get("cancellations", 0)
    cancel_qty = data.get("cancel_qty", 0)
    straight_returns = data.get("straight_returns", 0)
    straight_qty = data.get("straight_qty", 0)
    redo_returns = data.get("redo_returns", 0)
    exchange_pct = data.get("exchange_pct", 0)
    cancel_pct = data.get("cancel_pct", 0)
    straight_pct = data.get("straight_pct", 0)
    redo_pct = data.get("redo_pct", 0)
    avg_days = data.get("avg_days_to_return")
    top_products = data.get("top_products", [])
    top_categories = data.get("top_categories", [])
    top_notes = data.get("top_refund_notes", [])
    platforms = data.get("platforms", [])
    period_start = data.get("period_start", "")
    period_end = data.get("period_end", "")
    volume_change = data.get("volume_change_pct")
    qty_change = data.get("qty_change_pct")
    product_filter = data.get("product_filter")

    if total_lines == 0:
        filter_note = f" (filtered by {product_filter})" if product_filter else ""
        return _no_data_message("Returns", f"{period_start} – {period_end}{filter_note}", "Snowflake")

    # Headline
    filter_context = f" for *{product_filter}*" if product_filter else ""
    if volume_change is not None:
        if volume_change > 5:
            headline = f"*:package: Return volume is up{filter_context} over the measured period.*"
        elif volume_change < -5:
            headline = f"*:package: Return volume is down{filter_context} over the measured period.*"
        else:
            headline = f"*:package: Return volume is stable{filter_context}.*"
    else:
        headline = f"*:package: Returns summary{filter_context}.*"

    lines = [headline, ""]

    # Metric summary
    lines.append("*Metric summary*")
    lines.append(f"• *Total return lines:* {total_lines:,}")
    lines.append(f"• *Total units returned:* {total_qty:,}")
    if volume_change is not None:
        lines.append(f"• *Volume change:* {'+' if volume_change > 0 else ''}{volume_change}% vs. prior period")
    if avg_days is not None:
        lines.append(f"• *Avg days to return:* {avg_days}")
    lines.append(f"• *Timeframe:* {period_start} – {period_end}")
    if product_filter:
        lines.append(f"• *Filter:* {product_filter}")
    lines.append(f"• *Source:* Snowflake (`EXPORT_CSX__RETURNS`)")
    lines.append("")

    # Return type breakdown (mutually exclusive)
    lines.append("*Return type breakdown*")
    lines.append(f"• *Exchanges:* {exchanges:,} lines / {exchange_qty:,} units ({exchange_pct}%)")
    lines.append(f"• *Cancellations:* {cancellations:,} lines / {cancel_qty:,} units ({cancel_pct}%)")
    lines.append(f"• *Straight returns/refunds:* {straight_returns:,} lines / {straight_qty:,} units ({straight_pct}%)")
    lines.append(f"• _Of all returns, {redo_returns:,} ({redo_pct}%) were processed via Redo_")
    lines.append("")

    # Platform breakdown
    if platforms:
        lines.append("*Return platform*")
        for p in platforms:
            lines.append(f"• *{p['platform']}:* {p['count']:,}")
        lines.append("")

    # Top returned products
    if top_products:
        lines.append("*Top returned products*")
        for p in top_products[:5]:
            lines.append(f"• *{p['product']}:* {p['qty']} units ({p['lines']} lines)")
        lines.append("")

    # Top categories
    if top_categories:
        lines.append("*Top categories*")
        for c in top_categories[:5]:
            lines.append(f"• *{c['category']}:* {c['qty']} units")
        lines.append("")

    # Top refund notes (actionable insights)
    if top_notes:
        lines.append("*Notable refund reasons* (excluding generic)")
        for n in top_notes[:5]:
            lines.append(f"• [{n['count']}] _{n['note'][:100]}_")
        lines.append("")

    # Recommended action
    lines.append("*Recommended action*")
    if top_products:
        top_prod = top_products[0]["product"]
        lines.append(f"• *Product/Ops:* Investigate `{top_prod}` — the most returned product this period.")
    if cancel_pct > 15:
        lines.append(f"• *Ops:* Cancellation rate is {cancel_pct}% — review fulfillment speed and pre-ship communication.")
    if not top_products and cancel_pct <= 15:
        lines.append("• Return volume is within normal range. Continue monitoring.")

    # Caveat
    lines.append("")
    lines.append("*Caveat*")
    lines.append("• True line-item return rate requires joining with `OS_ALL_ORDERS`. This report shows return volume and mix from the returns table only.")
    lines.append("• Return comments and primary/secondary reasons from Redo API are not yet integrated.")

    return "\n".join(lines)
