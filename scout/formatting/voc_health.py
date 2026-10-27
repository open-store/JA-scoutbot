"""Slack formatting for the customer-health report in general `/voc`."""

from typing import Dict, List, Optional

from scout.health.themes import SOURCE_SHORT, UNIT_LABELS


def _n(count: Optional[int], word: str) -> str:
    count = count or 0
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def _value(entry: dict) -> str:
    v = entry.get("value")
    unit = entry.get("unit")
    if v is None:
        return ""
    if unit == "NPS":
        return f"{v:+.1f}"
    if unit == "stars":
        return f"{v:.2f}★"
    if unit == "%":
        return f"{v:.1f}%"
    return f"{v}"


def _prior(entry: dict) -> str:
    c = entry.get("comparison") or {}
    if c.get("prior_value") is None:
        return ""
    prior = dict(entry, value=c["prior_value"])
    n = f" ({c['prior_sample']:,})" if c.get("prior_sample") is not None else ""
    return f"prev {_value(prior)}{n}"


def _freshness(entry: dict) -> str:
    f = entry.get("freshness") or {}
    status = f.get("status")
    if status == "stale":
        return f":warning: stale, {f.get('note', '')}"
    if status == "fresh":
        return f.get("note", "")
    if status == "unknown" and entry.get("key") not in ("returns",):
        return "freshness not checked"
    return ""


def _join(parts: List[str]) -> str:
    return " · ".join(p for p in parts if p)


def _entry_line(entry: dict) -> List[str]:
    label = entry["label"]
    key = entry["key"]
    details = entry.get("details") or {}
    if entry["status"] == "unavailable":
        reasons = entry.get("reasons") or ["no data"]
        more = f" (+{len(reasons) - 1} more)" if len(reasons) > 1 else ""
        lines = [f"• *{label}:* unavailable — {reasons[0]}{more}"]
        if key == "returns":
            lines.append(
                "   _Unit cohort exchange rate and exchange share: unavailable. "
                "Not the period order return rate. Not scored as zero._"
            )
        return lines

    if key == "support_csat":
        rate = details.get("response_rate_pct")
        parts = [_value(entry), f"{entry['sample']:,} rated of {_n(entry.get('eligible'), 'ticket')}"
                 + (f" ({rate}%)" if rate is not None else ""), _prior(entry), _freshness(entry)]
    elif key == "nps_survey":
        mix = (f"{details.get('promoter_pct')}% promoters / {details.get('passive_pct')}% passives / "
               f"{details.get('detractor_pct')}% detractors")
        parts = [_value(entry), _n(entry.get("sample"), "response"), mix, _prior(entry), _freshness(entry)]
    elif key == "reviews":
        parts = [_value(entry) + " average", _n(entry.get("sample"), "review"), _prior(entry), entry.get("window", "")]
    elif key == "returns":
        rates = details.get("rates") or []
        line = _join([_value(entry), f"of {entry.get('sample') or 0:,} eligible delivered units"])
        if entry["status"] == "provisional":
            line += f" — _provisional: {(entry.get('reasons') or [''])[0]}_"
        others = [f"{r['label']} {r['value_pct']:.1f}%" for r in rates
                  if r["metric_id"] != "item_return_rate" and r.get("value_pct") is not None]
        lines = [f"• *{label}:* {line}"]
        if others:
            lines.append(f"   _{'; '.join(others)} (exchange metrics shown, not scored)_")
        return lines
    elif key in ("returning_pps", "new_pps"):
        parts = [f"recommend NPS {_value(entry)}" if entry.get("value") is not None else "",
                 _n(entry.get("eligible"), "response")]
        reason = details.get("top_return_reason")
        if reason:
            parts.append(f"top reason to come back: {reason['label']} ({reason['pct']}%)")
        if key == "returning_pps" and details.get("almost_stopped_pct") is not None:
            parts.append(f"{details['almost_stopped_pct']}% say something almost stopped them")
        parts.append("_not scored_")
    elif key == "attribution":
        top = details.get("top_channels") or []
        channels = ", ".join(f"{c['label']} {c['pct']}%" for c in top)
        parts = [channels, f"of {_n(entry.get('sample'), 'respondent')}", "_not scored_"]
    else:
        parts = [_value(entry)]

    if entry["status"] == "stale" and not any("stale" in p for p in parts):
        parts.append(":warning: stale")
    lines = [f"• *{label}:* {_join(parts)}"]
    for reason in entry.get("reasons") or []:
        lines.append(f"   _{reason}_")
    return lines


