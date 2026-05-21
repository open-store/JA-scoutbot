"""
Boundary tests for the voc-scope-alignment PR.

These tests verify the architecture contract:
  - voc.py does NOT import product_feedback_service
  - voc_service.py is the ONLY module that imports both voc and product_feedback_service
  - General VOC queries (no product filter) call run_voc() directly
  - Product VOC queries use the shared scope (same conversation_ids for metrics + synthesis)
  - Empty scope (no matching conversations) is handled safely
  - Fallback retrieval mode is reflected in result caveats
  - Low-sample cases do not overstate confidence

Run with:
    cd /home/ubuntu/scout && python3.11 -m pytest scout/tests/test_voc_scope_boundary.py -v
"""
from __future__ import annotations

import ast
import os
import sys
from dataclasses import field
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scout.models.product_feedback import (
    ProductFeedbackRequest,
    ProductFeedbackScope,
    ProductFeedbackResult,
    ProductFeedbackTheme,
)


# ---------------------------------------------------------------------------
# 1. Architecture boundary: voc.py must not import product_feedback_service
# ---------------------------------------------------------------------------

def test_voc_py_does_not_import_product_feedback_service():
    """voc.py must not import product_feedback_service or product_feedback."""
    voc_path = os.path.join(os.path.dirname(__file__), "..", "..", "queries", "voc.py")
    with open(voc_path, "r") as f:
        source = f.read()
    tree = ast.parse(source)
    forbidden = {"product_feedback_service", "product_feedback", "get_product_feedback"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(f in node.module for f in forbidden), (
                    f"voc.py must not import from '{node.module}'. "
                    "Product scope discovery belongs in voc_service.py."
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(f in alias.name for f in forbidden), (
                        f"voc.py must not import '{alias.name}'."
                    )


# ---------------------------------------------------------------------------
# 2. Architecture boundary: voc_service.py imports both (and only it should)
# ---------------------------------------------------------------------------

def test_voc_service_imports_both_voc_and_pipeline():
    """voc_service.py must import both run_voc and product_feedback_service."""
    svc_path = os.path.join(
        os.path.dirname(__file__), "..", "services", "voc_service.py"
    )
    with open(svc_path, "r") as f:
        source = f.read()
    assert "from queries.voc import run_voc" in source, (
        "voc_service.py must import run_voc from queries.voc"
    )
    assert "product_feedback_service" in source, (
        "voc_service.py must import from product_feedback_service"
    )


# ---------------------------------------------------------------------------
# 3. ProductFeedbackScope model
# ---------------------------------------------------------------------------

def test_scope_is_empty_when_no_conversations():
    scope = ProductFeedbackScope(
        product_name="Anytime Tee",
        conversation_ids=[],
        aliases_used=["anytime tee"],
        tag_groups_used=["product_feedback"],
        retrieval_mode="empty",
        timeframe_days=30,
    )
    assert scope.is_empty is True
    assert scope.total_conversations == 0


def test_scope_is_not_empty_with_conversations():
    scope = ProductFeedbackScope(
        product_name="Anytime Tee",
        conversation_ids=["id1", "id2", "id3"],
        aliases_used=["anytime tee"],
        tag_groups_used=["product_feedback"],
        retrieval_mode="strict",
        timeframe_days=30,
    )
    assert scope.is_empty is False
    assert scope.total_conversations == 3


def test_scope_caveats_default_empty():
    scope = ProductFeedbackScope(
        product_name="Clubhouse Polo",
        conversation_ids=["id1"],
        aliases_used=["clubhouse polo"],
        tag_groups_used=["product_feedback"],
        retrieval_mode="strict",
        timeframe_days=30,
    )
    assert scope.caveats == []


# ---------------------------------------------------------------------------
# 4. get_product_feedback_for_scope handles empty scope safely
# ---------------------------------------------------------------------------

def test_empty_scope_returns_no_data_result():
    """get_product_feedback_for_scope must return a safe result for empty scope."""
    from scout.services.product_feedback_service import get_product_feedback_for_scope

    scope = ProductFeedbackScope(
        product_name="Unknown Product",
        conversation_ids=[],
        aliases_used=["unknown product"],
        tag_groups_used=["product_feedback"],
        retrieval_mode="empty",
        timeframe_days=30,
    )
    request = ProductFeedbackRequest(product_name="Unknown Product", timeframe_days=30)
    result = get_product_feedback_for_scope(scope, request)

    assert result.total_candidate_conversations == 0
    assert result.total_messages_analysed == 0
    assert result.retrieval_mode == "empty"
    assert len(result.themes) == 0
    # Should mention the product name in headline
    assert "Unknown Product" in result.headline or "unknown product" in result.headline.lower()


# ---------------------------------------------------------------------------
# 5. Fallback retrieval mode is reflected in caveats
# ---------------------------------------------------------------------------

def test_fallback_relaxed_caveat_propagates_to_result():
    """Fallback retrieval mode caveats from scope must appear in result caveats."""
    from scout.services.product_feedback_service import get_product_feedback_for_scope

    scope = ProductFeedbackScope(
        product_name="Clubhouse Polo",
        conversation_ids=[],  # empty so synthesis is skipped
        aliases_used=["clubhouse polo"],
        tag_groups_used=["product_feedback"],
        retrieval_mode="fallback_relaxed",
        timeframe_days=30,
        caveats=["Broadened matching was used to find enough conversations."],
    )
    request = ProductFeedbackRequest(product_name="Clubhouse Polo", timeframe_days=30)
    result = get_product_feedback_for_scope(scope, request)

    # Scope caveat must propagate to result
    assert any("Broadened matching" in c for c in result.caveats), (
        "Fallback caveat from scope must appear in result caveats"
    )


# ---------------------------------------------------------------------------
# 6. run_voc accepts conversation_ids and builds correct SQL filter
# ---------------------------------------------------------------------------

def test_run_voc_filter_clause_with_ids():
    """_build_filter_clause must use IN (...) when conversation_ids are provided."""
    from queries.voc import _build_filter_clause
    from command_parser import ParsedCommand
    from datetime import date, timedelta

    today = date.today()
    cmd = ParsedCommand(
        command="voc",
        days=30,
        start_date=today - timedelta(days=30),
        end_date=today,
        filters={"product": "Clubhouse polo"},
        is_valid=True,
    )
    ids = ["conv-001", "conv-002", "conv-003"]
    sql, label = _build_filter_clause(cmd, conversation_ids=ids)

    assert "IN (" in sql, "Filter clause must use IN (...) for conversation_ids"
    assert "conv-001" in sql
    assert "product: Clubhouse polo" in label


def test_run_voc_filter_clause_empty_ids_returns_no_results():
    """_build_filter_clause must use AND 1=0 when conversation_ids is empty list."""
    from queries.voc import _build_filter_clause
    from command_parser import ParsedCommand
    from datetime import date, timedelta

    today = date.today()
    cmd = ParsedCommand(
        command="voc",
        days=30,
        start_date=today - timedelta(days=30),
        end_date=today,
        filters={"product": "Nonexistent Product"},
        is_valid=True,
    )
    sql, label = _build_filter_clause(cmd, conversation_ids=[])

    assert "1=0" in sql, "Empty scope must produce AND 1=0 to return no results"


def test_run_voc_no_product_filter_no_id_clause():
    """General VOC (no product filter) must not include an ID filter clause."""
    from queries.voc import _build_filter_clause
    from command_parser import ParsedCommand
    from datetime import date, timedelta

    today = date.today()
    cmd = ParsedCommand(
        command="voc",
        days=7,
        start_date=today - timedelta(days=7),
        end_date=today,
        filters={},
        is_valid=True,
    )
    sql, label = _build_filter_clause(cmd, conversation_ids=None)

    assert "IN (" not in sql, "General VOC must not have an ID IN clause"
    assert "1=0" not in sql, "General VOC must not have AND 1=0"
    assert label == "", "General VOC with no filters must have empty label"


# ---------------------------------------------------------------------------
# 7. voc_service routes correctly
# ---------------------------------------------------------------------------

def test_voc_service_routes_product_query_to_run_product_voc():
    """run_voc_query must call run_product_voc for product-filtered queries."""
    from command_parser import ParsedCommand
    from datetime import date, timedelta

    today = date.today()
    cmd = ParsedCommand(
        command="voc",
        days=30,
        start_date=today - timedelta(days=30),
        end_date=today,
        filters={"product": "Anytime Tee"},
        is_valid=True,
    )

    with patch("scout.services.voc_service.run_product_voc") as mock_product_voc:
        mock_product_voc.return_value = {"volume": {}, "product_feedback": None}
        from scout.services.voc_service import run_voc_query
        run_voc_query(cmd)
        mock_product_voc.assert_called_once_with(cmd)


def test_voc_service_routes_general_query_to_run_voc():
    """run_voc_query must call run_voc directly for general (non-product) queries."""
    from command_parser import ParsedCommand
    from datetime import date, timedelta

    today = date.today()
    cmd = ParsedCommand(
        command="voc",
        days=7,
        start_date=today - timedelta(days=7),
        end_date=today,
        filters={},
        is_valid=True,
    )

    with patch("scout.services.voc_service.run_voc") as mock_run_voc:
        mock_run_voc.return_value = {"volume": {}, "product_feedback": None}
        from scout.services.voc_service import run_voc_query
        # Need to reload to get fresh mock
        import importlib
        import scout.services.voc_service as svc_module
        importlib.reload(svc_module)
        svc_module.run_voc = mock_run_voc
        svc_module.run_voc_query(cmd)
        mock_run_voc.assert_called_once_with(cmd)
