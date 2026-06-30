"""
NPS query module — fetches NPS scores per survey from Snowflake
and classifies open-text responses against the validated theme taxonomy.

Schema: ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_
Tables: SURVEY, SURVEY_QUESTION, RESPONSE, RESPONSE_ANSWER

Key notes:
- COMPLETED_AT is NULL for many recent responses; use CREATED_AT for time filtering.
- VALUE column is VARIANT; scalar answers are JSON-encoded strings e.g. "10".
- NPS questions are TYPE='NPS' with LABEL containing 'recommend'.
- Open-text answers are TYPE IN ('Text', 'TextArea').
- Surveys are kept separate — never aggregate NPS across surveys.
"""

import os
import sys
import json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from snowflake_client import get_connection

DB = "ANALYTICS"
SCHEMA = "KNOCOMMERCE__NPS___SURVEYS_"

# Target survey IDs (live surveys only)
SURVEY_IDS = {
    "NPS Survey": "61e08ea9-7cb0-4e81-aea5-39c0cf0d7e84",
    "NPS Survey Copy": "e69d0c24-ad9c-449b-bf31-4f10867eb06d",
    "Returning Customer PPS": "81361b00-a8c6-4957-a632-838e133dace1",
    "PPS - New Customers": "1dd68a35-2c53-4822-9068-04dd7678d990",
}

# NPS-bearing surveys (have a "recommend" NPS question)
NPS_SURVEYS = ["NPS Survey", "NPS Survey Copy", "Returning Customer PPS", "PPS - New Customers"]

# Display names for output — NPS Survey + Copy are merged under one name
DISPLAY_NAMES = {
    "NPS Survey": "NPS Survey",
    "NPS Survey Copy": "NPS Survey",
    "Returning Customer PPS": "Returning Customer PPS",
    "PPS - New Customers": "New Customer PPS",
}

# Validated theme taxonomy from YTD NPS analysis (Mar-Jun 2026)
COMPLAINT_THEMES = [
    "Returns / exchange friction",
    "Delivery delay / order not received",
    "Wrong item / size shipped",
    "Fit: too slim / tight",
    "Fit: too baggy / loose",
    "Sizing inconsistency / size chart",
    "Wrinkles / creases",
    "Quality / material / defect",
    "CS unresponsive / slow",
    "Out of stock / inventory",
    "Pocket / design feature",
    "Price / promo / value",
    "Website / portal issues",
    "Email marketing volume",
    "Shipping cost",
    "Carrier complaints",
    "Survey timing",
]

POSITIVE_THEMES = [
    "Product praise",
    "CS praise",
    "Fit / sizing praise",
    "Fast delivery",
    "Easy shopping experience",
]


def fqn(table):
    return f'"{DB}"."{SCHEMA}"."{table}"'


