"""Slack formatting."""

from scout.formatting._common import _no_data_message

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

