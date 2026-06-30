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


# ── Channel normalisation ───────────────────────────────────────────
# Maps raw survey answer variants → canonical channel labels.
# Social Media is split by platform so the breakdown is actionable.
_CHANNEL_MAP = {
    # Instagram
    "instagram": "Instagram",
    "ig": "Instagram",
    "insta": "Instagram",
    "instagram and facebook": "Instagram",
    "instagram ad": "Instagram",
    # Facebook
    "facebook": "Facebook",
    "fb": "Facebook",
    "fakebook": "Facebook",
    "face book": "Facebook",
    "facebook promo & your website": "Facebook",
    "as on facebook": "Facebook",
    "fb ad": "Facebook",
    "facebook ad": "Facebook",
    "meta": "Facebook",
    # TikTok
    "tiktok": "TikTok",
    # YouTube
    "youtube": "YouTube",
    "you tube": "YouTube",
    "yourube": "YouTube",
    "youtube video": "YouTube",
    "youtube review": "YouTube",
    # Social Media (platform-unspecified)
    "social media": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "social media (instagram, tiktok, facebook, twitter.)": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "socials": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "social media ads": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "scrolling": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "twitter": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "x": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "pinterest": "Social Media (Instagram, TikTok, Facebook, Twitter)",
    # Search / Google variants
    "search engine (google, bing, etc.)": "Search Engine",
    "google": "Search Engine",
    "google search": "Search Engine",
    "google ads": "Search Engine",
    "google ad": "Search Engine",
    "google shopping": "Search Engine",
    "google ai": "Search Engine",
    "bing": "Search Engine",
    "online": "Search Engine",
    "internet": "Search Engine",
    "web": "Search Engine",
    "web search": "Search Engine",
    "web browser": "Search Engine",
    "online search": "Search Engine",
    "internet search": "Search Engine",
    "on line": "Search Engine",
    "on line search": "Search Engine",
    "on-line": "Search Engine",
    "shopped on net": "Search Engine",
    "found online": "Search Engine",
    "research": "Search Engine",
    # Word of mouth variants
    "friend or family member (word of mouth)": "Word of Mouth / Referral",
    "word of mouth/referral": "Word of Mouth / Referral",
    "friend": "Word of Mouth / Referral",
    "a friend": "Word of Mouth / Referral",
    "family": "Word of Mouth / Referral",
    "family member": "Word of Mouth / Referral",
    "referral": "Word of Mouth / Referral",
    "recommendation": "Word of Mouth / Referral",
    "friend recommended": "Word of Mouth / Referral",
    "a buddy": "Word of Mouth / Referral",
    "a buddy of mine said they felt good on the balls": "Word of Mouth / Referral",
    "coworker": "Word of Mouth / Referral",
    "coworkers": "Word of Mouth / Referral",
    # Influencer / Blog
    "influencer or blog": "Influencer / Blog",
    "influencer": "Influencer / Blog",
    "blog": "Influencer / Blog",
    # Podcast
    "podcast": "Podcast",
    "podcast or radio": "Podcast",
    # TV / Traditional
    "tv": "TV / Traditional Media",
    "tv ad": "TV / Traditional Media",
    "tv ad": "TV / Traditional Media",
    "television": "TV / Traditional Media",
    "radio": "TV / Traditional Media",
    "direct mail": "Direct Mail",
    "mail": "Direct Mail",
    "catalog": "Direct Mail",
    # Email / SMS
    "email": "Email / SMS",
    "email / sms": "Email / SMS",
    "sms": "Email / SMS",
    "text or email": "Email / SMS",
    "e mail": "Email / SMS",
    # AI-assisted discovery (emerging channel)
    "chatgpt": "AI Discovery",
    "chat gpt": "AI Discovery",
    "chat gpt": "AI Discovery",
    "gemini": "AI Discovery",
    "grok": "AI Discovery",
    "claude": "AI Discovery",
    # Other (keep as-is — write-ins handled separately)
    "other (please specify)": "Other",
    "other": "Other",
}

# Canonical channels shown in the main how_heard distribution.
# Anything not in this set is treated as a write-in and routed to Other breakdown.
CANONICAL_CHANNELS = {
    "Instagram", "Facebook", "TikTok", "YouTube", "Social Media (Instagram, TikTok, Facebook, Twitter)",
    "Search Engine", "Word of Mouth / Referral", "Influencer / Blog",
    "Podcast", "TV / Traditional Media", "Direct Mail", "Email / SMS",
    "AI Discovery", "Other",
}


def _normalise_channel(raw_label: str) -> str:
    """Map a raw channel answer to its canonical label."""
    key = raw_label.strip().rstrip(" ").lower()
    return _CHANNEL_MAP.get(key, raw_label.strip())


