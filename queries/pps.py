"""
PPS (Post-Purchase Survey) query module.

Handles two surveys:
  - Returning Customer PPS: why they came back, what almost stopped them,
    what they wish we sold, open-text feedback.
  - PPS - New Customers (attribution): how they heard about us, what brought
    them to the site, consideration window, who they bought for.

Schema: ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from snowflake_client import get_connection

DB = "ANALYTICS"
SCHEMA = "KNOCOMMERCE__NPS___SURVEYS_"

SURVEY_IDS = {
    "Returning Customer PPS": "81361b00-a8c6-4957-a632-838e133dace1",
    "PPS - New Customers": "1dd68a35-2c53-4822-9068-04dd7678d990",
}


def fqn(table):
    return f'"{DB}"."{SCHEMA}"."{table}"'


def _date_range(days: int):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _parse_value(raw) -> list:
    """
    Parse a VARIANT VALUE from RESPONSE_ANSWER into a list of strings.
    Values can be:
      - JSON array: '["Sale / promotion","Restock"]'  → ["Sale / promotion", "Restock"]
      - JSON string: '"Facebook"'                      → ["Facebook"]
      - Plain string: 'Facebook'                       → ["Facebook"]
    """
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if v]
        return [str(parsed).strip()]
    except (json.JSONDecodeError, ValueError):
        return [s.strip('"').strip()]


def _count_values(rows) -> dict:
    """Aggregate a list of (value,) rows into a frequency dict."""
    counts = {}
    for (raw,) in rows:
        for val in _parse_value(raw):
            if val:
                counts[val] = counts.get(val, 0) + 1
    return counts


def _top_n(counts: dict, n: int = 8) -> list:
    """Return top-N items as list of {label, count, pct} sorted by count."""
    total = sum(counts.values())
    if total == 0:
        return []
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]
    return [
        {"label": label, "count": count, "pct": round(count / total * 100, 1)}
        for label, count in sorted_items
    ]


# ── RETURNING CUSTOMER PPS ──────────────────────────────────────────

def get_returning_pps(days: int = 30) -> dict:
    """
    Returning Customer PPS report for the last N days.

    Returns:
      - response_count: total responses
      - nps: NPS score (from "recommend" question)
      - nps_breakdown: promoters/passives/detractors
      - return_reasons: why they came back (checkbox)
      - almost_stopped: % who said something almost stopped them
      - almost_stopped_reasons: open-text themes
      - open_text_themes: themes from "what else would you like to see" + other open text
      - top_verbatims: representative quotes
    """
    start_date, end_date = _date_range(days)
    survey_id = SURVEY_IDS["Returning Customer PPS"]
    conn = get_connection()
    cur = conn.cursor()

    # Response count
    cur.execute(f"""
        SELECT COUNT(*) FROM {fqn('RESPONSE')}
        WHERE SURVEY_ID = '{survey_id}'
          AND CREATED_AT >= '{start_date}'
          AND CREATED_AT < '{end_date}'
          AND _FIVETRAN_DELETED = FALSE
    """)
    response_count = cur.fetchone()[0]

    # NPS score
    cur.execute(f"""
        SELECT
            COUNT(DISTINCT ra.RESPONSE_ID) as total,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) >= 9 THEN 1 ELSE 0 END) as promoters,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) BETWEEN 7 AND 8 THEN 1 ELSE 0 END) as passives,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) <= 6 THEN 1 ELSE 0 END) as detractors
        FROM {fqn('RESPONSE_ANSWER')} ra
        JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
        WHERE r.SURVEY_ID = '{survey_id}'
          AND ra.TYPE = 'NPS'
          AND ra.LABEL ILIKE '%recommend%'
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND ra."VALUE" IS NOT NULL
          AND ra._FIVETRAN_DELETED = FALSE
    """)
    row = cur.fetchone()
    total, promoters, passives, detractors = row
    nps = round(((promoters - detractors) / total) * 100, 1) if total > 0 else None
    nps_breakdown = {
        "total": total, "nps": nps,
        "promoters": promoters, "passives": passives, "detractors": detractors,
        "promoter_pct": round(promoters / total * 100, 1) if total > 0 else 0,
        "passive_pct": round(passives / total * 100, 1) if total > 0 else 0,
        "detractor_pct": round(detractors / total * 100, 1) if total > 0 else 0,
    }

    # Why did they come back? (checkbox)
    cur.execute(f"""
        SELECT ra."VALUE"
        FROM {fqn('RESPONSE_ANSWER')} ra
        JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
        WHERE r.SURVEY_ID = '{survey_id}'
          AND ra.LABEL ILIKE '%come back%'
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND ra."VALUE" IS NOT NULL
          AND ra._FIVETRAN_DELETED = FALSE
    """)
    return_reasons_raw = cur.fetchall()
    return_reasons = _top_n(_count_values(return_reasons_raw))

    # Did anything almost stop them? (Yes/No)
    cur.execute(f"""
        SELECT TRIM(CAST(ra."VALUE" AS VARCHAR), '"') as val, COUNT(*) as cnt
        FROM {fqn('RESPONSE_ANSWER')} ra
        JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
        WHERE r.SURVEY_ID = '{survey_id}'
          AND ra.LABEL ILIKE '%stop you%'
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND ra."VALUE" IS NOT NULL
          AND ra._FIVETRAN_DELETED = FALSE
        GROUP BY val
    """)
    stop_rows = cur.fetchall()
    stop_counts = {r[0]: r[1] for r in stop_rows}
    yes_count = stop_counts.get("Yes", 0)
    no_count = stop_counts.get("No", 0)
    stop_total = yes_count + no_count
    almost_stopped_pct = round(yes_count / stop_total * 100, 1) if stop_total > 0 else 0

    # Open-text: what almost stopped them + what else would you like to see
    cur.execute(f"""
        SELECT TRIM(CAST(ra."VALUE" AS VARCHAR), '"') as comment
        FROM {fqn('RESPONSE_ANSWER')} ra
        JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
        WHERE r.SURVEY_ID = '{survey_id}'
          AND ra.TYPE IN ('Text', 'TextArea')
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND ra."VALUE" IS NOT NULL
          AND LENGTH(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) > 5
          AND ra._FIVETRAN_DELETED = FALSE
        ORDER BY r.CREATED_AT DESC
        LIMIT 100
    """)
    open_text_rows = cur.fetchall()
    open_text_comments = [r[0] for r in open_text_rows if r[0] and r[0].strip()]

    cur.close()
    conn.close()

    # LLM theme extraction for open text
    open_text_themes = _extract_pps_themes(open_text_comments, survey_type="returning")

    return {
        "days": days,
        "response_count": response_count,
        "nps_breakdown": nps_breakdown,
        "return_reasons": return_reasons,
        "almost_stopped_pct": almost_stopped_pct,
        "open_text_themes": open_text_themes,
        "open_text_count": len(open_text_comments),
    }


# ── NEW CUSTOMER PPS (ATTRIBUTION) ─────────────────────────────────

def get_attribution(days: int = 30) -> dict:
    """
    New Customer PPS attribution report for the last N days.

    Returns:
      - response_count
      - how_heard: channel breakdown (how did you first hear about us)
      - what_brought: what brought them to the site today
      - consideration_window: time from awareness to first purchase
      - who_for: who they bought for
      - open_text_themes: themes from open-text answers
    """
    start_date, end_date = _date_range(days)
    survey_id = SURVEY_IDS["PPS - New Customers"]
    conn = get_connection()
    cur = conn.cursor()

    # Response count
    cur.execute(f"""
        SELECT COUNT(*) FROM {fqn('RESPONSE')}
        WHERE SURVEY_ID = '{survey_id}'
          AND CREATED_AT >= '{start_date}'
          AND CREATED_AT < '{end_date}'
          AND _FIVETRAN_DELETED = FALSE
    """)
    response_count = cur.fetchone()[0]

    def fetch_distribution(label_pattern: str) -> list:
        cur.execute(f"""
            SELECT ra."VALUE"
            FROM {fqn('RESPONSE_ANSWER')} ra
            JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
            WHERE r.SURVEY_ID = '{survey_id}'
              AND ra.LABEL ILIKE '{label_pattern}'
              AND r.CREATED_AT >= '{start_date}'
              AND r.CREATED_AT < '{end_date}'
              AND ra."VALUE" IS NOT NULL
              AND ra._FIVETRAN_DELETED = FALSE
        """)
        return _top_n(_count_values(cur.fetchall()))

    how_heard = fetch_distribution("%hear about%")
    what_brought = fetch_distribution("%brought you%")
    consideration_window = fetch_distribution("%long did you know%")
    who_for = fetch_distribution("%purchase this for%")

    # Open-text answers
    cur.execute(f"""
        SELECT TRIM(CAST(ra."VALUE" AS VARCHAR), '"') as comment
        FROM {fqn('RESPONSE_ANSWER')} ra
        JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
        WHERE r.SURVEY_ID = '{survey_id}'
          AND ra.TYPE IN ('Text', 'TextArea')
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND ra."VALUE" IS NOT NULL
          AND LENGTH(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) > 5
          AND ra._FIVETRAN_DELETED = FALSE
        ORDER BY r.CREATED_AT DESC
        LIMIT 80
    """)
    open_text_rows = cur.fetchall()
    open_text_comments = [r[0] for r in open_text_rows if r[0] and r[0].strip()]

    cur.close()
    conn.close()

    open_text_themes = _extract_pps_themes(open_text_comments, survey_type="new")

    return {
        "days": days,
        "response_count": response_count,
        "how_heard": how_heard,
        "what_brought": what_brought,
        "consideration_window": consideration_window,
        "who_for": who_for,
        "open_text_themes": open_text_themes,
        "open_text_count": len(open_text_comments),
    }


def _extract_pps_themes(comments: list, survey_type: str = "returning") -> list:
    """
    Use GPT to extract themes from PPS open-text responses.
    Returns list of {theme, count, pct} dicts.
    Only called when n >= 5 comments.
    """
    if len(comments) < 5:
        return []

    from openai import OpenAI
    client = OpenAI()

    if survey_type == "returning":
        instruction = (
            "You are analyzing open-text responses from returning customers of Jack Archer "
            "(a men's apparel brand). These are answers to questions like 'what almost stopped you', "
            "'what else would you like to see', and 'what was the main reason behind your rating'. "
            "Identify the top recurring themes. Return a JSON object with theme names as keys and "
            "integer counts as values (max 6 themes). Focus on actionable product/service themes. "
            "Also include a 'top_verbatims' key with 2 representative quotes (max 100 chars each)."
        )
    else:
        instruction = (
            "You are analyzing open-text responses from new customers of Jack Archer "
            "(a men's apparel brand). These are answers to questions about their experience, "
            "what could be improved, and what keeps them coming back. "
            "Identify the top recurring themes. Return a JSON object with theme names as keys and "
            "integer counts as values (max 6 themes). Focus on actionable themes. "
            "Also include a 'top_verbatims' key with 2 representative quotes (max 100 chars each)."
        )

    comments_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(comments[:60]))

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": f"Comments:\n{comments_text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception:
        return []

    top_verbatims = data.pop("top_verbatims", [])
    total = len(comments)

    themes = []
    for theme_name, count in data.items():
        if isinstance(count, int) and count > 0:
            themes.append({
                "theme": theme_name,
                "count": count,
                "pct": round(count / total * 100, 1),
                "verbatims": top_verbatims if not themes else [],
            })

    themes.sort(key=lambda x: x["count"], reverse=True)
    return themes[:5]
