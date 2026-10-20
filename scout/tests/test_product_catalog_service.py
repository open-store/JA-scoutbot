"""Catalog loading, caching, and fallback. Snowflake is mocked."""
from unittest.mock import patch

import pytest

from scout.repositories import product_catalog_repository as repo
from scout.services import product_catalog_service as svc
from scout.tests.catalog_fixture import CATALOG_ROWS


@pytest.fixture(autouse=True)
def _fresh_cache():
    svc.reset_cache()
    yield
    svc.reset_cache()


def test_catalog_sql_is_bounded_and_read_only():
    sql = repo.build_catalog_sql(1234)
    upper = sql.upper()
    assert upper.strip().startswith("SELECT")
    assert "LIMIT 1234" in upper
    assert repo.CATALOG_SOURCE in sql
    for keyword in ("INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "DROP"):
        assert keyword not in upper


def test_catalog_loads_once_and_is_cached():
    with patch.object(svc, "fetch_catalog_rows", return_value=CATALOG_ROWS) as fetch:
        first = svc.get_catalog()
        second = svc.get_catalog()
    assert fetch.call_count == 1
    assert first is second
    assert first.available is True
    assert first.source == repo.CATALOG_SOURCE
    assert any(s.name == "Atlas Short" for s in first.styles)


def test_catalog_failure_falls_back_to_alias_list_and_retries_later():
    with patch.object(svc, "fetch_catalog_rows", side_effect=KeyError("SNOWFLAKE_RSA_KEY_PATH")) as fetch:
        catalog = svc.get_catalog()
        svc.get_catalog()
    assert fetch.call_count == 1
    assert catalog.available is False
    assert "KeyError" in (catalog.error or "")
    assert any(s.name == "Clubhouse Polo" for s in catalog.styles)
    assert svc.FAILURE_TTL_SECONDS < svc.CATALOG_TTL_SECONDS


def test_empty_catalog_result_is_treated_as_unavailable():
    with patch.object(svc, "fetch_catalog_rows", return_value=[]):
        catalog = svc.get_catalog()
    assert catalog.available is False
    assert "No styles" in (catalog.error or "")
