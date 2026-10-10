"""CSAT note text for the daily report.

``get_daily_report`` attaches ``note`` on each negative ticket.
``format_daily`` prints that string. The wording is the same text
``_generate_csat_note`` used to build inside the formatter.
"""

def generate_csat_note(ticket: dict) -> str:
    """
    Use LLM to generate a 3-part CSAT note for a negative ticket.
    Returns a formatted Slack block:
      :star::star:: <customer situation>
      Agent: <what agent did>
      Takeaway: <root cause / context>
      RPL: <url>
    Falls back to a plain summary if LLM is unavailable.
    """
    import json as _json
    try:
        from scout.llm import MODEL, get_client
    except ImportError:
        return csat_note_fallback(ticket)

    score_int = ticket.get("score_int", 1)
    stars = ":star:" * score_int
    csat_comment = ticket.get("csat_comment", "")
    subject = ticket.get("subject", "")
    agent_name = ticket.get("agent_name", "Agent")
    url = ticket.get("url", "")
    messages = ticket.get("messages", [])

    # Build thread context for the LLM
    thread_lines = []
    for m in messages:
        sender = "Customer" if m["sender"] == "customer" else "Agent"
        thread_lines.append(f"{sender}: {m['text']}")
    thread = "\n".join(thread_lines) if thread_lines else "(no messages available)"

    system_prompt = (
        "You are a customer service analyst for a premium menswear brand. "
        "You will receive a support conversation that received a negative CSAT rating. "
        "Write a concise 3-part note for the CS team's daily Slack report.\n\n"
        "Return ONLY valid JSON with exactly these three keys:\n"
        '{"customer": "One sentence: what the customer\'s issue was and why they were unhappy.", '
        '"agent": "One sentence: what the agent did and whether they followed protocol.", '
        '"takeaway": "One sentence: root cause or context — why the rating happened."}\n\n'
        "Rules:\n"
        "- Be factual and grounded in the conversation. Do not invent details.\n"
        "- Keep each sentence concise (under 40 words).\n"
        "- Do not use the customer's name."
    )

    user_prompt = (
        f"Subject: {subject}\n"
        f"CSAT rating: {score_int}/5 ({ticket.get('score_label', '')})\n"
        f"Customer comment: {csat_comment or '(none)'}\n"
        f"Agent: {agent_name}\n\n"
        f"Conversation:\n{thread}"
    )

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        if raw.startswith("json"):
            raw = raw[4:]
        parsed = _json.loads(raw.strip())
        customer_note = parsed.get("customer", "")
        agent_note = parsed.get("agent", "")
        takeaway = parsed.get("takeaway", "")
    except Exception:
        return csat_note_fallback(ticket)

    lines = [
        f"{stars}: {customer_note}",
        f"Agent: {agent_note}",
        f"Takeaway: {takeaway}",
    ]
    if url:
        lines.append(f"RPL: {url}")
    return "\n".join(lines)



def csat_note_fallback(ticket: dict) -> str:
    """Plain fallback when LLM is unavailable."""
    score_int = ticket.get("score_int", 1)
    stars = ":star:" * score_int
    subject = ticket.get("subject", "(no subject)")
    agent_name = ticket.get("agent_name", "Agent")
    url = ticket.get("url", "")
    csat_comment = ticket.get("csat_comment", "")
    comment_part = f" Customer comment: \"{csat_comment}\"" if csat_comment else ""
    lines = [
        f"{stars}: {subject}.{comment_part}",
        f"Agent: {agent_name} handled this conversation.",
        f"Takeaway: Review conversation for context.",
    ]
    if url:
        lines.append(f"RPL: {url}")
    return "\n".join(lines)

