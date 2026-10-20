"""
Product attribution for customer evidence.

Each cleaned customer message gets one tier (see
``scout.models.product_feedback``) and the method that decided it:

Confirmed
  ``customer_mention``      The customer's message names this product and no other.
  ``same_thread_mention``   The customer named only this product elsewhere in the thread.
  ``subject_reference``     The subject names this product, and the customer wrote
                            the first message (so the subject is theirs).
Order-linked
  ``order_single_product``  A referenced order, matched to this customer, contains
                            only this product.
  ``order_item_match``      A referenced multi-item order; the garment the customer
                            names ("the shorts") matches only this product's line.
Inferred
  ``purchase_history_single_product`` / ``purchase_history_item_match``
                            Same rules over the customer's orders placed before the
                            ticket within ``LOOKBACK_DAYS``. Never the latest order
                            by default.
Unresolved
  Several products named, a referenced order that conflicts or cannot be
  matched to the customer, a multi-item order with no item named, several
  plausible recent purchases, unverified identity, or no order data.

A message about a different product is ``other_product`` and is not
evidence for this product. The referenced order is used before purchase
history. A complaint is never attached to every item in an order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from scout.models.orders import OrderRecord
from scout.models.product_feedback import (
    CONFIRMED,
    INFERRED,
    ORDER_LINKED,
    OTHER_PRODUCT,
    UNRESOLVED,
    ProductAttribution,
)
from scout.taxonomy.products import (
    FAMILY,
    GARMENT_NOUNS,
    PHRASE,
    STYLE,
    ProductCatalog,
    ProductMention,
    ProductResolution,
    find_product_mentions,
    match_key,
    resolve_product,
)

LOOKBACK_DAYS = 90

_ORDER_REF_RE = re.compile(
    r"(?:\border\s*(?:number|no\.?|num|#)?\s*[:#]?\s*#?|#)\s?(\d{4,8})\b",
    re.I,
)

_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_UNVERIFIABLE_DOMAINS = ("jackarcher.com", "open.store", "marketplace.amazon.com", "richpanel.com")
_UNVERIFIABLE_LOCAL = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon")

# Words that are usually not garments in the singular ("too short", "on top",
# "in brief") name the garment only in the plural.
_PLURAL_ONLY = frozenset({"short", "top", "bottom", "brief", "tank", "cap", "crew", "oxford", "zip"})
_GARMENT_RE = re.compile(
    r"\b(" + "|".join(sorted(GARMENT_NOUNS - _PLURAL_ONLY, key=len, reverse=True)) + r")s?\b"
    r"|\b(" + "|".join(sorted(_PLURAL_ONLY, key=len, reverse=True)) + r")s\b"
    r"|\b(t-shirts?)\b",
    re.I,
)


def order_numbers_in(text: str) -> List[str]:
    return list(dict.fromkeys(m.group(1) for m in _ORDER_REF_RE.finditer(text or "")))


def verified_email(email: Optional[str]) -> Optional[str]:
    """Normalised customer email if it can identify a shopper, else None.

    Store, relay, and no-reply addresses are rejected because they do not
    identify one customer's orders.
    """
    if not email:
        return None
    value = email.strip().lower()
    if not _EMAIL_SHAPE.match(value):
        return None
    local, _, domain = value.partition("@")
    if any(domain == d or domain.endswith("." + d) for d in _UNVERIFIABLE_DOMAINS):
        return None
    if any(local.startswith(p) for p in _UNVERIFIABLE_LOCAL):
        return None
    return value


def garments_in(text: str) -> Set[str]:
    out: Set[str] = set()
    for m in _GARMENT_RE.finditer(text or ""):
        if m.group(3):
            out.add("shirt")
            continue
        word = (m.group(1) or m.group(2) or "").lower()
        if word in GARMENT_NOUNS:
            out.add(word)
    return out


def as_aware(value: Optional[datetime]) -> Optional[datetime]:
    """Treat a naive timestamp as UTC so it compares with timezone-aware ones."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


_aware = as_aware


@dataclass(frozen=True)
class ConversationContext:
    conversation_id: str
    subject: str = ""
    channel: str = ""
    created_at: Optional[datetime] = None
    customer_email: Optional[str] = None
    first_from_customer: bool = False

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> "ConversationContext":
        created = row.get("CREATED_AT")
        return cls(
            conversation_id=str(row.get("CONVERSATION_ID") or ""),
            subject=str(row.get("SUBJECT") or ""),
            channel=str(row.get("CHANNEL") or ""),
            created_at=created if isinstance(created, datetime) else None,
            customer_email=str(row["CUSTOMER_EMAIL"]) if row.get("CUSTOMER_EMAIL") else None,
            first_from_customer=bool(row.get("FIRST_FROM_CUSTOMER")),
        )


