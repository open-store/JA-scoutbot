"""Okendo review analytics.

HTTP pagination lives in ``scout.integrations.okendo``. This module
turns the review list into the dict ``format_reviews`` reads.
"""

from collections import Counter
from datetime import datetime, timedelta

from scout.integrations.okendo import fetch_current_reviews, fetch_prior_reviews

# ``fetch_current_reviews`` stops after 20 pages of 100.
CURRENT_FETCH_CAP = 2000

EVIDENCE_PER_SIDE = 30


def review_evidence(reviews: list[dict], per_side: int = EVIDENCE_PER_SIDE) -> list[dict]:
    """Most recent low-rated (1–3★) and high-rated (4–5★) reviews with text, for cited synthesis."""
    def item(r: dict) -> dict:
        text = " ".join(p for p in ((r.get("title") or "").strip(), (r.get("body") or "").strip()) if p)
        return {
            "review_id": str(r.get("reviewId") or ""),
            "rating": r.get("rating"),
            "product": r.get("productName") or "",
            "text": text,
            "created_at": r.get("dateCreated"),
        }

    with_text = [r for r in reviews if (r.get("body") or r.get("title")) and r.get("rating")]
    low = [item(r) for r in with_text if r["rating"] <= 3][:per_side]
    high = [item(r) for r in with_text if r["rating"] >= 4][:per_side]
    return low + high


def run_reviews(days: int, product_filter: str | None = None) -> dict:
    """Analyze reviews for the given time window.

    Returns a dict with: avg_rating, total_reviews, rating_distribution,
    sentiment_breakdown, top_products, top_tags, sample_positive,
    sample_negative, period_start, period_end, prior_avg_rating, change.
    """
    reviews = fetch_current_reviews(days)
    fetch_capped = len(reviews) >= CURRENT_FETCH_CAP

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
    result["evidence"] = review_evidence(reviews)
    result["fetch_capped"] = fetch_capped

    # Prior period comparison
    prior_reviews = fetch_prior_reviews(days)
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
