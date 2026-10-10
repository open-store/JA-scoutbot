"""Keyword themes from CSAT ticket subjects.

Analysis, not Slack formatting. ``format_csat`` calls this and prints the counts.
"""

def _extract_themes_from_subjects(subjects: list[str]) -> list[tuple[str, int]]:
    """
    Simple keyword-based theme extraction from subject lines.
    Returns list of (theme, count) sorted by count descending.
    """
    theme_keywords = {
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

    theme_counts = {}
    for subject in subjects:
        if not subject:
            continue
        subject_lower = subject.lower()
        for theme, keywords in theme_keywords.items():
            if any(kw in subject_lower for kw in keywords):
                theme_counts[theme] = theme_counts.get(theme, 0) + 1

    sorted_themes = sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_themes