def _score_lines(score: dict) -> List[str]:
    version = score.get("version", "")
    if score.get("value") is None:
        why = (score.get("notes") or [""])[0]
        return [f"*Health score:* unavailable — {why} The scorecard below is the data."]
    excluded = [c for c in score.get("components", []) if not c.get("included")]
    line = f"*Health score:* {score['value']:.1f} / 100 _(provisional, {version})_"
    if score.get("partial"):
        missing = ", ".join(f"{c['label']} ({c['weight']:.0%})" for c in excluded)
        line += (f" — partial: covers {score['coverage']:.0%} of the weight; {missing} excluded, "
                 "not scored as zero.")
        rng = score.get("range_if_complete")
        if rng:
            line += f" With every component, the score would fall between {rng[0]:.1f} and {rng[1]:.1f}."
    if score.get("delta") is not None:
        line += f" Previous window: {score['prior_value']:.1f} ({score['delta']:+.1f})."
    return [line]


def _theme_lines(index: int, theme: dict, samples: Dict[str, dict]) -> List[str]:
    counts = []
    for source, n in theme["counts"].items():
        analysed = (samples.get(source) or {}).get("analysed")
        share = theme["shares"].get(source)
        counts.append(f"{SOURCE_SHORT.get(source, source)} {n} of {analysed} {UNIT_LABELS.get(source, '')}"
                      + (f" ({share:.0f}%)" if share is not None else ""))
    sources = theme["source_count"]
    head = f"{index}. *{theme['label']}* — {'; '.join(counts)}"
    if sources > 1:
        head += f" · in {sources} sources"
    elif sum(theme["counts"].values()) == 1:
        head += " · single report"
    if theme.get("products"):
        head += f" · {', '.join(theme['products'])}"
    lines = [head]
    body = theme.get("summary", "")
    if theme.get("quote"):
        body += f" \"_{theme['quote']}_\""
    if body:
        lines.append(f"   {body.strip()}")
    if theme.get("action"):
        lines.append(f"   → {theme['action']}")
    return lines


def _support_lines(data: dict) -> List[str]:
    volume = data.get("volume") or {}
    prev = data.get("prev_volume") or {}
    total = int(volume.get("TOTAL_CONVERSATIONS") or 0)
    prev_total = int(prev.get("TOTAL_CONVERSATIONS") or 0)
    days = data.get("days", 7)
    filter_label = data.get("filter_label", "")
    lines = [f"*Support volume* (Richpanel tickets{', ' + filter_label if filter_label else ''})"]
    change = ""
    if prev_total > 0:
        pct = round((total - prev_total) / prev_total * 100, 1)
        change = f" ({'+' if pct > 0 else ''}{pct}% vs. previous {days} days)"
    lines.append(f"• *Total conversations:* {total:,}{change}")
    channels = data.get("by_channel") or []
    if channels and total:
        mix = ", ".join(f"{c.get('CHANNEL')} {round(c.get('CNT', 0) / total * 100)}%" for c in channels[:4])
        lines.append(f"• *Channels:* {mix}")
    tags = data.get("tags") or []
    if tags and total:
        top = ", ".join(f"{t.get('TAG_NAME')} {t.get('CNT', 0):,}" for t in tags[:5])
        lines.append(f"• *Top Richpanel tags:* {top} _(tags count contact reasons; the themes above say what they are about)_")
    lines.append("")
    return lines


def _breakdown_lines(score: dict, config: dict) -> List[str]:
    lines = [f"*Score breakdown* — {config.get('version')}, proposed defaults"]
    for c in score.get("components", []):
        weight = f"{c['weight']:.0%}"
        if c.get("included"):
            raw = c["raw"]
            raw_text = {"nps_survey": f"{raw:+.1f}", "reviews": f"{raw:.2f}★"}.get(c["key"], f"{raw:.1f}%")
            lines.append(f"• *{c['label']}* {weight} · {raw_text} → {c['normalized']:.1f} · {c['normalization']}")
        else:
            lines.append(f"• *{c['label']}* {weight} · excluded: {c['reason']}")
    lines.append(
        f"_The score needs {config.get('min_components')} components and {config.get('min_weight_coverage', 0):.0%} "
        f"of the weight. Unavailable, stale (over {config.get('stale_after_hours')}h), provisional, or "
        f"below-minimum components are excluded, never zeroed. {config.get('reference_note', '')}_"
    )
    lines.append("")
    return lines


