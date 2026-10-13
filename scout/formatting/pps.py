"""Slack formatting."""

from scout.formatting._common import _bar, _no_data_message, _nps_emoji

def format_pps_returning(data: dict) -> str:
    """Format Returning Customer PPS report for Slack."""
    days = data.get("days", 30)
    response_count = data.get("response_count", 0)
    nps_bd = data.get("nps_breakdown", {})
    return_reasons = data.get("return_reasons", [])
    almost_stopped_pct = data.get("almost_stopped_pct", 0)
    open_text_themes = data.get("open_text_themes", [])
    open_text_count = data.get("open_text_count", 0)

    if response_count == 0:
        return _no_data_message("Returning Customer PPS", f"last {days} days", "Snowflake (KnoCommerce)")

    lines = [f"*:repeat: Returning Customer PPS — Last {days} Days*  (n={response_count:,} responses)", ""]

    # NPS
    nps = nps_bd.get("nps")
    if nps is not None:
        emoji = _nps_emoji(nps)
        lines.append(
            f"{emoji} *NPS:* `{nps:+.1f}`  "
            f"P:{nps_bd['promoter_pct']}%  Pa:{nps_bd['passive_pct']}%  D:{nps_bd['detractor_pct']}%  "
            f"(n={nps_bd['total']:,})"
        )
        lines.append("")

    # Why did they come back?
    if return_reasons:
        lines.append("*Why did they come back?*")
        for r in return_reasons[:6]:
            bar = _bar(r["pct"], width=15)
            lines.append(f"• *{r['label']}*  {bar}  {r['pct']}%  ({r['count']:,})")
        lines.append("")

    # Almost stopped?
    lines.append("*Did anything almost stop them from purchasing?*")
    lines.append(f"• Yes: *{almost_stopped_pct}%*  │  No: *{100 - almost_stopped_pct:.1f}%*")
    lines.append("")

    # Open-text themes
    if open_text_themes:
        lines.append(f"*Open-text themes* _(from {open_text_count} responses)_")
        for i, theme in enumerate(open_text_themes, 1):
            bar = _bar(theme["pct"], width=15)
            lines.append(f"{i}. *{theme['theme']}*  {bar}  {theme['pct']}%")
            if theme.get("verbatims"):
                for v in theme["verbatims"][:1]:
                    lines.append(f"   _\"{v}\"_")
        lines.append("")

    lines.append(f"*Source:* Snowflake · `ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_`")
    return "\n".join(lines)


def format_pps_new(data: dict) -> str:
    """Format New Customer PPS report for Slack."""
    days = data.get("days", 30)
    response_count = data.get("response_count", 0)
    nps_bd = data.get("nps_breakdown", {})
    open_text_themes = data.get("open_text_themes", [])
    open_text_count = data.get("open_text_count", 0)

    if response_count == 0:
        return _no_data_message("New Customer PPS", f"last {days} days", "Snowflake (KnoCommerce)")

    lines = [f"*:sparkles: New Customer PPS — Last {days} Days*  (n={response_count:,} responses)", ""]

    nps = nps_bd.get("nps")
    if nps is not None:
        emoji = _nps_emoji(nps)
        lines.append(
            f"{emoji} *NPS:* `{nps:+.1f}`  "
            f"P:{nps_bd['promoter_pct']}%  Pa:{nps_bd['passive_pct']}%  D:{nps_bd['detractor_pct']}%  "
            f"(n={nps_bd['total']:,})"
        )
        lines.append("")

    if open_text_themes:
        lines.append(f"*Open-text themes* _(from {open_text_count} responses)_")
        for i, theme in enumerate(open_text_themes, 1):
            bar = _bar(theme["pct"], width=15)
            lines.append(f"{i}. *{theme['theme']}*  {bar}  {theme['pct']}%")
            if theme.get("verbatims"):
                for v in theme["verbatims"][:1]:
                    lines.append(f"   _\"{v}\"_")
        lines.append("")

    lines.append(f"*Source:* Snowflake · `ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_`")
    return "\n".join(lines)


def format_attribution(data: dict) -> str:
    """Format New Customer PPS attribution report for Slack."""
    days = data.get("days", 30)
    response_count = data.get("response_count", 0)
    how_heard = data.get("how_heard", [])
    what_brought = data.get("what_brought", [])
    consideration_window = data.get("consideration_window", [])
    who_for = data.get("who_for", [])
    other_breakdown = data.get("other_breakdown", [])
    other_writeins_count = data.get("other_writeins_count", 0)
    open_text_themes = data.get("open_text_themes", [])
    open_text_count = data.get("open_text_count", 0)

    if response_count == 0:
        return _no_data_message("Attribution", f"last {days} days", "Snowflake (KnoCommerce)")

    lines = [f"*:mag: Attribution — Last {days} Days*  (n={response_count:,} new customer responses)", ""]

    def _section(title: str, items: list, max_items: int = None):
        if not items:
            return
        lines.append(f"*{title}*")
        display = items if max_items is None else items[:max_items]
        for r in display:
            bar = _bar(r["pct"], width=15)
            lines.append(f"• *{r['label']}*  {bar}  {r['pct']}%  ({r['count']:,})")
        lines.append("")

    # How heard: full normalised distribution (no cap)
    _section("How did they first hear about us?", how_heard)

    # Other breakdown: LLM-clustered write-ins
    if other_breakdown and other_writeins_count >= 5:
        lines.append(f"*↳ 'Other' breakdown* _(from {other_writeins_count} write-in responses)_")
        for r in other_breakdown:
            bar = _bar(r["pct"], width=12)
            lines.append(f"  • *{r['label']}*  {bar}  {r['pct']}%  ({r['count']:,})")
        lines.append("")

    _section("What brought them to the site?", what_brought, max_items=8)
    _section("Consideration window (awareness → first purchase)", consideration_window)
    _section("Who did they buy for?", who_for)

    if open_text_themes:
        lines.append(f"*Open-text themes* _(from {open_text_count} responses)_")
        for i, theme in enumerate(open_text_themes, 1):
            bar = _bar(theme["pct"], width=15)
            lines.append(f"{i}. *{theme['theme']}*  {bar}  {theme['pct']}%")
            if theme.get("verbatims"):
                for v in theme["verbatims"][:1]:
                    lines.append(f"   _\"{v}\"_")
        lines.append("")

    lines.append(f"*Source:* Snowflake · `ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_`")
    return "\n".join(lines)

