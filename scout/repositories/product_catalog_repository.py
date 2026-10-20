"""
Product catalog repository.

Reads distinct styles and their family, category, class, subclass, season,
and lifecycle from Snowflake. One bounded, read-only query; the service
layer caches the result.

``CATALOG_SOURCE`` is ``JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE``.
It is the table Yankho named as the product-context enrichment source, and
its ``STYLE_NAME``, ``FRANCHISE``, ``CATEGORY``, ``CLASS``, ``SUBCLASS``,
``SEASON``, and ``LIFECYCLE`` columns are what product resolution needs. It
holds products with delivered orders, so a style with no deliveries yet is
not in it. If Scout's Snowflake role cannot read it, the catalog service
falls back to the explicit alias list and says so.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from scout.integrations.snowflake import execute_query_dict

logger = logging.getLogger("scout.repository.product_catalog")

CATALOG_SOURCE = "JA_ANALYTICS.REPORTING.SHOPIFY_AGG_POST_PURCHASE"

MAX_CATALOG_ROWS = 5000


def build_catalog_sql(max_rows: int = MAX_CATALOG_ROWS) -> str:
    return f"""
    SELECT
        STYLE_NAME,
        FRANCHISE,
        CATEGORY,
        CLASS,
        SUBCLASS,
        LISTAGG(DISTINCT SEASON, '|') WITHIN GROUP (ORDER BY SEASON) AS SEASONS,
        LISTAGG(DISTINCT LIFECYCLE, '|') WITHIN GROUP (ORDER BY LIFECYCLE) AS LIFECYCLES
    FROM {CATALOG_SOURCE}
    WHERE STYLE_NAME IS NOT NULL
    GROUP BY STYLE_NAME, FRANCHISE, CATEGORY, CLASS, SUBCLASS
    ORDER BY STYLE_NAME
    LIMIT {int(max_rows)}
    """


def fetch_catalog_rows(max_rows: int = MAX_CATALOG_ROWS) -> List[Dict[str, Any]]:
    """Distinct style rows from the catalog source. May raise on access errors."""
    rows = execute_query_dict(build_catalog_sql(max_rows))
    logger.info("Product catalog rows: %d (source=%s)", len(rows), CATALOG_SOURCE)
    if len(rows) >= max_rows:
        logger.warning("Product catalog hit the %d-row cap; some styles may be missing", max_rows)
    return rows