def _sample_lines(samples: List[dict]) -> List[str]:
    lines = ["*Evidence samples*"]
    for s in samples:
        eligible = f" from {s['eligible']:,} {s['eligible_label']}" if s.get("eligible") is not None else ""
        cap = f" (cap {s['retrieval_cap']})" if s.get("retrieval_cap") else ""
        lines.append(f"• *{s['label']}:* {s['retrieved']} retrieved{cap}{eligible}, {s['analysed']} analysed")
    lines.append("")
    return lines


def format_health_voc(data: dict) -> str:
    health = data["health"]
    days = data.get("days", health.get("days", 7))
    filter_label = data.get("filter_label", "")
    score = health.get("score") or {}
    config = health.get("score_config") or {}
    synthesis = health.get("themes") or {}
    samples = {s["source"]: s for s in synthesis.get("samples", [])}

    title = f"*:stethoscope: Customer health — last {days} complete days ({data.get('timeframe_label', '')})"
    title += f", tickets filtered by {filter_label}*" if filter_label else "*"
    lines = [title]
    lines.extend(_score_lines(score))
    lines.append("")

    lines.append("*Scorecard*")
    for entry in health.get("scorecard", []):
        lines.extend(_entry_line(entry))
    lines.append(
        "_Each source has its own window and population: tickets use complete ET days, surveys UTC dates, "
        "reviews the last N days from now, and returns mature delivery cohorts. The three NPS-type surveys "
        "are never averaged._"
    )
    if health.get("filters"):
        lines.append("_Survey, review, and returns data cover all customers; the /voc filters apply to tickets only._")
    lines.append("")

    themes = synthesis.get("themes") or []
    lines.append("*What customers are saying*")
    if synthesis.get("status") == "ok" and themes:
        friction = [t for t in themes if t["kind"] == "friction"]
        positive = [t for t in themes if t["kind"] == "positive"]
        if friction:
            lines.append("_Friction_")
            for i, t in enumerate(friction, 1):
                lines.extend(_theme_lines(i, t, samples))
        if positive:
            lines.append("_What's working_")
            for i, t in enumerate(positive, 1):
                lines.extend(_theme_lines(i, t, samples))
        analysed = sum(s.get("analysed", 0) for s in samples.values())
        dropped = sum((synthesis.get("dropped") or {}).values())
        lines.append(
            f"_{analysed} items analysed. Labels, summaries, and actions are model-written from cited items; "
            "counts and shares are computed from the citations within each source and are not added across sources."
            + (f" {dropped} model theme(s) dropped for a generic label or no valid citations." if dropped else "")
            + "_"
        )
    elif synthesis.get("status") == "no_evidence":
        lines.append("_No customer text was available from any source in this window._")
    else:
        lines.append(f"_Theme synthesis unavailable ({synthesis.get('error') or 'no themes returned'}). "
                     "The scorecard and support volume are unaffected._")
    lines.append("")

    lines.extend(_support_lines(data))
    lines.extend(_breakdown_lines(score, config))
    lines.extend(_sample_lines(synthesis.get("samples", [])))

    lines.append("*So what*")
    friction = [t for t in themes if t["kind"] == "friction"]
    if synthesis.get("so_what"):
        lines.append(f"• {synthesis['so_what']}")
    elif friction:
        lines.append(f"• The leading friction theme is *{friction[0]['label']}*.")
    else:
        lines.append("• No cited customer theme is available for this window; see the scorecard.")
    lines.append("")
    lines.append("*Recommended action*")
    if friction and friction[0].get("action"):
        lines.append(f"• {friction[0]['action']}")
    else:
        lines.append("• Review the scorecard components marked unavailable or stale before acting on the score.")
    return "\n".join(lines)
