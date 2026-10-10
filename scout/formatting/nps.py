"""Slack formatting."""

from scout.formatting._common import _bar, _delta_str, _no_data_message, _nps_emoji

def format_nps_report(data: dict) -> str:
    """
    Format the full NPS report for Slack.
    Shows each survey separately (never aggregated).
    """
    days = data.get("days", 30)
    scores = data.get("scores", {})
    trend = data.get("trend", {})
    detractor_themes = data.get("detractor_themes", [])
    detractor_comment_count = data.get("detractor_comment_count", 0)

    if not scores:
        return _no_data_message("NPS", f"last {days} days", "Snowflake (KnoCommerce)")

    lines = [f"*:bar_chart: NPS — Last {days} Days*", ""]

    # Survey-by-survey scores
    lines.append("*Scores by survey*")
    for survey_name, s in scores.items():
        nps = s.get("nps")
        total = s.get("total", 0)
        if nps is None or total == 0:
            lines.append(f"• *{survey_name}:* No data")
            continue
        emoji = _nps_emoji(nps)
        t = trend.get(survey_name, {})
        delta = t.get("delta")
        delta_label = f"  {_delta_str(delta)} vs prior {days}d" if delta is not None else ""
        lines.append(
            f"{emoji} *{survey_name}:*  `{nps:+.1f}`  "
            f"P:{s['promoter_pct']}%  Pa:{s['passive_pct']}%  D:{s['detractor_pct']}%  "
            f"(n={total:,}){delta_label}"
        )
    lines.append("")

    # Trend summary
    has_trend = any(t.get("delta") is not None for t in trend.values())
    if has_trend:
        lines.append("*vs. prior period*")
        for survey_name, t in trend.items():
            delta = t.get("delta")
            if delta is not None:
                direction = "▲" if delta > 0 else ("▼" if delta < 0 else "→")
                lines.append(f"• *{survey_name}:* {direction} {abs(delta):.1f} pts  (prior: {t['prior_nps']:+.1f})")
        lines.append("")

    # Detractor themes (NPS Survey only, when n >= 10)
    if detractor_themes:
        lines.append(f"*Top detractor themes — NPS Survey* _(from {detractor_comment_count} open-text responses)_")
        for i, theme in enumerate(detractor_themes, 1):
            bar = _bar(theme["pct"], width=15)
            lines.append(f"{i}. *{theme['theme']}*  {bar}  {theme['pct']}%")
            if theme.get("verbatims"):
                for v in theme["verbatims"][:2]:
                    lines.append(f"   _\"{v}\"_")
        lines.append("")
    elif detractor_comment_count > 0:
        lines.append(f"_({detractor_comment_count} detractor comments — not enough for theme analysis in this window)_")
        lines.append("")

    lines.append(f"*Source:* Snowflake · `ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_`")
    return "\n".join(lines)

