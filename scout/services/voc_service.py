"""
VOC Service — coordinator for product-specific VOC queries.

This module owns the orchestration for product-filtered VOC requests.
It is the ONLY place where product-feedback scope discovery and VOC
aggregation are combined.

Responsibilities:
  - Accept a ParsedCommand with a product filter
  - Resolve the product against the catalog (style, family, or ask)
  - Call get_candidate_scope() for the current window, and separately for
    the previous window, with the same product, filters, and retrieval pass
  - Pass each period's IDs into run_voc() for aggregate metrics
  - Call get_product_feedback_for_scope() for message synthesis
  - Return a merged result dict ready for formatting

General (non-product) VOC queries bypass this coordinator entirely and
call run_voc() directly, preserving existing behaviour.

Architecture contract:
  - voc.py does NOT import or call product_feedback_service
  - product_feedback_service does NOT import voc.py
  - This module is the only place that imports both
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Dict, Optional

from scout.routing.command_parser import BUSINESS_TZ, ParsedCommand
from queries.voc import run_voc
from scout.models.product_feedback import (
    ProductFeedbackRequest,
    ProductFeedbackResult,
    ProductFeedbackScope,
)
from scout.services.product_catalog_service import get_catalog
from scout.services.product_feedback_service import (
    get_candidate_scope,
    get_product_feedback_for_scope,
    retrieval_ceiling_for,
)
from scout.taxonomy.products import ProductCatalog, ProductResolution, resolve_product

logger = logging.getLogger("scout.services.voc_service")

SCOPE_FILTER_KEYS = ("channel", "tag", "agent")


def run_product_voc(cmd: ParsedCommand, catalog: Optional[ProductCatalog] = None) -> dict:
    """Execute a product-filtered VOC query using the shared scope pattern.

    Flow:
      1. Resolve the product. Ambiguous or unknown names stop here with a
         disambiguation payload; nothing broader is queried.
      2. Discover the current-period scope, then a previous-period scope with
         the same product, filters, and widest retrieval pass
      3. run_voc() aggregates each period over its own conversation IDs
      4. Fetch + synthesise product feedback using the current scope
      5. Merge and return a single result dict

    The returned dict has the same shape as run_voc() output, with an additional
    'product_feedback' key built by product_feedback_payload().
    """
    product = cmd.filters.get("product") if cmd.filters else None
    if not product:
        raise ValueError("run_product_voc called without a product filter")

    catalog = catalog if catalog is not None else get_catalog()
    resolution = resolve_product(product, catalog)
    logger.info(
        "Product '%s' resolved: status=%s level=%s name=%s method=%s",
        product, resolution.status, resolution.level, resolution.canonical_name, resolution.match_method,
    )
    if not resolution.is_resolved:
        return _unresolved_result(cmd, resolution)
    canonical = resolution.canonical_name or product

    # Step 2 — Scope discovery for both periods
    request = ProductFeedbackRequest(
        product_name=canonical,
        timeframe_days=cmd.days,
        start=cmd.start_date,
        end=cmd.end_date,
        timezone=BUSINESS_TZ.key,
        filters={k: str(cmd.filters[k]) for k in SCOPE_FILTER_KEYS if cmd.filters.get(k)},
        resolution=resolution,
        topic=cmd.filters.get("topic") or None,
    )
    scope: ProductFeedbackScope = get_candidate_scope(request)
    previous_scope: ProductFeedbackScope = get_candidate_scope(replace(
        request,
        start=cmd.previous_start_date,
        end=cmd.previous_end_date,
        period="previous",
        retrieval_ceiling=retrieval_ceiling_for(scope),
    ))
    logger.info(
        "Scopes resolved: current=%d (mode=%s), previous=%d (mode=%s)",
        scope.total_conversations, scope.retrieval_mode,
        previous_scope.total_conversations, previous_scope.retrieval_mode,
    )

    # Step 3 — VOC aggregation; each period uses its own IDs.
    # run_voc() receives the IDs externally; it does NOT call the pipeline itself.
    scoped_cmd = replace(cmd, filters={**cmd.filters, "product": canonical})
    voc_result = run_voc(
        scoped_cmd,
        conversation_ids=scope.conversation_ids,
        previous_conversation_ids=previous_scope.conversation_ids,
    )

    # Step 4 — Synthesis (uses the same scope, no re-discovery)
    pf_result = get_product_feedback_for_scope(scope, request)

    # Step 5 — Merge
    voc_result["product_feedback"] = product_feedback_payload(pf_result, scope, previous_scope)
    return voc_result


def product_feedback_payload(
    result: ProductFeedbackResult,
    scope: ProductFeedbackScope,
    previous_scope: Optional[ProductFeedbackScope] = None,
) -> Dict[str, Any]:
    """The ``product_feedback`` dict that ``format_voc`` reads.

    ``sample_count`` is the number of customer messages sent to synthesis.
    """
    summary = scope.summary()
    return {
        "status": "ok",
        "product_name": result.product_name,
        "product_level": summary["product_level"],
        "family": summary["family"],
        "styles": summary["styles"],
        "match_method": summary["match_method"],
        "catalog_source": summary["catalog_source"],
        "catalog_available": summary["catalog_available"],
        "seasons": summary["seasons"],
        "timeframe_label": result.timeframe_label,
        "timezone": scope.timezone,
        "filters": summary["filters"],
        "topic": scope.topic,
        "total_conversations": result.total_candidate_conversations,
        "previous_total_conversations": previous_scope.total_conversations if previous_scope else None,
        "retrieved_message_count": result.retrieved_message_count,
        "cleaned_message_count": result.cleaned_message_count,
        "sample_count": result.total_messages_analysed,
        "low_sample": result.low_sample,
        "retrieval_mode": result.retrieval_mode,
        "retrieval_capped": result.retrieval_capped,
        "headline": result.headline,
        "themes": [
            {
                "label": t.label,
                "mention_count": t.mention_count,
                "representative_quote": t.representative_quote,
            }
            for t in result.themes
        ],
        "so_what": result.so_what,
        "recommended_action": result.recommended_action,
        "caveats": result.caveats,
        "source": result.source,
        "candidate_conversation_ids": result.candidate_conversation_ids,
        "evidence_message_ids": result.analysed_message_ids,
        "scope": summary,
        "previous_scope": previous_scope.summary() if previous_scope else None,
    }


def _unresolved_result(cmd: ParsedCommand, resolution: ProductResolution) -> dict:
    """Result for an ambiguous or unknown product. No ticket data is queried."""
    return {
        "volume": {},
        "prev_volume": {},
        "by_channel": [],
        "tags": [],
        "prev_tags": [],
        "status": [],
        "timeframe_label": cmd.timeframe_label,
        "days": cmd.days,
        "filter_label": f"product: {resolution.query}",
        "product_feedback": {
            "status": resolution.status,
            "query": resolution.query,
            "candidates": list(resolution.candidates),
            "family": resolution.family,
            "catalog_source": resolution.catalog_source,
            "catalog_available": resolution.catalog_available,
            "topic": cmd.filters.get("topic") or None,
        },
    }


def run_voc_query(cmd: ParsedCommand) -> dict:
    """Entry point for all VOC queries.

    Routes to run_product_voc() when a product filter is present,
    otherwise calls run_voc() directly (general VOC, unchanged behaviour).
    """
    product = cmd.filters.get("product") if cmd.filters else None
    if product:
        return run_product_voc(cmd)
    else:
        return run_voc(cmd)
