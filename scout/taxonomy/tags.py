"""
Tag taxonomy: groups known Richpanel tags into business-relevant scopes.

Tags are stored as UUID arrays in CONVERSATIONS.TAGS and resolved via
tag_mapping.TAG_ID_TO_NAME.  This module provides the reverse mapping
(tag-name → scope) so the pipeline can retrieve candidate conversations
by scope without hard-coding UUIDs everywhere.

To extend: add new tag names to the appropriate group, or add a new group.
"""
from __future__ import annotations

from typing import Dict, FrozenSet, List, Optional, Set

# ---------------------------------------------------------------------------
# Tag group definitions  (tag *names*, not UUIDs)
# ---------------------------------------------------------------------------

TAG_GROUPS: Dict[str, FrozenSet[str]] = {
    "product_feedback": frozenset({
        "product-quality",
        "product-defective-damage",
        "product-size-fit-issue",
        "product-spec-other",
        "product_inquiry",
        "product_recommendations",
        "product_comparison",
        "product_availability",
        "product-inventory",
        "product-suggestion",
        "product-reviews",
        "product_reviews_and_ratings",
        "product_setup/installation",
        "product_restocking_requests",
        "variant-mismatch",
        "size/fit_assistance",
        "damaged_items",
    }),
    "returns": frozenset({
        "return/exchange_request",
        "return-refund-other-status",
        "return-logistics",
        "misc-refund-exchange",
    }),
    "shipping": frozenset({
        "international_shipping",
        "delayed_shipment",
        "order-delays",
        "lost-in-transit",
        "warehouse-processecing-delay",
    }),
    "pricing_promos": frozenset({
        "pricing",
        "price_matching",
        "discounts-gift-promos",
        "coupon_code/promotion_inquiries",
        "gift_card_redemption",
    }),
    "order_status": frozenset({
        "order_status",
        "order-status",
        "order_modification",
        "order-modification",
        "order_cancellation",
        "order-cancellation",
        "order_confirmation/resend_emails",
        "order_history_inquiry",
        "order_bulk_tracking",
        "order_customization",
        "missing_items",
        "missing-items",
        "wrong-items",
        "wrong_items",
    }),
    "web_errors": frozenset({
        "web_issues",
        "storefront-experience",
    }),
    "customer_support_experience": frozenset({
        "customer-support-experience",
        "dsats",
    }),
}

# Flattened reverse index: tag_name → set of group names it belongs to
_TAG_TO_GROUPS: Dict[str, Set[str]] = {}
for _group, _names in TAG_GROUPS.items():
    for _name in _names:
        _TAG_TO_GROUPS.setdefault(_name, set()).add(_group)


def tag_names_for_group(group: str) -> FrozenSet[str]:
    """Return all tag names belonging to a given group.

    Raises ``KeyError`` if the group is unknown.
    """
    return TAG_GROUPS[group]


def groups_for_tag(tag_name: str) -> Set[str]:
    """Return the set of groups a tag name belongs to (may be empty)."""
    return _TAG_TO_GROUPS.get(tag_name, set())


def all_group_names() -> List[str]:
    """Return a sorted list of all known group names."""
    return sorted(TAG_GROUPS.keys())
