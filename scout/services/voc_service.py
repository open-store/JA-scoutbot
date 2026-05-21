"""
VOC Service — coordinator for product-specific VOC queries.

This module owns the orchestration for product-filtered VOC requests.
It is the ONLY place where product-feedback scope discovery and VOC
aggregation are combined.

Responsibilities:
  - Accept a ParsedCommand with a product filter
  - Call get_candidate_scope() to discover the authoritative conversation IDs
  - Pass those IDs into run_voc() for aggregate metrics
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
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from command_parser import ParsedCommand
from queries.voc import run_voc
from scout.models.product_feedback import ProductFeedbackRequest, ProductFeedbackScope
from scout.services.product_feedback_service import (
    get_candidate_scope,
    get_product_feedback_for_scope,
)

logger = logging.getLogger("scout.services.voc_service")


def run_product_voc(cmd: ParsedCommand) -> dict:
    """Execute a product-filtered VOC query using the shared scope pattern.

    Flow:
      1. Discover candidate scope (conversation IDs) via product-feedback pipeline
      2. Pass conversation_ids into run_voc() for aggregate metrics
      3. Fetch + synthesise product feedback using the same scope
      4. Merge and return a single result dict

    The returned dict has the same shape as run_voc() output, with an additional
    'product_feedback' key containing the ProductFeedbackResult as a dict.
    """
    product = cmd.filters.get("product") if cmd.filters else None
    if not product:
        raise ValueError("run_product_voc called without a product filter")

    # Step 1 — Scope discovery
    request = ProductFeedbackRequest(
        product_name=product,
        timeframe_days=cmd.days,
    )
    logger.info("Starting scope discovery for product=%s, days=%d", product, cmd.days)
    scope: ProductFeedbackScope = get_candidate_scope(request)
    logger.info(
        "Scope resolved: %d conversations (mode=%s)",
        scope.total_conversations, scope.retrieval_mode,
    )

    # Step 2 — VOC aggregation using the scoped conversation IDs
    # run_voc() receives the IDs externally; it does NOT call the pipeline itself.
    voc_result = run_voc(cmd, conversation_ids=scope.conversation_ids)

    # Step 3 — Synthesis (uses the same scope, no re-discovery)
    pf_result = get_product_feedback_for_scope(scope, request)

    # Step 4 — Merge: attach product_feedback to the voc result dict
    # Convert ProductFeedbackResult dataclass to dict for the formatter
    voc_result["product_feedback"] = {
        "product_name": pf_result.product_name,
        "timeframe_label": pf_result.timeframe_label,
        "total_conversations": pf_result.total_candidate_conversations,
        "total_messages": pf_result.total_messages_analysed,
        "retrieval_mode": pf_result.retrieval_mode,
        "headline": pf_result.headline,
        "themes": [
            {
                "label": t.label,
                "mention_count": t.mention_count,
                "representative_quote": t.representative_quote,
            }
            for t in pf_result.themes
        ],
        "so_what": pf_result.so_what,
        "recommended_action": pf_result.recommended_action,
        "caveats": pf_result.caveats,
        "candidate_conversation_ids": pf_result.candidate_conversation_ids,
    }

    return voc_result


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
