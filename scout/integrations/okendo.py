"""Okendo reviews HTTP API.

Pagination is one loop. Callers pass a ``[start, end]`` window.
``end is None`` means no upper bound, which is how the current period
was fetched. Dates stay naive UTC via ``datetime.utcnow`` so the window
matches the previous two fetchers.
"""

import os
import base64
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import requests

OKENDO_BASE = "https://api.okendo.io/enterprise"


def _auth_header() -> str:
    """Build Basic auth header from subscriberId + API key."""
    subscriber_id = os.environ["OKENDO_SUBSCRIBER_ID"]
    api_key = os.environ["OKENDO_API_KEY"]
    creds = base64.b64encode(f"{subscriber_id}:{api_key}".encode()).decode()
    return f"Basic {creds}"


def _api_get(path: str, params: dict | None = None) -> dict:
    """Authenticated GET against the Okendo Merchant REST API."""
    resp = requests.get(
        f"{OKENDO_BASE}{path}",
        headers={"Authorization": _auth_header(), "Accept": "application/json"},
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_reviews_between(
    start: datetime,
    end: datetime | None,
    *,
    max_pages: int,
    status: str = "approved",
) -> list[dict]:
    """Fetch reviews with ``dateCreated`` in ``[start, end]``.

    Reviews come back newest first. The walk stops at the first review
    older than ``start``. When ``end`` is set, newer reviews are skipped
    and pagination continues. ``end is None`` keeps every review at or
    after ``start``.
    """
    all_reviews: list[dict] = []
    last_evaluated = None

    for _ in range(max_pages):
        params: dict = {
            "limit": 100,
            "orderBy": "date desc",
            "status": status,
        }
        if last_evaluated:
            params["lastEvaluated"] = last_evaluated

        data = _api_get("/reviews", params)
        reviews = data.get("reviews", [])

        if not reviews:
            break

        for review in reviews:
            created = review.get("dateCreated", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                dt_naive = dt.replace(tzinfo=None)
            except (ValueError, AttributeError):
                dt_naive = datetime.utcnow()

            if dt_naive < start:
                return all_reviews
            if end is not None and dt_naive > end:
                continue
            all_reviews.append(review)

        next_url = data.get("nextUrl")
        if not next_url:
            break

        try:
            parsed = urlparse(next_url)
            qs = parse_qs(parsed.query)
            last_evaluated = qs.get("lastEvaluated", [None])[0]
            if not last_evaluated:
                break
        except Exception:
            break

    return all_reviews


def fetch_current_reviews(days: int, status: str = "approved", max_pages: int = 20) -> list[dict]:
    """Reviews in the current window: now minus ``days``, no upper bound."""
    from datetime import timedelta
    since = datetime.utcnow() - timedelta(days=days)
    return fetch_reviews_between(since, None, max_pages=max_pages, status=status)


def fetch_prior_reviews(days: int) -> list[dict]:
    """Reviews in the prior window of the same length, for comparison."""
    from datetime import timedelta
    end = datetime.utcnow() - timedelta(days=days)
    start = end - timedelta(days=days)
    return fetch_reviews_between(start, end, max_pages=40)
