"""Redo fields Scout needs, and the columns that hold them.

The Redo sync is a schema of tables, not one table named RETURN_REDO. This
module is the only place Scout names a Redo column. A field stays unverified,
with ``column is None``, when the profile did not find one column for it.
``outcome`` is derived from ``REFUND_TYPE`` and ``EXCHANGE_ITEM_QUANTITY``.
``settled_at`` has no column. The schema name is recorded in the returns
discovery note, not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from scout.returns.sources import REDO_RETURNS

@dataclass(frozen=True)
class RedoField:
    name: str
    meaning: str
    column: str | None = None
    relation: str | None = None
    verified: bool = False
    predicate: str | None = None

    @property
    def usable(self) -> bool:
        return self.verified and self.column is not None


_FIELDS = (
    RedoField(
        "return_id",
        "Redo return; one return can hold several items",
        column="ID",
        relation="RETURN",
        verified=True,
    ),
    RedoField(
        "return_item_id",
        "One returned item within a return",
        column="ID",
        relation="RETURN_ITEM",
        verified=True,
    ),
    RedoField(
        "shopify_order_id",
        "Original Shopify order. ORDER_EXTERNAL_ID equals RETURN_SHOPIFY_ORDER_ID.SHOPIFY_ORDER_ID on every return",
        column="ORDER_EXTERNAL_ID",
        relation="RETURN",
        verified=True,
    ),
    RedoField(
        "shopify_line_item_id",
        "Original Shopify order line on the item. Populated on every item; not joined to the dbt order lines",
        column="ORDER_ITEM_LINE_ITEM_ID",
        relation="RETURN_ITEM",
        verified=True,
    ),
    RedoField(
        "sku",
        "SKU of the returned item",
        column="SKU",
        relation="RETURN_ITEM",
        verified=True,
    ),
    RedoField(
        "quantity",
        "Units on the returned item. Every profiled row has quantity 1",
        column="QUANTITY",
        relation="RETURN_ITEM",
        verified=True,
    ),
    RedoField(
        "status",
        "Return lifecycle on RETURN.STATUS: complete, rejected, open, deleted, delivered, in_transit, flagged, needs_review. Item status is RETURN_ITEM.STATUS",
        column="STATUS",
        relation="RETURN",
        verified=True,
    ),
    RedoField(
        "outcome",
        "Settlement per item. Not one column: REFUND_TYPE is refund, store_credit, or none, and a null refund type with EXCHANGE_ITEM_QUANTITY = 1 is an exchange",
    ),
    RedoField(
        "exchange_order_id",
        "Replacement Shopify order Redo created for an exchange. Filled on 3322 of 3678 returns that have an exchange item",
        column="EXCHANGE_ORDER_EXTERNAL_ID",
        relation="RETURN",
        verified=True,
    ),
    RedoField(
        "primary_reason",
        "Structured primary reason the customer chose. INDEX = 0. Five parent values cover nearly all rows",
        column="REASON",
        relation="RETURN_ITEM_REASON",
        verified=True,
        predicate="INDEX = 0",
    ),
    RedoField(
        "secondary_reason",
        "Structured follow-up reason the customer chose. INDEX = 1",
        column="REASON",
        relation="RETURN_ITEM_REASON",
        verified=True,
        predicate="INDEX = 1",
    ),
    RedoField(
        "customer_comment",
        "The customer's own free-text comment on the item",
        column="CUSTOMER_COMMENT",
        relation="RETURN_ITEM",
        verified=True,
    ),
    RedoField(
        "internal_note",
        "Staff or operational note. One return can have several notes",
        column="MESSAGE",
        relation="RETURN_NOTE",
        verified=True,
    ),
    RedoField(
        "requested_at",
        "When the customer started the return (RETURN.CREATED_AT)",
        column="CREATED_AT",
        relation="RETURN",
        verified=True,
    ),
    RedoField(
        "received_at",
        "When the return shipment was delivered",
        column="SHIPMENT_DELIVERED_AT",
        relation="RETURN",
        verified=True,
    ),
    RedoField(
        "settled_at",
        "When the outcome was issued. No column holds this timestamp",
    ),
)

REDO_FIELDS: dict[str, RedoField] = {f.name: f for f in _FIELDS}

REASON_FIELDS = ("return_id", "return_item_id", "quantity", "primary_reason", "secondary_reason")
COMMENT_FIELDS = ("return_id", "return_item_id", "sku", "customer_comment")


def unverified_reason(missing: Iterable[str]) -> str:
    return f"Redo fields not yet verified in `{REDO_RETURNS.relation}`: {', '.join(missing)}"


def missing_fields(names: Iterable[str], fields: dict[str, RedoField] | None = None) -> list[str]:
    """Logical fields in *names* that are not yet verified to a column."""
    table = REDO_FIELDS if fields is None else fields
    out = []
    for name in names:
        field = table.get(name)
        if field is None:
            raise KeyError(f"Unknown Redo field: {name}")
        if not field.usable:
            out.append(name)
    return out
