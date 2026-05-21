"""
Product-feedback service — orchestration layer.

Public API (two-step):
  1. get_candidate_scope(request) -> ProductFeedbackScope
     Discovers which conversations are relevant for the product.
     Returns a scope object containing the authoritative conversation IDs.
     This is fast — it does NOT fetch message bodies or call the LLM.

  2. get_product_feedback_for_scope(scope, request) -> ProductFeedbackResult
     Takes an already-resolved scope and fetches customer messages,
     cleans evidence, and synthesises themes via LLM.

Callers (e.g. voc_service.py) should call step 1 first, pass the scope's
conversation_ids into VOC aggregation queries, then call step 2 for synthesis.
This ensures VOC metrics and synthesis always share the same conversation scope.

Legacy helper (backward-compat):
  run_product_feedback_pipeline(request) -> ProductFeedbackResult
    Calls both steps internally. Kept for any callers that don't need the
    split behaviour. Will be removed once all callers migrate to voc_service.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

from openai import OpenAI

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scout.taxonomy.tags import tag_names_for_group
from scout.taxonomy.products import resolve_aliases, canonical_name
from scout.models.product_feedback import (
    ProductFeedbackRequest,
    ProductFeedbackScope,
    ProductFeedbackResult,
    ProductFeedbackTheme,
)
from scout.repositories.product_feedback_repository import (
    fetch_strict_candidates,
    fetch_fallback_relaxed_candidates,
    fetch_fallback_body_only_candidates,
    fetch_customer_messages,
    resolve_tag_names_to_uuids,
)
from scout.services.evidence_service import process_evidence

logger = logging.getLogger("scout.services.product_feedback")

_LLM_MODEL = "gpt-4.1-mini"


# ---------------------------------------------------------------------------
# Step 1 — Scope discovery
# ---------------------------------------------------------------------------

def get_candidate_scope(request: ProductFeedbackRequest) -> ProductFeedbackScope:
    """Discover candidate conversations for the given product request.

    Tries strict retrieval first, then ranked fallbacks.
    Returns a ProductFeedbackScope with the authoritative conversation IDs.

    This function does NOT fetch message bodies or call the LLM.
    It is intentionally fast and side-effect-free beyond Snowflake reads.
    """
    product = canonical_name(request.product_name)
    aliases = list(request.product_aliases) if request.product_aliases else resolve_aliases(request.product_name)
    tag_groups = list(request.tag_groups)

    # Resolve tag scope → UUIDs
    all_tag_names: Set[str] = set()
    for group in tag_groups:
        try:
            all_tag_names.update(tag_names_for_group(group))
        except KeyError:
            logger.warning("Unknown tag group: %s", group)
    tag_uuids = resolve_tag_names_to_uuids(all_tag_names)

    logger.info(
        "Scope discovery: product=%s, aliases=%s, tag_uuids=%d, days=%d",
        product, aliases, len(tag_uuids), request.timeframe_days,
    )

    caveats: List[str] = []

    # --- Strict pass ---
    candidates = fetch_strict_candidates(
        tag_uuids=tag_uuids,
        aliases=aliases,
        timeframe_days=request.timeframe_days,
    )
    retrieval_mode = "strict"

    # --- Fallback 1: relaxed alias match (still requires tag) ---
    if len(candidates) < request.min_sample_size:
        logger.info(
            "Strict returned %d (< %d), trying fallback_relaxed",
            len(candidates), request.min_sample_size,
        )
        fallback1 = fetch_fallback_relaxed_candidates(
            tag_uuids=tag_uuids,
            aliases=aliases,
            timeframe_days=request.timeframe_days,
        )
        seen_ids = {c["CONVERSATION_ID"] for c in candidates}
        for c in fallback1:
            if c["CONVERSATION_ID"] not in seen_ids:
                candidates.append(c)
                seen_ids.add(c["CONVERSATION_ID"])
        if len(candidates) >= request.min_sample_size:
            retrieval_mode = "fallback_relaxed"
            caveats.append(
                "Broadened matching was used to find enough conversations. "
                "Some results may reference related products."
            )

    # --- Fallback 2: body/subject only (no tag requirement) ---
    if len(candidates) < request.min_sample_size:
        logger.info(
            "Relaxed returned %d (< %d), trying fallback_body_only",
            len(candidates), request.min_sample_size,
        )
        fallback2 = fetch_fallback_body_only_candidates(
            aliases=aliases,
            timeframe_days=request.timeframe_days,
        )
        seen_ids = {c["CONVERSATION_ID"] for c in candidates}
        for c in fallback2:
            if c["CONVERSATION_ID"] not in seen_ids:
                candidates.append(c)
                seen_ids.add(c["CONVERSATION_ID"])
        retrieval_mode = "fallback_body_only"
        caveats.append(
            "Results include conversations without product-feedback tags. "
            "Some may not be directly product-related."
        )

    conversation_ids = [c["CONVERSATION_ID"] for c in candidates]
    logger.info(
        "Scope discovery complete: %d conversations (mode=%s)",
        len(conversation_ids), retrieval_mode,
    )

    if not conversation_ids:
        retrieval_mode = "empty"

    return ProductFeedbackScope(
        product_name=product,
        conversation_ids=conversation_ids,
        aliases_used=aliases,
        tag_groups_used=tag_groups,
        retrieval_mode=retrieval_mode,
        timeframe_days=request.timeframe_days,
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Step 2 — Evidence fetching + synthesis (takes a pre-resolved scope)
# ---------------------------------------------------------------------------

def get_product_feedback_for_scope(
    scope: ProductFeedbackScope,
    request: ProductFeedbackRequest,
) -> ProductFeedbackResult:
    """Fetch customer messages for the given scope and synthesise themes.

    Requires a ProductFeedbackScope from get_candidate_scope().
    Does NOT re-run scope discovery — it uses scope.conversation_ids directly.
    """
    product = scope.product_name
    aliases = scope.aliases_used

    if scope.is_empty:
        return ProductFeedbackResult(
            product_name=product,
            timeframe_label=f"Last {request.timeframe_days} complete days",
            total_candidate_conversations=0,
            total_messages_analysed=0,
            retrieval_mode=scope.retrieval_mode,
            headline=f"No customer conversations found for '{product}'.",
            so_what="No data available for this product in this timeframe.",
            recommended_action="Try a longer timeframe (e.g., L180) or check the product name.",
            caveats=list(scope.caveats) + ["No conversations matched the product filter."],
        )

    # --- Fetch customer-authored messages ---
    raw_messages = fetch_customer_messages(
        conversation_ids=scope.conversation_ids,
        max_messages=request.max_messages,
        strict_customer=True,
    )

    # If strict customer filter returns too few, try broad filter
    if len(raw_messages) < request.min_sample_size:
        logger.info("Strict customer filter returned %d, trying broad filter", len(raw_messages))
        raw_messages = fetch_customer_messages(
            conversation_ids=scope.conversation_ids,
            max_messages=request.max_messages,
            strict_customer=False,
        )

    # --- Evidence pipeline ---
    cleaned_messages = process_evidence(
        raw_messages=raw_messages,
        aliases=aliases,
        body_key="BODY",
        max_output=request.max_messages,
    )

    total_analysed = len(cleaned_messages)
    logger.info("Cleaned messages for synthesis: %d", total_analysed)

    # --- Build caveats (merge scope caveats + synthesis caveats) ---
    caveats: List[str] = list(scope.caveats)
    if total_analysed < request.min_sample_size:
        caveats.append(
            f"Low sample size ({total_analysed} messages). "
            "Results are directional only — try a longer timeframe for more data."
        )

    # --- LLM synthesis ---
    if total_analysed > 0:
        synthesis = _synthesize_themes(cleaned_messages, product, max_messages=150)
    else:
        synthesis = {
            "headline": f"No usable customer messages found for '{product}' after cleaning.",
            "themes": [],
            "so_what": "Messages were found but contained only auto-replies, surveys, or agent responses.",
            "recommended_action": "Try a longer timeframe or review tickets manually.",
        }

    # --- Build result ---
    themes = [
        ProductFeedbackTheme(
            label=t.get("label", ""),
            mention_count=t.get("mention_count", 0),
            representative_quote=t.get("representative_quote"),
        )
        for t in synthesis.get("themes", [])
    ]

    return ProductFeedbackResult(
        product_name=product,
        timeframe_label=f"Last {request.timeframe_days} complete days",
        total_candidate_conversations=scope.total_conversations,
        total_messages_analysed=total_analysed,
        retrieval_mode=scope.retrieval_mode,
        themes=themes,
        headline=synthesis.get("headline", ""),
        so_what=synthesis.get("so_what", ""),
        recommended_action=synthesis.get("recommended_action", ""),
        caveats=caveats,
        candidate_conversation_ids=scope.conversation_ids,
    )


# ---------------------------------------------------------------------------
# LLM synthesis (private)
# ---------------------------------------------------------------------------

def _synthesize_themes(
    cleaned_messages: List[Dict[str, Any]],
    product_name: str,
    max_messages: int = 150,
) -> Dict[str, Any]:
    """Send cleaned message bodies to the LLM for theme synthesis."""
    bodies = [m["cleaned_body"] for m in cleaned_messages[:max_messages] if m.get("cleaned_body")]
    if not bodies:
        return {
            "headline": f"No usable customer messages found for {product_name}.",
            "themes": [],
            "so_what": "Insufficient data for analysis.",
            "recommended_action": "Try a longer timeframe or check product name.",
        }

    numbered = "\n".join(f"[{i+1}] {b[:500]}" for i, b in enumerate(bodies))

    system_prompt = """You are a customer-insights analyst for a premium menswear brand.
