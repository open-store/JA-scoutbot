"""KnoCommerce NPS integration — direct API calls."""

import os
import base64
import urllib.parse
import requests
from datetime import datetime, timedelta


# ── Auth ────────────────────────────────────────────────────────────
_TOKEN_CACHE: dict = {"token": None, "expires": 0}

KNO_BASE = "https://app-api.knocommerce.com"


def _get_token() -> str:
    """Obtain or reuse an OAuth 2.0 Bearer token."""
    import time

    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires"] - 60:
        return _TOKEN_CACHE["token"]

    client_id = os.environ["KNOCOMMERCE_CLIENT_ID"]
    client_secret = os.environ["KNOCOMMERCE_SECRET"]

    creds = f"{urllib.parse.quote(client_id, safe='')}:{urllib.parse.quote(client_secret, safe='')}"
    basic = base64.b64encode(creds.encode()).decode()

    resp = requests.post(
        f"{KNO_BASE}/api/oauth2/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data="grant_type=client_credentials&scope=SURVEYS+RESPONSES",
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _TOKEN_CACHE["token"] = data["access_token"]
    _TOKEN_CACHE["expires"] = now + data.get("expires_in", 3600)
    return _TOKEN_CACHE["token"]


def _api_get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET against the KnoCommerce API."""
    token = _get_token()
    resp = requests.get(
        f"{KNO_BASE}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Surveys discovery ───────────────────────────────────────────────

def list_surveys() -> list[dict]:
    """Return all surveys from KnoCommerce."""
    return _api_get("/api/rest/surveys")


# ── Response fetching ───────────────────────────────────────────────

def _fetch_responses(
    days: int,
    survey_id: str | None = None,
    question_id: str | None = None,
    max_pages: int = 20,
) -> list[dict]:
    """Fetch completed responses within the last *days* days.

    Uses cursor pagination, up to *max_pages* pages of 250 each.
    """
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    params: dict = {
        "maxPageSize": 250,
        "status": "completed",
        "completedAt[gte]": since,
    }
    if survey_id:
        params["surveyId"] = survey_id
    if question_id:
        params["questionId"] = question_id

    all_results: list[dict] = []
    for _ in range(max_pages):
        data = _api_get("/api/rest/responses", params)
        results = data.get("results", [])
        all_results.extend(results)
        if not data.get("hasMore") or not data.get("nextPageToken"):
            break
        params["pageToken"] = data["nextPageToken"]
        # Remove prevPageToken if present
        params.pop("prevPageToken", None)

    return all_results


# ── NPS calculation ─────────────────────────────────────────────────

def _extract_nps_score(response: dict) -> int | None:
    """Try to extract an NPS score (0-10) from a response's answers.

    KnoCommerce stores answers in a 'response' array. We look for
    numeric answers in the 0-10 range that are likely NPS.
    """
    answers = response.get("response", [])
    if not answers:
        return None

    for answer in answers:
        # Answers can be structured differently — try common patterns
        val = answer.get("value") or answer.get("answer") or answer.get("text")
        if val is None:
            continue
        try:
            score = int(float(str(val)))
            if 0 <= score <= 10:
                return score
        except (ValueError, TypeError):
            continue
    return None


def run_nps(days: int) -> dict:
    """Calculate NPS for the given time window.

    Returns a dict with: nps, promoters, passives, detractors,
    total_responses, promoter_pct, passive_pct, detractor_pct,
    score_distribution, period_start, period_end, prior_nps, change.
    """
    # Current period
    responses = _fetch_responses(days)
    scores = []
    for r in responses:
        s = _extract_nps_score(r)
        if s is not None:
            scores.append(s)

    result = _calc_nps(scores, days)

    # Prior period for comparison
    prior_responses = _fetch_responses_prior(days)
    prior_scores = []
    for r in prior_responses:
        s = _extract_nps_score(r)
        if s is not None:
            prior_scores.append(s)

    prior = _calc_nps(prior_scores, days, prior=True)
    result["prior_nps"] = prior["nps"]
    result["prior_responses"] = prior["total_responses"]
    if result["nps"] is not None and prior["nps"] is not None:
        result["change"] = round(result["nps"] - prior["nps"], 1)
    else:
        result["change"] = None

    return result


def _fetch_responses_prior(days: int) -> list[dict]:
    """Fetch responses from the prior period (days*2 to days ago)."""
    end = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    start = (datetime.utcnow() - timedelta(days=days * 2)).strftime("%Y-%m-%d")
    params: dict = {
        "maxPageSize": 250,
        "status": "completed",
        "completedAt[gte]": start,
        "completedAt[lte]": end,
    }
    all_results: list[dict] = []
    for _ in range(20):
        data = _api_get("/api/rest/responses", params)
        results = data.get("results", [])
        all_results.extend(results)
        if not data.get("hasMore") or not data.get("nextPageToken"):
            break
        params["pageToken"] = data["nextPageToken"]
        params.pop("prevPageToken", None)
    return all_results


def _calc_nps(scores: list[int], days: int, prior: bool = False) -> dict:
    """Calculate NPS metrics from a list of scores."""
    if not scores:
        return {
            "nps": None,
            "promoters": 0,
            "passives": 0,
            "detractors": 0,
            "total_responses": 0,
            "promoter_pct": 0,
            "passive_pct": 0,
            "detractor_pct": 0,
            "score_distribution": {},
            "period_start": (datetime.utcnow() - timedelta(days=days * (2 if prior else 1))).strftime("%b %-d"),
            "period_end": (datetime.utcnow() - timedelta(days=days if prior else 0)).strftime("%b %-d, %Y"),
        }

    total = len(scores)
    promoters = sum(1 for s in scores if s >= 9)
    passives = sum(1 for s in scores if 7 <= s <= 8)
    detractors = sum(1 for s in scores if s <= 6)

    nps = round((promoters / total - detractors / total) * 100, 1)

    # Score distribution
    dist = {}
    for s in range(11):
        count = scores.count(s)
        if count > 0:
            dist[s] = count

    offset = days if prior else 0
    return {
        "nps": nps,
        "promoters": promoters,
        "passives": passives,
        "detractors": detractors,
        "total_responses": total,
        "promoter_pct": round(promoters / total * 100, 1),
        "passive_pct": round(passives / total * 100, 1),
        "detractor_pct": round(detractors / total * 100, 1),
        "score_distribution": dist,
        "period_start": (datetime.utcnow() - timedelta(days=days + offset)).strftime("%b %-d"),
        "period_end": (datetime.utcnow() - timedelta(days=offset)).strftime("%b %-d, %Y"),
    }