class ProductTarget:
    """What counts as "this product" for a resolution: one style, a family, or a typed phrase."""

    def __init__(self, resolution: ProductResolution, catalog: ProductCatalog):
        self.resolution = resolution
        self.catalog = catalog
        self.name = resolution.canonical_name or resolution.query
        self.level = resolution.level
        self.styles = set(resolution.styles)
        self.key = match_key(self.name)
        self._garments: Optional[Set[str]] = None

    def is_mention(self, mention: ProductMention) -> bool:
        if self.level == STYLE:
            return mention.level == STYLE and mention.name in self.styles
        if self.level == FAMILY:
            return (mention.level == STYLE and mention.name in self.styles) or (
                mention.level == FAMILY and mention.name == self.name)
        return mention.level == PHRASE and match_key(mention.name) == self.key

    def is_other(self, mention: ProductMention) -> bool:
        """A catalog product outside this target. Family mentions of a style's own family are neutral."""
        if self.is_mention(mention):
            return False
        if mention.level == STYLE:
            return True
        if mention.level == FAMILY:
            return mention.name != self.resolution.family
        return False

    def product_of_line(self, title: str) -> str:
        """Catalog style for an order-line title, or the title itself when it does not resolve."""
        r = resolve_product(title, self.catalog)
        if r.is_resolved and r.level == STYLE and r.canonical_name:
            return r.canonical_name
        return title.strip()

    def owns(self, product: str) -> bool:
        if self.level in (STYLE, FAMILY):
            return product in self.styles
        return match_key(product) == self.key

    def garments_of(self, product: str) -> Set[str]:
        style = next((s for s in self.catalog.styles if s.name == product), None)
        words = set(match_key(product).split())
        if style and style.product_class:
            words.update(match_key(style.product_class).split())
        return {w for w in words if w in GARMENT_NOUNS}


def _iso(value: object) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def _items_for(orders: Sequence[OrderRecord], target: ProductTarget) -> Dict[str, List[Tuple[str, str]]]:
    """Distinct products across orders → [(order_id, line_id)]."""
    items: Dict[str, List[Tuple[str, str]]] = {}
    for order in orders:
        for line in order.lines:
            items.setdefault(target.product_of_line(line.product_title), []).append((order.order_id, line.line_id))
    return items


def _pick_item(items: Dict[str, List[Tuple[str, str]]], target: ProductTarget,
               text: str) -> Tuple[str, Optional[str]]:
    """Decide which purchased product the message is about.

    Returns (outcome, product) where outcome is ``single``, ``item_match``,
    ``absent``, or ``ambiguous``.
    """
    products = list(items)
    owned = [p for p in products if target.owns(p)]
    if not owned:
        return "absent", None
    distinct_targets = {target.name} if target.level == FAMILY else set(owned)
    if len(products) == len(owned) and len(distinct_targets) == 1:
        return "single", owned[0]
    named = garments_in(text)
    if named:
        matching = [p for p in products if target.garments_of(p) & named]
        if matching and all(target.owns(p) for p in matching) and (
                target.level == FAMILY or len(matching) == 1):
            return "item_match", matching[0]
    return "ambiguous", None