You will receive numbered customer messages about a specific product.
Your job is to synthesize the key themes, with mention counts and representative quotes.

Return ONLY valid JSON with this structure:
{
  "headline": "One-sentence summary of what customers are mainly saying",
  "themes": [
    {"label": "Theme name", "mention_count": N, "representative_quote": "Exact customer quote"},
    ...
  ],
  "so_what": "One-sentence business implication",
  "recommended_action": "One specific recommended next step"
}

Rules:
- Themes must be grounded in the actual messages. Do not invent themes.
- mention_count must reflect how many of the numbered messages support that theme.
- representative_quote must be a real excerpt from the messages (not fabricated).
- Order themes by mention_count descending.
- Maximum 7 themes.
- Keep headline, so_what, and recommended_action concise (one sentence each)."""

    user_prompt = f"Product: {product_name}\n\nCustomer messages ({len(bodies)} total):\n\n{numbered}"

    try:
        client = OpenAI()
        resp = client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        if raw.startswith("json"):
            raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        logger.error("LLM synthesis failed: %s", e)
        return {
            "headline": f"Theme synthesis unavailable ({type(e).__name__}).",
            "themes": [],
            "so_what": "LLM synthesis failed. Raw message data was retrieved successfully.",
            "recommended_action": "Retry or review raw messages manually.",
        }


# ---------------------------------------------------------------------------
# Legacy helper — backward-compat only
# ---------------------------------------------------------------------------

def run_product_feedback_pipeline(
    request: ProductFeedbackRequest,
) -> ProductFeedbackResult:
    """Execute the full product-feedback pipeline (scope discovery + synthesis).

    Deprecated: prefer calling get_candidate_scope() + get_product_feedback_for_scope()
    separately via voc_service.py so that VOC aggregation and synthesis share the
    same conversation scope.

    Kept for backward compatibility. Will be removed once all callers migrate.
    """
    scope = get_candidate_scope(request)
    return get_product_feedback_for_scope(scope, request)