def _date_range(days: int):
    """Return (start_date, end_date) strings for the last N days."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def get_nps_scores(days: int = 30) -> dict:
    """
    Fetch NPS scores for each NPS-bearing survey over the last N days.
    Returns dict keyed by display name with score breakdown.
    """
    start_date, end_date = _date_range(days)
    conn = get_connection()
    cur = conn.cursor()

    nps_ids = [SURVEY_IDS[s] for s in NPS_SURVEYS if s in SURVEY_IDS]
    id_placeholders = ", ".join(f"'{sid}'" for sid in nps_ids)

    query = f"""
        SELECT
            s.TITLE,
            COUNT(DISTINCT ra.RESPONSE_ID) as total,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) >= 9 THEN 1 ELSE 0 END) as promoters,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) BETWEEN 7 AND 8 THEN 1 ELSE 0 END) as passives,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) <= 6 THEN 1 ELSE 0 END) as detractors
        FROM {fqn('RESPONSE_ANSWER')} ra
        JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
        JOIN {fqn('SURVEY')} s ON r.SURVEY_ID = s.ID
        WHERE ra.TYPE = 'NPS'
          AND ra.LABEL ILIKE '%recommend%'
          AND r.SURVEY_ID IN ({id_placeholders})
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND ra."VALUE" IS NOT NULL
          AND ra._FIVETRAN_DELETED = FALSE
        GROUP BY s.TITLE
        ORDER BY total DESC
    """

    cur.execute(query)
    rows = cur.fetchall()

    # Merge NPS Survey + Copy under one display name
    merged = {}
    for row in rows:
        title, total, promoters, passives, detractors = row
        display = DISPLAY_NAMES.get(title, title)
        if display in merged:
            m = merged[display]
            m["total"] += total
            m["promoters"] += promoters
            m["passives"] += passives
            m["detractors"] += detractors
        else:
            merged[display] = {
                "total": total,
                "promoters": promoters,
                "passives": passives,
                "detractors": detractors,
            }

    results = {}
    for display, m in merged.items():
        total = m["total"]
        if total > 0:
            nps = round(((m["promoters"] - m["detractors"]) / total) * 100, 1)
            m["nps"] = nps
            m["promoter_pct"] = round(m["promoters"] / total * 100, 1)
            m["passive_pct"] = round(m["passives"] / total * 100, 1)
            m["detractor_pct"] = round(m["detractors"] / total * 100, 1)
        else:
            m["nps"] = None
            m["promoter_pct"] = 0
            m["passive_pct"] = 0
            m["detractor_pct"] = 0
        results[display] = m

    cur.close()
    conn.close()
    return results


def get_nps_trend(days: int = 30) -> dict:
    """
    Compare NPS for current period vs prior period of same length.
    Returns dict keyed by display name with current_nps, prior_nps, delta.
    """
    current = get_nps_scores(days)

    end = datetime.now(timezone.utc) - timedelta(days=days)
    start = end - timedelta(days=days)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    conn = get_connection()
    cur = conn.cursor()

    nps_ids = [SURVEY_IDS[s] for s in NPS_SURVEYS if s in SURVEY_IDS]
    id_placeholders = ", ".join(f"'{sid}'" for sid in nps_ids)

    query = f"""
        SELECT
            s.TITLE,
            COUNT(DISTINCT ra.RESPONSE_ID) as total,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) >= 9 THEN 1 ELSE 0 END) as promoters,
            SUM(CASE WHEN TRY_TO_NUMBER(TRIM(CAST(ra."VALUE" AS VARCHAR), '"')) <= 6 THEN 1 ELSE 0 END) as detractors
        FROM {fqn('RESPONSE_ANSWER')} ra
        JOIN {fqn('RESPONSE')} r ON ra.RESPONSE_ID = r.ID
        JOIN {fqn('SURVEY')} s ON r.SURVEY_ID = s.ID
        WHERE ra.TYPE = 'NPS'
          AND ra.LABEL ILIKE '%recommend%'
          AND r.SURVEY_ID IN ({id_placeholders})
          AND r.CREATED_AT >= '{start_str}'
          AND r.CREATED_AT < '{end_str}'
          AND ra."VALUE" IS NOT NULL
          AND ra._FIVETRAN_DELETED = FALSE
        GROUP BY s.TITLE
    """

    cur.execute(query)
    rows = cur.fetchall()

    prior_merged = {}
    for row in rows:
        title, total, promoters, detractors = row
        display = DISPLAY_NAMES.get(title, title)
        if display in prior_merged:
            prior_merged[display]["total"] += total
            prior_merged[display]["promoters"] += promoters
            prior_merged[display]["detractors"] += detractors
        else:
            prior_merged[display] = {"total": total, "promoters": promoters, "detractors": detractors}

    prior = {}
    for display, m in prior_merged.items():
        if m["total"] > 0:
            prior[display] = round(((m["promoters"] - m["detractors"]) / m["total"]) * 100, 1)
        else:
            prior[display] = None

    cur.close()
    conn.close()

    trend = {}
    for display, data in current.items():
        current_nps = data.get("nps")
        prior_nps = prior.get(display)
        delta = round(current_nps - prior_nps, 1) if (current_nps is not None and prior_nps is not None) else None
        trend[display] = {"current_nps": current_nps, "prior_nps": prior_nps, "delta": delta}

    return trend


def get_open_text_responses(days: int = 30, survey_name: str = "NPS Survey",
                             segment: str = "detractor", limit: int = 80) -> list:
    """
    Fetch open-text responses for a given survey and NPS segment.
    segment: 'detractor' (0-6), 'promoter' (9-10), 'all'
    """
    start_date, end_date = _date_range(days)
    conn = get_connection()
    cur = conn.cursor()

    if survey_name == "NPS Survey":
        survey_ids = [SURVEY_IDS["NPS Survey"], SURVEY_IDS["NPS Survey Copy"]]
    else:
        sid = SURVEY_IDS.get(survey_name)
        survey_ids = [sid] if sid else []

    if not survey_ids:
        return []

    id_placeholders = ", ".join(f"'{sid}'" for sid in survey_ids)

    if segment == "detractor":
        score_filter = "TRY_TO_NUMBER(TRIM(CAST(nps_ans.\"VALUE\" AS VARCHAR), '\"')) <= 6"
    elif segment == "promoter":
        score_filter = "TRY_TO_NUMBER(TRIM(CAST(nps_ans.\"VALUE\" AS VARCHAR), '\"')) >= 9"
    else:
        score_filter = "1=1"

    query = f"""
        SELECT TRIM(CAST(text_ans."VALUE" AS VARCHAR), '"') as comment
        FROM {fqn('RESPONSE_ANSWER')} text_ans
        JOIN {fqn('RESPONSE')} r ON text_ans.RESPONSE_ID = r.ID
        JOIN {fqn('RESPONSE_ANSWER')} nps_ans ON nps_ans.RESPONSE_ID = r.ID
            AND nps_ans.TYPE = 'NPS'
            AND nps_ans.LABEL ILIKE '%recommend%'
        WHERE text_ans.TYPE IN ('Text', 'TextArea')
          AND r.SURVEY_ID IN ({id_placeholders})
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND text_ans."VALUE" IS NOT NULL
          AND LENGTH(TRIM(CAST(text_ans."VALUE" AS VARCHAR), '"')) > 10
          AND text_ans._FIVETRAN_DELETED = FALSE
          AND {score_filter}
        ORDER BY r.CREATED_AT DESC
        LIMIT {limit}
    """

    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [row[0] for row in rows if row[0] and row[0].strip()]


def classify_themes_with_llm(comments: list, segment: str = "detractor") -> list:
    """
    Classify open-text comments against the validated theme taxonomy using GPT.
    Returns list of {theme, count, pct, verbatims} dicts sorted by count desc.
    Only called when n >= 10 comments.
    """
    if len(comments) < 10:
        return []

    from openai import OpenAI
    client = OpenAI()

    if segment == "detractor":
        theme_list = "\n".join(f"- {t}" for t in COMPLAINT_THEMES)
        instruction = (
            "You are analyzing customer NPS detractor comments for Jack Archer (a men's apparel brand). "
            "Classify each comment against the following complaint themes. A comment can match multiple themes. "
            "Return a JSON object with theme names as keys and integer counts as values. "
            "Only include themes with at least 1 match. Also include a 'top_verbatims' key with a list of "
            "2-3 representative short quotes (max 100 chars each) from the most common theme.\n\n"
            f"Themes:\n{theme_list}"
        )
    else:
        theme_list = "\n".join(f"- {t}" for t in POSITIVE_THEMES)
        instruction = (
            "You are analyzing customer NPS promoter comments for Jack Archer (a men's apparel brand). "
            "Classify each comment against the following positive themes. A comment can match multiple themes. "
            "Return a JSON object with theme names as keys and integer counts as values. "
            "Only include themes with at least 1 match. Also include a 'top_verbatims' key with a list of "
            "2-3 representative short quotes (max 100 chars each) from the most common theme.\n\n"
            f"Themes:\n{theme_list}"
        )

    comments_text = "\n".join(f"{i+1}. {c}" for i, c in enumerate(comments[:80]))

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
    total_comments = len(comments)

    themes = []
    for theme_name, count in data.items():
        if isinstance(count, int) and count > 0:
            themes.append({
                "theme": theme_name,
                "count": count,
                "pct": round(count / total_comments * 100, 1),
                "verbatims": top_verbatims if not themes else [],
            })

    themes.sort(key=lambda x: x["count"], reverse=True)
    return themes[:5]


def get_full_nps_report(days: int = 30) -> dict:
    """
    Full NPS report: scores per survey + trend + detractor themes for NPS Survey.
    Main entry point for the /nps command.
    """
    scores = get_nps_scores(days)
    trend = get_nps_trend(days)

    detractor_comments = get_open_text_responses(
        days=days, survey_name="NPS Survey", segment="detractor"
    )
    detractor_themes = classify_themes_with_llm(detractor_comments, segment="detractor")

    return {
        "days": days,
        "scores": scores,
        "trend": trend,
        "detractor_themes": detractor_themes,
        "detractor_comment_count": len(detractor_comments),
    }
