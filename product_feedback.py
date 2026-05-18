"""
Scout Product Feedback — thin wrapper.

This module is the public API called by ``queries/voc.py``.
It delegates to the new pipeline (scout.services.product_feedback_service)
and converts the typed result back into the dict shape that ``formatters.py``
already expects.

The old subject-line-only retrieval is fully replaced.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from scout.models.product_feedback import ProductFeedbackRequest
from scout.services.product_feedback_service import run_product_feedback_pipeline

logger = logging.getLogger("scout.product_feedback")


def get_product_feedback(
    product_name: str,
    timeframe_days: int,
    *,
    product_aliases: Optional[List[str]] = None,
    min_sample_size: int = 5,
    max_messages: int = 200,
) -> Dict[str, Any]:
    """Run the product-feedback pipeline and return a formatter-ready dict.

    This is the only public function that ``voc.py`` should call.

    Returns a dict with keys matching the new formatter contract:
        headline, themes, so_what, recommended_action,
        sample_count, total_conversations, retrieval_mode,
        low_sample, caveats, source.
    """
    request = ProductFeedbackRequest(
        product_name=product_name,
        timeframe_days=timeframe_days,
        tag_groups=("product_feedback",),
        product_aliases=tuple(product_aliases) if product_aliases else (),
        min_sample_size=min_sample_size,
        max_messages=max_messages,
    )

    try:
        result = run_product_feedback_pipeline(request)
    except Exception as e:
        logger.error("Product feedback pipeline failed: %s", e, exc_info=True)
        return {
            "headline": f"Product feedback unavailable ({type(e).__name__}).",
            "themes": [],
            "so_what": "The pipeline encountered an error. Raw VOC data is still shown above.",
            "recommended_action": "Retry or review tickets manually.",
            "sample_count": 0,
            "total_conversations": 0,
            "retrieval_mode": "error",
            "low_sample": True,
            "caveats": [f"Pipeline error: {e}"],
            "source": "Richpanel tickets via Snowflake",
        }

    # Convert typed result → dict for formatters.py
    return {
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
        "sample_count": result.total_messages_analysed,
        "total_conversations": result.total_candidate_conversations,
        "retrieval_mode": result.retrieval_mode,
        "low_sample": result.total_messages_analysed < min_sample_size,
        "caveats": result.caveats,
        "source": result.source,
    }
