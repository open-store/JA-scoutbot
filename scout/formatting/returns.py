"""Slack formatting for `/returns`."""

from scout.formatting._common import _no_data_message
from scout.returns.metrics import MetricStatus, RatesReport, RateResult

_RATE_NOTE = (
    "Rates are Redo units ÷ eligible delivered Shopify units; exchange share is "
    "Redo exchanged ÷ Redo returned units."
)


def _format_rate(result: RateResult) -> str:
    label = result.definition.label
    if result.status is MetricStatus.UNAVAILABLE or result.value_pct is None:
        return f"• *{label}:* unavailable"
    line = (
        f"• *{label}:* {result.value_pct:.1f}% "
        f"({result.numerator:,} of {result.denominator:,} {result.definition.denominator_label})"
    )
    if result.status is MetricStatus.PROVISIONAL:
        line += f" — _provisional: {result.reason}_"
    return line


def format_rates(report: RatesReport) -> list[str]:
    lines = [f"*Return and exchange rates* (delivery cohorts, {report.scope_label})"]
    lines += [_format_rate(r) for r in report.results]
    if report.window is not None:
        lines.append(f"_Cohort: {report.window.describe()}._")
    if report.outcomes is not None:
        o = report.outcomes
        lines.append(
            f"_Returned units by outcome: {o.refunded:,} refunded, {o.exchanged:,} exchanged, "
            f"{o.store_credit:,} store credit, {o.open:,} open, {o.unexplained:,} other or unexplained._"
        )
    lines.append(f"_{_RATE_NOTE}_")
    if report.blockers:
        lines.append(f"_Not available yet: {'; '.join(report.blockers)}._")
    lines.append("")
    return lines


def format_returns(data: dict) -> str:
    """Format `/returns` for Slack."""
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
    product_filter = data.get("product_filter")
    rates = data.get("rates")

    if total_lines == 0:
        filter_note = f" (filtered by {product_filter})" if product_filter else ""
        return _no_data_message("Returns", f"{period_start} – {period_end}{filter_note}", "Snowflake")

    filter_context = f" for *{product_filter}*" if product_filter else ""
    if volume_change is not None:
        if volume_change > 5:
            headline = f"*:package: Refund volume is up{filter_context} over the measured period.*"
        elif volume_change < -5:
            headline = f"*:package: Refund volume is down{filter_context} over the measured period.*"
        else:
            headline = f"*:package: Refund volume is stable{filter_context}.*"
    else:
        headline = f"*:package: Returns summary{filter_context}.*"

    lines = [headline, ""]

    if isinstance(rates, RatesReport):
        lines += format_rates(rates)

    lines.append("*Refund volume* (by refund date)")
    lines.append(f"• *Refund lines:* {total_lines:,}")
    lines.append(f"• *Units refunded:* {total_qty:,}")
    if volume_change is not None:
        lines.append(f"• *Volume change:* {'+' if volume_change > 0 else ''}{volume_change}% in refund lines vs. prior period")
    if avg_days is not None:
        lines.append(f"• *Avg days to return:* {avg_days}")
    lines.append(f"• *Refund dates:* {period_start} – {period_end} (ET)")
    if product_filter:
        lines.append(f"• *Filter:* {product_filter}")
    lines.append(
        "• *Source:* Shopify refund export (`EXPORT_CSX__RETURNS`): Redo and non-Redo refunds, "
        "including cancellations; open returns are not included"
    )
    lines.append("")

    lines.append("*Refund mix* (share of refund lines, not a rate against orders)")
    lines.append(f"• *Exchanges:* {exchanges:,} lines / {exchange_qty:,} units ({exchange_pct}% of lines)")
    lines.append(f"• *Cancellations:* {cancellations:,} lines / {cancel_qty:,} units ({cancel_pct}% of lines)")
    lines.append(f"• *Straight refunds:* {straight_returns:,} lines / {straight_qty:,} units ({straight_pct}% of lines)")
    lines.append(f"• _Redo-flagged: {redo_returns:,} lines ({redo_pct}% of lines)_")
    lines.append("")

    if platforms:
        lines.append("*Return platform* (refund lines)")
        for p in platforms:
            lines.append(f"• *{p['platform']}:* {p['count']:,}")
        lines.append("")

    if top_products:
        lines.append("*Most refunded products* (units)")
        for p in top_products[:5]:
            lines.append(f"• *{p['product']}:* {p['qty']} units ({p['lines']} lines)")
        lines.append("")

    if top_categories:
        lines.append("*Most refunded categories* (units)")
        for c in top_categories[:5]:
            lines.append(f"• *{c['category']}:* {c['qty']} units")
        lines.append("")

    if top_notes:
        lines.append("*Internal refund notes* (Shopify `REFUND_NOTE`, generic notes excluded; not customer reasons)")
        for n in top_notes[:5]:
            lines.append(f"• [{n['count']}] _{n['note'][:100]}_")
        lines.append("")

    lines.append("*Recommended action*")
    if top_products:
        top_prod = top_products[0]["product"]
        lines.append(
            f"• *Product/Ops:* `{top_prod}` has the most refunded units. Volume follows sales, "
            "so check its item return rate before acting."
        )
    if cancel_pct > 15:
        lines.append(
            f"• *Ops:* Cancellations are {cancel_pct}% of refund lines — review fulfillment speed "
            "and pre-ship communication."
        )
    if not top_products and cancel_pct <= 15:
        lines.append("• Refund volume is within normal range. Continue monitoring.")

    lines.append("")
    lines.append("*Caveat*")
    lines.append(
        "• Refund volume and mix come from Shopify refunds, not Redo. Open returns are missing, "
        "and exchanges that created no refund may be too."
    )
    lines.append("• Redo return reasons and customer comments are not shown yet.")

    return "\n".join(lines)
