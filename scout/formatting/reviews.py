"""Slack formatting."""

from scout.formatting._common import _no_data_message

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

