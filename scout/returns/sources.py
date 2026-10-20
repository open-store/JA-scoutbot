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
    status=SourceStatus.NAMED_UNVERIFIED,
    evidence=(
        "Authoritative for return records and outcomes, named by the business "
        "owner. Two-part name: a RETURN_REDO schema in ANALYTICS or a "
        "RETURN_REDO table. Level, casing, grain, and columns have not been "
        "read from Snowflake metadata."
    ),
)

SHOPIFY_ORDER_LINES = ReturnsSource(
    role=SourceRole.ORDER_DENOMINATOR,
    label="Shopify order lines",
    relation="JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDER_LINES",
    status=SourceStatus.NAMED_UNVERIFIED,
    evidence=(
        "Read by the Rocky dashboard under its own service role, with "
        "is_gwp_line and variant_title_imd. Scout's role access, delivery "
        "dates, and unit columns are unverified. `OS_ALL_ORDERS` was only "
        "shorthand."
    ),
)

SHOPIFY_ORDERS = ReturnsSource(
    role=SourceRole.ORDER_DENOMINATOR,
    label="Shopify orders",
    relation="JA_ANALYTICS.DBT.FCT_SHOPIFY__ORDERS",
    status=SourceStatus.NAMED_UNVERIFIED,
    evidence=(
        "Read by the Rocky dashboard with is_dtc_demand_eligible, "
        "is_reportable_sales_order, and is_test_order. Scout's role access "
        "and the sales-channel columns are unverified."
    ),
)

POST_PURCHASE_ENRICHMENT = ReturnsSource(
    role=SourceRole.ENRICHMENT,
    label="Shopify post-purchase aggregate",
    relation="JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE",
    status=SourceStatus.NAMED_UNVERIFIED,
    evidence=(
        "Named by the business owner with a field list. Schema, grain, "
        "maturity rule, and refresh have not been read from Snowflake."
    ),
)

ALL_SOURCES: tuple[ReturnsSource, ...] = (
    REDO_RETURNS,
    SHOPIFY_ORDER_LINES,
    SHOPIFY_ORDERS,
    POST_PURCHASE_ENRICHMENT,
    REFUND_EXPORT,
)
