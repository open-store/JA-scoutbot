"""Slack formatting for /daily."""

def format_daily(data: dict) -> str:
    """
    Format the /daily CS KPI report for Slack.
    Matches the exact format specified:

      :chart_with_upwards_trend: CS KPIs & Updates | M/D/YYYY


      Total tickets closed: N
      Avg. tickets closed per agent: N
      CSAT: X.X | Total surveys: N | Negatives: N
      Avg. FRT (last 24 hrs): N mins
      Avg. RT (last 24 hrs): N hr N mins


      Notes on CSATs:


      No negatives! :starmario:
      (or per-negative LLM blocks)


      :mag: FRT = First Response Time
      :repeat: RT = Resolution Time
    """
    date_label = data.get("date_label", "yesterday")
    totals = data.get("totals", {})
    active_agents = data.get("active_agents", 0)
    negatives_count = data.get("negatives_count", 0)
    negative_tickets = data.get("negative_tickets", [])

    closed = totals.get("closed", 0)
    frt_display = totals.get("frt_avg_display", "—")
    rt_display = totals.get("rt_avg_display", "—")
    csat_score = totals.get("csat_score")
    surveys_sent = totals.get("surveys_sent", 0)
    surveys_rated = totals.get("surveys_rated", 0)

    if closed == 0:
        return (
            f":chart_with_upwards_trend: CS KPIs & Updates | {date_label}\n\n"
            "No ticket data found for this date."
        )

    # ── CSAT score display ────────────────────────────────────────────────────
    # Show as avg score (1-5). When exactly 5.0 → "5:star:". Otherwise just the number.
    if csat_score is not None:
        score_f = float(csat_score)
        if score_f == 5.0:
            csat_str = "5:star:"
        else:
            csat_str = f"{score_f:.1f}"
    else:
        csat_str = "—"

    # Negatives display: "0!" when zero, otherwise just the number
    neg_display = f"{negatives_count}!" if negatives_count == 0 else str(negatives_count)

    # Avg tickets per agent
    avg_per_agent = round(closed / active_agents) if active_agents > 0 else closed

    lines = [
        f"*:chart_with_upwards_trend: CS KPIs & Updates | {date_label}*",
        "",
        "",
        f"• Total tickets closed: {closed}",
        f"• Avg. tickets closed per agent: {avg_per_agent}",
        f"• CSAT: {csat_str} | Total surveys: {surveys_rated} | Negatives: {neg_display}",
        f"• Avg. FRT (last 24 hrs): {frt_display}",
        f"• Avg. RT (last 24 hrs): {rt_display}",
        "",
        "",
        "*Notes on CSATs:*",
        "",
        "",
    ]

    # ── CSAT notes section ────────────────────────────────────────────────────
    if negatives_count == 0:
        lines.append("No negatives! :star:")
    else:
        for i, ticket in enumerate(negative_tickets):
            if "note" in ticket:
                note = ticket["note"]
            else:
                from scout.services.daily_notes import generate_csat_note
                note = generate_csat_note(ticket)
            lines.append(note)
            if i < len(negative_tickets) - 1:
                lines.append("")  # blank line between multiple negatives

        # If we couldn't fetch the actual tickets but count > 0
        if not negative_tickets:
            plural = "negative" if negatives_count == 1 else "negatives"
            lines.append(
                f"{negatives_count} {plural} received. Run `/csat L7` for details."
            )

    lines += [
        "",
        "",
        ":mag: FRT = First Response Time",
        ":repeat: RT = Resolution Time",
    ]

    return "\n".join(lines)

