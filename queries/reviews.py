"""Okendo Reviews integration — direct API calls."""

import os
import base64
import json
import urllib.parse
import requests
from datetime import datetime, timedelta
from collections import Counter


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


def _fetch_reviews(
    days: int,
    status: str = "approved",
    max_pages: int = 20,
) -> list[dict]:
    """Fetch approved reviews, paginating up to max_pages * 100 reviews.

    Okendo doesn't have a date filter param, so we fetch in reverse
    chronological order and stop when we pass the date boundary.
    """
    since = datetime.utcnow() - timedelta(days=days)
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

        for r in reviews:
            created = r.get("dateCreated", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                dt_naive = dt.replace(tzinfo=None)
            except (ValueError, AttributeError):
                dt_naive = datetime.utcnow()

            if dt_naive < since:
                # We've passed the date boundary — stop
                return all_reviews

            all_reviews.append(r)

        # Check for next page cursor
        next_url = data.get("nextUrl")
        if not next_url:
            break

        # Extract lastEvaluated from the next URL
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(next_url)
            qs = parse_qs(parsed.query)
            last_evaluated = qs.get("lastEvaluated", [None])[0]
            if not last_evaluated:
                break
        except Exception:
            break

    return all_reviews


def _fetch_reviews_prior(days: int) -> list[dict]:
    """Fetch reviews from the prior period for comparison.

    Since Okendo doesn't have date range filters, we fetch more data
    and filter client-side.
    """
    end = datetime.utcnow() - timedelta(days=days)
    start = end - timedelta(days=days)
    all_reviews: list[dict] = []
    last_evaluated = None

    for _ in range(40):  # More pages to reach the prior window
        params: dict = {
            "limit": 100,
            "orderBy": "date desc",
            "status": "approved",
        }
        if last_evaluated:
            params["lastEvaluated"] = last_evaluated

        data = _api_get("/reviews", params)
        reviews = data.get("reviews", [])

        if not reviews:
            break

        for r in reviews:
            created = r.get("dateCreated", "")
            try:
                dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                dt_naive = dt.replace(tzinfo=None)
            except (ValueError, AttributeError):
                dt_naive = datetime.utcnow()

            if dt_naive < start:
                return all_reviews
            if dt_naive <= end:
                all_reviews.append(r)

        next_url = data.get("nextUrl")
        if not next_url:
            break
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(next_url)
            qs = parse_qs(parsed.query)
            last_evaluated = qs.get("lastEvaluated", [None])[0]
            if not last_evaluated:
                break
        except Exception:
            break

    return all_reviews


def run_reviews(days: int, product_filter: str | None = None) -> dict:
    """Analyze reviews for the given time window.

    Returns a dict with: avg_rating, total_reviews, rating_distribution,
    sentiment_breakdown, top_products, top_tags, sample_positive,
    sample_negative, period_start, period_end, prior_avg_rating, change.
    """
    reviews = _fetch_reviews(days)

    # Apply product filter if specified
    if product_filter:
        pf = product_filter.lower()
        reviews = [
            r for r in reviews
            if pf in (r.get("productName") or "").lower()
            or pf in (r.get("title") or "").lower()
            or pf in (r.get("body") or "").lower()
        ]

    result = _analyze_reviews(reviews, days)

    # Prior period comparison
    prior_reviews = _fetch_reviews_prior(days)
    if product_filter:
        pf = product_filter.lower()
        prior_reviews = [
            r for r in prior_reviews
            if pf in (r.get("productName") or "").lower()
            or pf in (r.get("title") or "").lower()
            or pf in (r.get("body") or "").lower()
        ]

    prior = _analyze_reviews(prior_reviews, days, prior=True)
    result["prior_avg_rating"] = prior["avg_rating"]
    result["prior_total_reviews"] = prior["total_reviews"]

    if result["avg_rating"] is not None and prior["avg_rating"] is not None:
        result["rating_change"] = round(result["avg_rating"] - prior["avg_rating"], 2)
    else:
        result["rating_change"] = None

    if result["total_reviews"] > 0 and prior["total_reviews"] > 0:
        result["volume_change_pct"] = round(
            (result["total_reviews"] - prior["total_reviews"]) / prior["total_reviews"] * 100, 1
        )
    else:
        result["volume_change_pct"] = None

    result["product_filter"] = product_filter
    return result


def _analyze_reviews(reviews: list[dict], days: int, prior: bool = False) -> dict:
    """Compute review analytics from a list of review objects."""
    if not reviews:
        offset = days if prior else 0
        return {
            "avg_rating": None,
            "total_reviews": 0,
            "rating_distribution": {},
            "sentiment_breakdown": {},
            "top_products": [],
            "top_tags": [],
            "sample_positive": None,
            "sample_negative": None,
            "period_start": (datetime.utcnow() - timedelta(days=days + offset)).strftime("%b %-d"),
            "period_end": (datetime.utcnow() - timedelta(days=offset)).strftime("%b %-d, %Y"),
        }

    total = len(reviews)
    ratings = [r.get("rating", 0) for r in reviews if r.get("rating")]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

    # Rating distribution (1-5 stars)
    rating_dist = Counter(ratings)
    rating_distribution = {i: rating_dist.get(i, 0) for i in range(1, 6)}

    # Sentiment breakdown
    sentiments = [r.get("sentiment", "unknown") for r in reviews]
    sentiment_breakdown = dict(Counter(sentiments))

    # Top products by review count
    product_counter = Counter(r.get("productName", "Unknown") for r in reviews)
    top_products = []
    for product, count in product_counter.most_common(5):
        prod_ratings = [r.get("rating", 0) for r in reviews if r.get("productName") == product and r.get("rating")]
        avg = round(sum(prod_ratings) / len(prod_ratings), 2) if prod_ratings else None
        top_products.append({"product": product, "count": count, "avg_rating": avg})

    # Top tags
    all_tags = []
    for r in reviews:
        tags = r.get("tags", [])
        if tags:
            all_tags.extend(tags)
    top_tags = Counter(all_tags).most_common(5)

    # Sample positive review (highest rated, most helpful)
    positive_reviews = sorted(
        [r for r in reviews if r.get("rating", 0) >= 4],
        key=lambda x: (x.get("rating", 0), x.get("helpfulCount", 0)),
        reverse=True,
    )
    sample_positive = None
    if positive_reviews:
        r = positive_reviews[0]
        sample_positive = {
            "title": r.get("title", ""),
            "body": (r.get("body") or "")[:200],
            "rating": r.get("rating"),
            "product": r.get("productName", ""),
        }

    # Sample negative review (lowest rated)
    negative_reviews = sorted(
        [r for r in reviews if r.get("rating", 0) <= 2],
        key=lambda x: x.get("rating", 0),
    )
    sample_negative = None
    if negative_reviews:
        r = negative_reviews[0]
        sample_negative = {
            "title": r.get("title", ""),
            "body": (r.get("body") or "")[:200],
            "rating": r.get("rating"),
            "product": r.get("productName", ""),
        }

    offset = days if prior else 0
    return {
        "avg_rating": avg_rating,
        "total_reviews": total,
        "rating_distribution": rating_distribution,
        "sentiment_breakdown": sentiment_breakdown,
        "top_products": top_products,
        "top_tags": top_tags,
        "sample_positive": sample_positive,
        "sample_negative": sample_negative,
        "period_start": (datetime.utcnow() - timedelta(days=days + offset)).strftime("%b %-d"),
        "period_end": (datetime.utcnow() - timedelta(days=offset)).strftime("%b %-d, %Y"),
    }
