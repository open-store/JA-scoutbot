"""Returns data sources and how far each one is verified.

Redo owns return records and outcomes. Shopify order lines own the
delivered-unit denominator. ``SHOPIFY_AGG_POST_PURCHASE`` only adds context
and comparison values. A source is usable for an authoritative metric only
when its relation is verified against Snowflake metadata or production code.
See ``docs/architecture/returns-data.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceStatus(str, Enum):
    VERIFIED = "verified"
    NAMED_UNVERIFIED = "named_unverified"
    UNKNOWN = "unknown"


class SourceRole(str, Enum):
    AUTHORITATIVE_RETURNS = "authoritative_returns"
    ORDER_DENOMINATOR = "order_denominator"
    ENRICHMENT = "enrichment"
    REFUND_EXPORT = "refund_export"


@dataclass(frozen=True)
class ReturnsSource:
    role: SourceRole
    label: str
    relation: str | None
    status: SourceStatus
    evidence: str

    @property
    def usable(self) -> bool:
        return self.relation is not None and self.status is SourceStatus.VERIFIED


REFUND_EXPORT = ReturnsSource(
    role=SourceRole.REFUND_EXPORT,
    label="Shopify refund export",
    relation="ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS",
    status=SourceStatus.VERIFIED,
    evidence=(
        "Queried by /returns in production. One row per Shopify refund line, "
        "dated by refund date. Includes cancellations and non-Redo refunds; "
        "open returns are absent."
    ),
)

REDO_RETURNS = ReturnsSource(
    role=SourceRole.AUTHORITATIVE_RETURNS,
    label="Redo returns",
    relation="ANALYTICS.RETURN_REDO",
    status=SourceStatus.VERIFIED,
    evidence=(
        "Schema, not a table named RETURN_REDO. Read on 2026-10-27: 48 base "
        "tables. Return grain is RETURN (15,796 rows) and item grain is "
        "RETURN_ITEM (26,205 rows). Columns are scout/returns/redo_fields.py. "
        "Item outcome is not one column, and no column is a settled-at timestamp."
    ),
)

SHOPIFY_ORDER_LINES = ReturnsSource(
    role=SourceRole.ORDER_DENOMINATOR,
    label="Shopify order lines",
    relation="JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES",
    status=SourceStatus.NAMED_UNVERIFIED,
    evidence=(
        "Named from the Rocky dashboard, which reads is_gwp_line and "
        "variant_title_imd. On 2026-10-27 Scout's role got Snowflake errno "
        "2003 on this relation, and that database has no DBT schema. "
        "A selectable SHOPIFY.FCT_SHOPIFY__ORDER_LINES copy has no is_gwp_line, "
        "variant_title_imd, or delivery date."
    ),
)

SHOPIFY_ORDERS = ReturnsSource(
    role=SourceRole.ORDER_DENOMINATOR,
    label="Shopify orders",
    relation="JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS",
    status=SourceStatus.NAMED_UNVERIFIED,
    evidence=(
        "Named from the Rocky dashboard, which reads is_dtc_demand_eligible, "
        "is_reportable_sales_order, and is_test_order. On 2026-10-27 Scout's "
        "role got Snowflake errno 2003 on this relation. "
        "A selectable SHOPIFY.FCT_SHOPIFY__ORDERS copy does not have those three flags."
    ),
)

POST_PURCHASE_ENRICHMENT = ReturnsSource(
    role=SourceRole.ENRICHMENT,
    label="Shopify post-purchase aggregate",
    relation="JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE",
    status=SourceStatus.VERIFIED,
    evidence=(
        "Base table read on 2026-10-27: 588,759 rows, last altered that day, "
        "columns match the named field list. IS_COHORT_MATURE is not a single "
        "day cutoff; immature rows exist on and before the latest mature "
        "cohort date (2026-09-27). RETURN_RATE_PCT is a fraction, maximum 1. "
        "The cohort-channel-status-SKU grain has 12 duplicate keys."
    ),
)

ALL_SOURCES: tuple[ReturnsSource, ...] = (
    REDO_RETURNS,
    SHOPIFY_ORDER_LINES,
    SHOPIFY_ORDERS,
    POST_PURCHASE_ENRICHMENT,
    REFUND_EXPORT,
)