def _count_values(rows, normalise: bool = False, canonical_only: bool = False) -> dict:
    """
    Aggregate a list of (value,) rows into a frequency dict.
    normalise=True  → map raw values to canonical labels via _CHANNEL_MAP
    canonical_only=True → after normalising, only keep labels in CANONICAL_CHANNELS
                          (non-canonical write-ins are excluded from the main distribution
                           and handled separately by _cluster_other_writeins)
    """
    counts = {}
    for (raw,) in rows:
        for val in _parse_value(raw):
            if val:
                label = _normalise_channel(val) if normalise else val
                if canonical_only and label not in CANONICAL_CHANNELS:
                    continue
                counts[label] = counts.get(label, 0) + 1
    return counts


def _top_n(counts: dict, n: int = None) -> list:
    """Return top-N items (all items if n is None) as list of {label, count, pct} sorted by count."""
    total = sum(counts.values())
    if total == 0:
        return []
    sorted_items = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    if n is not None:
        sorted_items = sorted_items[:n]
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

    def fetch_distribution(label_pattern: str, normalise: bool = False, canonical_only: bool = False) -> list:
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
        return _top_n(_count_values(cur.fetchall(), normalise=normalise, canonical_only=canonical_only))

    # how_heard: normalise variants, show only canonical channels (write-ins handled separately)
    how_heard = fetch_distribution("%hear about%", normalise=True, canonical_only=True)
    what_brought = fetch_distribution("%brought you%")
    consideration_window = fetch_distribution("%long did you know%")
    who_for = fetch_distribution("%purchase this for%")

    # Fetch "Other (Please specify)" write-in text for how_heard
    cur.execute(f"""
        SELECT TRIM(CAST(other_ans."VALUE" AS VARCHAR), '"') as writeins
        FROM {fqn('RESPONSE_ANSWER')} other_ans
        JOIN {fqn('RESPONSE')} r ON other_ans.RESPONSE_ID = r.ID
        JOIN {fqn('RESPONSE_ANSWER')} heard_ans ON heard_ans.RESPONSE_ID = r.ID
            AND heard_ans.LABEL ILIKE '%hear about%'
        WHERE r.SURVEY_ID = '{survey_id}'
          AND other_ans.LABEL ILIKE '%specify%'
          AND other_ans.TYPE IN ('Text', 'TextArea')
          AND r.CREATED_AT >= '{start_date}'
          AND r.CREATED_AT < '{end_date}'
          AND other_ans."VALUE" IS NOT NULL
          AND LENGTH(TRIM(CAST(other_ans."VALUE" AS VARCHAR), '"')) > 3
          AND other_ans._FIVETRAN_DELETED = FALSE
        ORDER BY r.CREATED_AT DESC
        LIMIT 120
    """)
    other_writeins = [r[0] for r in cur.fetchall() if r[0] and r[0].strip()]

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
    other_breakdown = _cluster_other_writeins(other_writeins)

    return {
        "days": days,
        "response_count": response_count,
        "how_heard": how_heard,
        "what_brought": what_brought,
        "consideration_window": consideration_window,
        "who_for": who_for,
        "other_writeins_count": len(other_writeins),
        "other_breakdown": other_breakdown,
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


def _cluster_other_writeins(writeins: list) -> list:
    """
    Use GPT to cluster 'Other (Please specify)' write-in responses for how_heard
    into meaningful sub-categories. Returns list of {label, count, pct} dicts.
    Only called when n >= 5 write-ins.
    """
    if len(writeins) < 5:
        return []

    from openai import OpenAI
    client = OpenAI()

    instruction = (
        "You are analyzing free-text 'Other' responses from a post-purchase survey asking "
        "new customers of Jack Archer (a men's apparel brand) how they first heard about the brand. "
        "Group these responses into clear, concise channel categories (e.g. 'YouTube', 'Reddit', "
        "'Friend recommendation', 'Podcast', 'Email', 'Saw an ad', etc.). "
        "Return a JSON object with category names as keys and integer counts as values. "
        "Aim for 4-8 categories. Do not include a 'top_verbatims' key."
    )

    text = "\n".join(f"{i+1}. {w}" for i, w in enumerate(writeins[:100]))

    try:
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": instruction},
                {"role": "user", "content": f"Write-in responses:\n{text}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        data = json.loads(response.choices[0].message.content)
    except Exception:
        return []

    total = len(writeins)
    results = []
    for label, count in data.items():
        if isinstance(count, int) and count > 0:
            results.append({
                "label": label,
                "count": count,
                "pct": round(count / total * 100, 1),
            })

    results.sort(key=lambda x: x["count"], reverse=True)
    return results
