"""
Product catalog service.

``get_catalog()`` returns the cached ``ProductCatalog``. The catalog is read
from Snowflake at most once per ``CATALOG_TTL_SECONDS``. A failed read
returns the explicit alias list (``available=False``) and is retried after
``FAILURE_TTL_SECONDS`` so a Snowflake outage does not slow every question.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from scout.repositories.product_catalog_repository import CATALOG_SOURCE, fetch_catalog_rows
from scout.taxonomy.products import ProductCatalog, alias_only_catalog, catalog_from_rows

logger = logging.getLogger("scout.services.product_catalog")

CATALOG_TTL_SECONDS = 6 * 60 * 60
FAILURE_TTL_SECONDS = 5 * 60

_lock = threading.Lock()
_cached: Optional[ProductCatalog] = None
_expires_at = 0.0


def _load() -> ProductCatalog:
    try:
        rows = fetch_catalog_rows()
    except Exception as e:  # access, network, or missing-credential errors
        logger.warning("Product catalog unavailable from %s: %s", CATALOG_SOURCE, type(e).__name__)
        return alias_only_catalog(error=f"{type(e).__name__} reading {CATALOG_SOURCE}")
    catalog = catalog_from_rows(rows, source=CATALOG_SOURCE)
    if not any(s.in_catalog for s in catalog.styles):
        logger.warning("Product catalog query returned no styles from %s", CATALOG_SOURCE)
        return alias_only_catalog(error=f"No styles returned from {CATALOG_SOURCE}")
    return catalog


def get_catalog(force_refresh: bool = False) -> ProductCatalog:
    global _cached, _expires_at
    with _lock:
        now = time.monotonic()
        if _cached is not None and not force_refresh and now < _expires_at:
            return _cached
        catalog = _load()
        ttl = CATALOG_TTL_SECONDS if catalog.available else FAILURE_TTL_SECONDS
        _cached, _expires_at = catalog, now + ttl
        return catalog


def reset_cache() -> None:
    global _cached, _expires_at
    with _lock:
        _cached, _expires_at = None, 0.0
