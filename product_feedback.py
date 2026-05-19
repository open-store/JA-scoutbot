"""
product_feedback.py — thin wrapper (legacy entry point).

This module exists for backward compatibility only.
New code should use scout/services/voc_service.py for product VOC queries,
which calls get_candidate_scope() and get_product_feedback_for_scope() directly.

This wrapper delegates to run_product_feedback_pipeline() which internally
calls both steps. It is kept so any callers that haven't migrated yet
continue to work without changes.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scout.models.product_feedback import ProductFeedbackRequest
from scout.services.product_feedback_service import run_product_feedback_pipeline


def get_product_feedback(
    product_name: str,
    timeframe_days: int = 30,
) -> dict:
    """Run the full product-feedback pipeline and return a dict for the formatter.

    Deprecated: prefer voc_service.run_voc_query() for product VOC queries.
    """
    request = ProductFeedbackRequest(
        product_name=product_name,
        timeframe_days=timeframe_days,
    )
    result = run_product_feedback_pipeline(request)

    return {
        "product_name": result.product_name,
        "timeframe_label": result.timeframe_label,
        "total_conversations": result.total_candidate_conversations,
        "total_messages": result.total_messages_analysed,
        "retrieval_mode": result.retrieval_mode,
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
        "candidate_conversation_ids": result.candidate_conversation_ids,
    }
