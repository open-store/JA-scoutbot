"""Issue sub-category tags for the support error rate.

The support taxonomy's operational first level is the sub-category under the
``Issue`` category. Each sub-category carries two sets of Richpanel tag names:

- ``live_tags``: Jack Archer auto-tags in ``scout.taxonomy.tag_ids`` that stand
  for the sub-category on tickets. Each one co-occurs with the ``issue``
  category tag on nearly every ticket that carries it.
- ``legacy_tags``: the names the taxonomy's legacy mappings list for the
  sub-category. None of them is in ``TAG_ID_TO_NAME`` and the warehouse has
  no tag table, so they match no ticket today. They stay listed so a UUID
  added for one of them counts without another code change.

``ERROR_RATE_GROUP`` is the one value that picks which sub-category, or all
of ``Issue``, is the error rate's numerator. The tag groups in
``scout.taxonomy.tags`` are separate and are not changed by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple

from scout.taxonomy.tag_ids import TAG_NAME_TO_UUIDS

ISSUE = "Issue"
ISSUE_CATEGORY_TAGS: FrozenSet[str] = frozenset({"issue"})


@dataclass(frozen=True)
class IssueSubcategory:
    name: str
    live_tags: FrozenSet[str]
    legacy_tags: FrozenSet[str]

    @property
    def tag_names(self) -> FrozenSet[str]:
        return self.live_tags | self.legacy_tags


def _sub(name: str, live: Tuple[str, ...], legacy: Tuple[str, ...]) -> IssueSubcategory:
    return IssueSubcategory(name, frozenset(live), frozenset(legacy))


ISSUE_SUBCATEGORIES: Dict[str, IssueSubcategory] = {s.name: s for s in (
    _sub(
        "Fulfillment",
        ("warehouse-processecing-delay", "wrong-items", "wrong_items", "missing-items", "missing_items",
         "product-defective-damage", "product-inventory"),
        ("Order Issue - Backorder", "Order Issue - Barrett Transportation Claims", "Order Issue - Damaged/Empty",
         "Order Issue - EGS Processing Delay", "Order Issue - FC1 Processing Delay",
         "Order Issue - FC1 Transportation Claims", "Order Issue - Incorrect Contents",
         "Order Issue - Incorrect Quantity", "Order Issue - OSW Processing Delay", "Order Issue - Order OOS",
         "Order Issue - RTS", "Replacement  - RTS", "Replacement - Delivered Unreceived",
         "Replacement - Incorrect Order Quantity/Contents"),
    ),
    _sub(
        "Logistics",
        ("lost-in-transit", "order-delays", "delayed_shipment", "return-logistics"),
        ("Order Issue - 10+ Days in Transit (edit with brand)", "Order Issue - 5+ Days in Transit (edit with brand)",
         "Order Issue - Delivered Unrecieved", "Order Issue - Inventory Transfer Delay",
         "Order Issue - Lost in Transit", "Order Issue - Shipping Delay",
         "Order Issue - Unshipped outside of processing time", "Order Issue - Unshipped within processing time",
         "Replacement  - Damaged Items", "Replacement - Lost in Transit", "Replacement - Not received",
         "Returns - Issue"),
    ),
    _sub(
        "Product Quality",
        ("product-quality", "product-size-fit-issue"),
        ("Order Issue - Defective", "Product Issue - Damaged", "Product Issue - Expired",
         "Product Issue - Inconcistency", "Product Issue - Odor", "Product Issue - Other",
         "Product Issue - Overheating", "Product Issue - Quality (came this way)", "Product Issue - Side Effects",
         "Product Issue - Sizing Inconsistency", "Replacement  - Quality Complaint"),
    ),
    _sub(
        "Storefront / UX",
        ("storefront-experience", "web_issues"),
        ("Discount - Code not working", "Refund - Discount code not working", "Tech Issue - Cart/Checkout Issue",
         "Tech Issue - Other Issue", "Tech Issue - Promo Code Not Working",
         "Tech Issue - Social Media Promo Code Not Working", "Tech Issue - Subscriptions/Flows Issue",
         "Tech Issue - Undelivered Email", "Tech Issue - Website Error"),
    ),
    _sub(
        "Customer Support",
        ("customer-support-experience",),
        ("Bad  - CSAT",),
    ),
    _sub(
        "Transaction Processing",
        ("transactions", "payment_issues"),
        ("Account - Billing Issue", "Order Issue - Chargeback", "Order Issue - Chargeback - PayPal",
         "Order Issue - Chargeback - Shopify", "Order Issue - Fraud Verification"),
    ),
    _sub(
        "Legal Issues",
        ("legal-issues",),
        ("Escalations - BBB", "sentiments - urgent"),
    ),
)}

ERROR_RATE_GROUP = "Storefront / UX"


def issue_groups() -> Tuple[str, ...]:
    """Every value ``ERROR_RATE_GROUP`` may take: each sub-category, then all of Issue."""
    return tuple(ISSUE_SUBCATEGORIES) + (ISSUE,)


def issue_tag_names(group: str) -> FrozenSet[str]:
    """Tag names for one sub-category, or for all of Issue including the category tag.

    Raises ``KeyError`` for an unknown group.
    """
    if group == ISSUE:
        names = set(ISSUE_CATEGORY_TAGS)
        for sub in ISSUE_SUBCATEGORIES.values():
            names |= sub.tag_names
        return frozenset(names)
    return ISSUE_SUBCATEGORIES[group].tag_names


def resolve_issue_tags(group: str) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    """``(uuids, resolved_names, unresolved_names)`` for a group, each sorted."""
    uuids = set()
    resolved, unresolved = [], []
    for name in sorted(issue_tag_names(group)):
        found = TAG_NAME_TO_UUIDS.get(name, [])
        if found:
            uuids.update(found)
            resolved.append(name)
        else:
            unresolved.append(name)
    return tuple(sorted(uuids)), tuple(resolved), tuple(unresolved)