class Attributor:
    """Attributes cleaned customer messages to one product target."""

    def __init__(
        self,
        target: ProductTarget,
        contexts: Mapping[str, ConversationContext],
        orders_by_number: Mapping[str, OrderRecord],
        history_by_email: Mapping[str, Sequence[OrderRecord]],
        order_data_available: bool,
        lookback_days: int = LOOKBACK_DAYS,
    ):
        self.target = target
        self.contexts = contexts
        self.orders_by_number = orders_by_number
        self.history_by_email = history_by_email
        self.order_data_available = order_data_available
        self.lookback = timedelta(days=lookback_days)
        self.lookback_days = lookback_days

    # -- helpers -------------------------------------------------------------

    def _mentions(self, text: str) -> Tuple[List[ProductMention], List[ProductMention]]:
        mentions = find_product_mentions(text, self.target.catalog)
        hits = [m for m in mentions if self.target.is_mention(m)]
        others = [m for m in mentions if self.target.is_other(m)]
        return hits, others

    def _thread_names(self, messages: Sequence[Mapping[str, object]]) -> Dict[str, Tuple[bool, bool]]:
        """Per conversation: (customer named this product, customer named another product)."""
        out: Dict[str, Tuple[bool, bool]] = {}
        for msg in messages:
            cid = str(msg.get("CONVERSATION_ID"))
            hits, others = self._mentions(str(msg.get("cleaned_body", "")))
            named, other = out.get(cid, (False, False))
            out[cid] = (named or bool(hits), other or bool(others))
        return out

    def _orders_for(self, refs: Sequence[str], ctx: ConversationContext) -> Tuple[List[OrderRecord], bool]:
        """Referenced orders that belong to this customer, and whether any did not."""
        email = verified_email(ctx.customer_email)
        mine: List[OrderRecord] = []
        mismatch = False
        for ref in refs:
            order = self.orders_by_number.get(ref)
            if order is None:
                continue
            order_email = verified_email(order.customer_email)
            if email and order_email and email != order_email:
                mismatch = True
                continue
            mine.append(order)
        return mine, mismatch

    def history_for(self, ctx: ConversationContext) -> Optional[List[OrderRecord]]:
        email = verified_email(ctx.customer_email)
        ticket = _aware(ctx.created_at)
        if not email or ticket is None:
            return None
        start = ticket - self.lookback
        out = []
        for order in self.history_by_email.get(email, []):
            placed = _aware(order.created_at)
            if placed is not None and start <= placed < ticket:
                out.append(order)
        return out

    def _make(self, msg: Mapping[str, object], ctx: ConversationContext, tier: str, method: str,
              basis: str, candidates: Iterable[str] = (), links: Sequence[Tuple[str, str]] = ()) -> ProductAttribution:
        return ProductAttribution(
            message_id=str(msg.get("MESSAGE_ID", "")),
            conversation_id=ctx.conversation_id,
            tier=tier,
            method=method,
            basis=basis,
            product=self.target.name if tier in (CONFIRMED, ORDER_LINKED, INFERRED) else None,
            candidates=tuple(dict.fromkeys(candidates)),
            order_ids=tuple(dict.fromkeys(o for o, _ in links)),
            order_line_ids=tuple(dict.fromkeys(line for _, line in links if line)),
            message_created_at=_iso(msg.get("CREATED_AT")),
            ticket_created_at=_iso(ctx.created_at),
            customer_identity_verified=verified_email(ctx.customer_email) is not None,
        )

    # -- main rule -----------------------------------------------------------

    def attribute(self, messages: Sequence[Mapping[str, object]]) -> List[ProductAttribution]:
        threads = self._thread_names(messages)
        out: List[ProductAttribution] = []
        for msg in messages:
            cid = str(msg.get("CONVERSATION_ID"))
            ctx = self.contexts.get(cid) or ConversationContext(conversation_id=cid)
            out.append(self._attribute_one(msg, ctx, threads.get(cid, (False, False))))
        return out

    def _attribute_one(self, msg: Mapping[str, object], ctx: ConversationContext,
                       thread: Tuple[bool, bool]) -> ProductAttribution:
        name = self.target.name
        text = str(msg.get("cleaned_body", ""))
        hits, others = self._mentions(text)
        refs = order_numbers_in(text) or order_numbers_in(ctx.subject)
        orders, mismatch = self._orders_for(refs, ctx) if (refs and self.order_data_available) else ([], False)
        items = _items_for(orders, self.target)
        outcome, _ = _pick_item(items, self.target, text) if orders else ("none", None)
        links = [link for p, ls in items.items() if self.target.owns(p) for link in ls]

        if hits and others:
            return self._make(msg, ctx, UNRESOLVED, "multiple_products_named",
                              "The message names this product and another one.",
                              candidates=[m.name for m in hits + others])
        if hits:
            if orders and outcome == "absent":
                return self._make(msg, ctx, UNRESOLVED, "conflicting_order_reference",
                                  f"The customer names {name}, but the order they reference does not contain it.",
                                  candidates=list(items))
            basis = f"The customer names {name}."
            if orders and outcome in ("single", "item_match"):
                basis += " The order they reference contains it."
            return self._make(msg, ctx, CONFIRMED, "customer_mention", basis, links=links)
        if others:
            return self._make(msg, ctx, OTHER_PRODUCT, "names_other_product",
                              "The message is about a different product.",
                              candidates=[m.name for m in others])

        named_here, named_other = thread
        if refs:
            if not self.order_data_available:
                if named_here and not named_other:
                    return self._make(msg, ctx, CONFIRMED, "same_thread_mention",
                                      f"The customer named {name} earlier in this ticket.")
                return self._make(msg, ctx, UNRESOLVED, "order_data_unavailable",
                                  "The message references an order, but order data is not connected.")
            if not orders:
                reason = ("order_identity_mismatch",
                          "The referenced order belongs to a different customer email.") if mismatch else (
                          "order_not_found", "The referenced order number was not found.")
                return self._make(msg, ctx, UNRESOLVED, reason[0], reason[1])
            if outcome == "absent":
                if named_here:
                    return self._make(msg, ctx, UNRESOLVED, "conflicting_order_reference",
                                      f"The ticket names {name}, but the referenced order does not contain it.",
                                      candidates=list(items))
                return self._make(msg, ctx, OTHER_PRODUCT, "order_without_product",
                                  "The referenced order does not contain this product.", candidates=list(items))
            if named_here and not named_other:
                return self._make(msg, ctx, CONFIRMED, "same_thread_mention",
                                  f"The customer named {name} earlier in this ticket, and the referenced order contains it.",
                                  links=links)
            if outcome == "single":
                return self._make(msg, ctx, ORDER_LINKED, "order_single_product",
                                  f"The referenced order contains only {name}.", links=links)
            if outcome == "item_match":
                return self._make(msg, ctx, ORDER_LINKED, "order_item_match",
                                  f"The referenced order has {len(items)} products; only {name} matches the item the customer describes.",
                                  links=links)
            return self._make(msg, ctx, UNRESOLVED, "multi_item_order",
                              f"The referenced order has {len(items)} products and the message does not say which one.",
                              candidates=list(items))

        if named_here and not named_other:
            return self._make(msg, ctx, CONFIRMED, "same_thread_mention",
                              f"The customer named {name} earlier in this ticket.")
        if named_here and named_other:
            return self._make(msg, ctx, UNRESOLVED, "thread_names_several_products",
                              "The customer named several products in this ticket.")

        subject_hits, subject_others = self._mentions(ctx.subject)
        if subject_hits and not subject_others and ctx.first_from_customer:
            return self._make(msg, ctx, CONFIRMED, "subject_reference",
                              f"The customer's subject line names {name}.")

        return self._infer(msg, ctx, text)

    def _infer(self, msg: Mapping[str, object], ctx: ConversationContext, text: str) -> ProductAttribution:
        name = self.target.name
        if not self.order_data_available:
            return self._make(msg, ctx, UNRESOLVED, "order_data_unavailable",
                              "The message does not name the product, and order data is not connected.")
        history = self.history_for(ctx)
        if history is None:
            return self._make(msg, ctx, UNRESOLVED, "customer_identity_unverified",
                              "The message does not name the product, and the customer could not be matched to orders.")
        items = _items_for(history, self.target)
        outcome, _ = _pick_item(items, self.target, text)
        links = [link for p, ls in items.items() if self.target.owns(p) for link in ls]
        window = f"the {self.lookback_days} days before the ticket"
        if outcome == "single":
            return self._make(msg, ctx, INFERRED, "purchase_history_single_product",
                              f"{name} is the only product in the customer's orders from {window}.",
                              links=links)
        if outcome == "item_match":
            return self._make(msg, ctx, INFERRED, "purchase_history_item_match",
                              f"Of {len(items)} products in the customer's orders from {window}, only {name} matches the item they describe.",
                              links=links)
        if outcome == "ambiguous":
            return self._make(msg, ctx, UNRESOLVED, "several_recent_products",
                              f"The customer bought {len(items)} products in {window}; the message does not say which one.",
                              candidates=list(items))
        return self._make(msg, ctx, UNRESOLVED, "no_matching_purchase",
                          f"No order for {name} from this customer in {window}.")


def summarise(attributions: Sequence[ProductAttribution]) -> Dict[str, object]:
    """Counts by tier and method, for the payload. Messages and distinct tickets."""
    tiers: Dict[str, Dict[str, int]] = {}
    methods: Dict[str, int] = {}
    for a in attributions:
        slot = tiers.setdefault(a.tier, {"messages": 0, "tickets": 0})
        slot["messages"] += 1
        methods[a.method] = methods.get(a.method, 0) + 1
    for tier in tiers:
        tiers[tier]["tickets"] = len({a.conversation_id for a in attributions if a.tier == tier})
    return {"tiers": tiers, "methods": methods}
