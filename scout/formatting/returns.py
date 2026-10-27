"""Slack formatting for `/returns`."""

from scout.formatting._common import _no_data_message
from scout.returns.comments import CommentSynthesis
from scout.returns.metrics import MetricStatus, RatesReport, RateResult
from scout.returns.period import PeriodRate, PeriodReport
from scout.returns.reasons import ReasonSummary
from scout.returns.recommendations import MonitoringView, RankedProduct

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


def _format_period_rate(rate: PeriodRate) -> list[str]:
    if rate.status is MetricStatus.UNAVAILABLE or rate.value_pct is None:
        lines = [f"• *{rate.window.label}:* unavailable — {rate.reason}"]
    else:
        lines = [
            f"• *{rate.window.label} ({rate.window.describe()}):* {rate.value_pct:.1f}% "
            f"({rate.orders_with_return:,} orders with a return submitted "
            f"÷ {rate.orders_placed:,} orders placed)"
            f" — _provisional: {rate.reason}_"
        ]
    raw = "n/a" if rate.return_count is None else f"{rate.return_count:,}"
    unmatched = "n/a" if rate.unmatched_returns is None else f"{rate.unmatched_returns:,}"
    lines.append(f"  • *Raw returns submitted:* {raw}")
    lines.append(f"  • *Unmatched returns:* {unmatched}")
    return lines


def format_period_rates(report: PeriodReport) -> list[str]:
    """Slack shows the default variant only. Comparison figures stay out of this text."""
    lines = [
        "*Period order return rate* (default)",
        f"_{report.note}_",
    ]
    for rate in report.rates:
        lines += _format_period_rate(rate)
    lines.append(
        f"• *Exchange share of submitted returns:* unavailable — {report.exchange_share_reason}"
    )
    lines.append("")
    return lines


def format_rates(report: RatesReport) -> list[str]:
    lines = [f"*Unit cohort rates* (not the period order return rate; {report.scope_label})"]
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


def _status_note(status: MetricStatus) -> str:
    return " — _provisional_" if status is MetricStatus.PROVISIONAL else ""


def format_reason_summary(summary: ReasonSummary) -> list[str]:
    if summary.status is MetricStatus.UNAVAILABLE or summary.coverage is None:
        return [f"• *Redo return reasons:* unavailable — {summary.reason}"]
    c = summary.coverage
    lines = [f"*Return reasons* (share of {c.units:,} {summary.population}){_status_note(summary.status)}"]
    for r in summary.reasons[:6]:
        share = f"{r.share_of_units_pct:.1f}%" if r.share_of_units_pct is not None else "n/a"
        line = f"• *{r.reason}:* {r.units:,} units ({share}), {r.returns:,} returns"
        if r.secondary:
            line += " — " + ", ".join(f"{name} {units:,}" for name, units in r.secondary[:2])
        lines.append(line)
    lines.append(
        f"_Coverage: {c.units_with_reason:,} of {c.units:,} units have a reason, {c.units_with_comment:,} "
        f"have a comment, {c.units_with_neither:,} have neither ({c.returns:,} returns)._"
    )
    if c.duplicate_items_dropped:
        lines.append(f"_{c.duplicate_items_dropped:,} duplicate item rows were dropped before counting._")
    return lines


def format_comment_synthesis(synthesis: CommentSynthesis) -> list[str]:
    if synthesis.status is MetricStatus.UNAVAILABLE:
        return [f"• *Customer return comments:* unavailable — {synthesis.reason}"]
    lines = [
        f"*What customers say in return comments* ({synthesis.comments_analysed:,} of "
        f"{synthesis.comments_in_scope:,} distinct comments analysed){_status_note(synthesis.status)}"
    ]
    for t in synthesis.themes[:5]:
        products = ", ".join(f"{name} ({n})" for name, n in t.products[:3])
        count = "1 return, emerging signal" if t.single_report else f"{t.returns:,} returns"
        lines.append(f"• *{t.label}* ({t.meaning.replace('_', ' ')}) — {count}; {products}")
        if t.quote:
            lines.append(f"   _\"{t.quote}\"_")
    lines += [f"_{c}_" for c in synthesis.caveats]
    return lines


def _ranked_line(r: RankedProduct) -> str:
    c = r.candidate
    season = f" ({c.season})" if c.season else ""
    trend = f", {r.trend_pp:+.1f} pp vs comparable prior" if r.trend_pp is not None else ", no comparable prior"
    return (
        f"• *{c.product}*{season}: {r.rate_pct:.1f}% item return rate, {r.delta_pp:+.1f} pp vs overall, "
        f"{r.excess_units:+,.0f} returned units vs the overall rate ({r.delivered_units:,} delivered){trend}"
        f"{_status_note(r.status)}"
    )


def format_monitoring_view(view: MonitoringView) -> list[str]:
    if view.status is MetricStatus.UNAVAILABLE:
        return [f"• *Products to watch:* unavailable — {view.reason}. _{view.label}_"]
    lines = ["*Products to watch*", f"_{view.label}_"]
    for r in view.needs_attention[:5]:
        lines.append(_ranked_line(r))
    if view.low_sample:
        lines.append("_Low sample, directional only:_")
        lines += [_ranked_line(r) for r in view.low_sample[:3]]
    if view.performing_well:
        lines.append("_Below the overall rate:_")
        lines += [_ranked_line(r) for r in view.performing_well[:3]]
    skipped = len(view.not_ranked) + len(view.out_of_focus) + len(view.excluded)
    if skipped:
        lines.append(
            f"_Not ranked: {len(view.not_ranked)} without an available rate, {len(view.out_of_focus)} "
            f"Prior or unknown season, {len(view.excluded)} excluded from recommendations._"
        )
    return lines


def format_customer_voice(data: dict) -> list[str]:
    reasons = data.get("reasons")
    comments = data.get("comments")
    monitoring = data.get("monitoring")
    if not any(isinstance(x, (ReasonSummary, CommentSynthesis, MonitoringView)) for x in (reasons, comments, monitoring)):
        return []
    lines = ["*Customer reasons and products to watch*"]
    if isinstance(reasons, ReasonSummary):
        lines += format_reason_summary(reasons)
    if isinstance(comments, CommentSynthesis):
        lines += format_comment_synthesis(comments)
    if isinstance(monitoring, MonitoringView):
        lines += format_monitoring_view(monitoring)
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
    period_rates = data.get("period_rates")

    if total_lines == 0 and not isinstance(period_rates, PeriodReport):
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

    if isinstance(period_rates, PeriodReport):
        lines += format_period_rates(period_rates)
        if product_filter:
            lines.append("_The period rate is all orders. The product filter applies to refund volume only._")
            lines.append("")

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

    lines += format_customer_voice(data)

    if top_notes:
        lines.append("*Internal refund notes* (Shopify `REFUND_NOTE`, generic notes excluded; not customer reasons)")
        for n in top_notes[:5]:
            lines.append(f"• [{n['count']}] _{n['note'][:100]}_")
        lines.append("")

    lines.append("*Recommended action*")
    if top_products:
        top_prod = top_products[0]["product"]
        lines.append(
            f"• *Product/Ops:* `{top_prod}` has the most refunded units. Volume follows sales. "
            "The period order return rate is not a product rate, and the unit cohort item return rate is not available."
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

    return "\n".join(lines)
