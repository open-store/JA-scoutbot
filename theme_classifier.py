"""Deterministic subject-line theme classification helpers for Scout."""

import re
from collections import Counter

THEME_KEYWORDS = {
    "Shipping/Delivery": ["shipping", "delivery", "tracking", "shipped", "transit", "usps", "ups", "fedex", "delayed", "lost package", "where is my order", "wismo"],
    "Returns/Exchanges": ["return", "exchange", "refund", "send back", "return label", "rma"],
    "Sizing/Fit": ["size", "sizing", "fit", "too big", "too small", "measurements", "length"],
    "Discount/Promo Codes": ["discount", "promo", "coupon", "code", "promotion"],
    "Order Issues": ["order", "cancel", "cancellation", "wrong item", "missing item", "incomplete"],
    "Product Quality": ["quality", "defect", "damaged", "broken", "stain", "hole", "tear", "fabric"],
    "Account/Login": ["account", "login", "password", "sign in", "email"],
    "Subscription/Billing": ["subscription", "billing", "charge", "charged", "recurring"],
    "General Inquiry": ["question", "inquiry", "info", "information", "help"],
}


def _normalize(text: str) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def classify_subject(subject: str) -> str | None:
    """Return a single best-fit theme label, or None if unclassified."""
    normalized = _normalize(subject)
    if not normalized:
        return None

    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return theme
    return None


def summarize_subject_themes(subjects: list[str]) -> dict:
    """Return deterministic theme summary with counts and unclassified coverage."""
    counts: Counter = Counter()
    unclassified = 0

    for subject in subjects:
        theme = classify_subject(subject)
        if theme is None:
            unclassified += 1
        else:
            counts[theme] += 1

    sorted_themes = counts.most_common()
    total = len([s for s in subjects if s is not None])
    coverage = round((sum(counts.values()) / total) * 100, 1) if total > 0 else 0
    unclassified_pct = round((unclassified / total) * 100, 1) if total > 0 else 0

    return {
        "themes": sorted_themes,
        "classified_count": sum(counts.values()),
        "unclassified_count": unclassified,
        "total_subjects": total,
        "coverage_pct": coverage,
        "unclassified_pct": unclassified_pct,
    }
