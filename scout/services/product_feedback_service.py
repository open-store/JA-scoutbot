"""
Product-feedback service — orchestration layer.

Flow:
  request → resolve aliases → resolve tag scope → repository (strict → fallback) →
  evidence cleaning/ranking → LLM synthesis → ProductFeedbackResult
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set

from openai import OpenAI

# Sibling modules
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scout.taxonomy.tags import tag_names_for_group
from scout.taxonomy.products import resolve_aliases, canonical_name
from scout.models.product_feedback import (
    ProductFeedbackRequest,
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
# LLM synthesis
# ---------------------------------------------------------------------------

def _synthesize_themes(
    cleaned_messages: List[Dict[str, Any]],
    product_name: str,
    max_messages: int = 150,
) -> Dict[str, Any]:
    """Send cleaned message bodies to the LLM for theme synthesis.

    Returns a dict with keys: headline, themes, so_what, recommended_action.
    """
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
        # Strip markdown code fences if present
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
# Orchestration
# ---------------------------------------------------------------------------

def run_product_feedback_pipeline(
    request: ProductFeedbackRequest,
) -> ProductFeedbackResult:
    """Execute the full product-feedback pipeline.

    Tries strict retrieval first, then ranked fallbacks.
    """
    product = canonical_name(request.product_name)
    aliases = list(request.product_aliases) if request.product_aliases else resolve_aliases(request.product_name)

    # Resolve tag scope → UUIDs
    all_tag_names: Set[str] = set()
    for group in request.tag_groups:
        try:
            all_tag_names.update(tag_names_for_group(group))
        except KeyError:
            logger.warning("Unknown tag group: %s", group)
    tag_uuids = resolve_tag_names_to_uuids(all_tag_names)

    logger.info(
        "Pipeline start: product=%s, aliases=%s, tag_uuids=%d, days=%d",
        product, aliases, len(tag_uuids), request.timeframe_days,
    )

    # --- Strict pass ---
    candidates = fetch_strict_candidates(
        tag_uuids=tag_uuids,
        aliases=aliases,
        timeframe_days=request.timeframe_days,
    )
    retrieval_mode = "strict"

    # --- Fallback 1: relaxed alias match (still requires tag) ---
    if len(candidates) < request.min_sample_size:
        logger.info("Strict returned %d (< %d), trying fallback_relaxed",
                     len(candidates), request.min_sample_size)
        fallback1 = fetch_fallback_relaxed_candidates(
            tag_uuids=tag_uuids,
            aliases=aliases,
            timeframe_days=request.timeframe_days,
        )
        # Merge, dedup by conversation ID
        seen_ids = {c["CONVERSATION_ID"] for c in candidates}
        for c in fallback1:
            if c["CONVERSATION_ID"] not in seen_ids:
                candidates.append(c)
                seen_ids.add(c["CONVERSATION_ID"])
        if len(candidates) >= request.min_sample_size:
            retrieval_mode = "fallback_relaxed"

    # --- Fallback 2: body/subject only (no tag requirement) ---
    if len(candidates) < request.min_sample_size:
        logger.info("Relaxed returned %d (< %d), trying fallback_body_only",
                     len(candidates), request.min_sample_size)
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

    total_candidates = len(candidates)
    logger.info("Total candidate conversations: %d (mode=%s)", total_candidates, retrieval_mode)

    if total_candidates == 0:
        return ProductFeedbackResult(
            product_name=product,
            timeframe_label=f"Last {request.timeframe_days} complete days",
            total_candidate_conversations=0,
            total_messages_analysed=0,
            retrieval_mode=retrieval_mode,
            headline=f"No customer conversations found for '{product}'.",
            so_what="No data available for this product in this timeframe.",
            recommended_action="Try a longer timeframe (e.g., L180) or check the product name.",
            caveats=["No conversations matched the product filter."],
        )

    # --- Fetch customer-authored messages ---
    conversation_ids = [c["CONVERSATION_ID"] for c in candidates]
    raw_messages = fetch_customer_messages(
        conversation_ids=conversation_ids,
        max_messages=request.max_messages,
        strict_customer=True,
    )

    # If strict customer filter returns too few, try broad filter
    if len(raw_messages) < request.min_sample_size:
        logger.info("Strict customer filter returned %d, trying broad filter", len(raw_messages))
        raw_messages = fetch_customer_messages(
            conversation_ids=conversation_ids,
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

    # --- Build caveats ---
    caveats: List[str] = []
    if total_analysed < request.min_sample_size:
        caveats.append(
            f"Low sample size ({total_analysed} messages). "
            "Results are directional only — try a longer timeframe for more data."
        )
    if retrieval_mode == "fallback_body_only":
        caveats.append(
            "Results include conversations without product-feedback tags. "
            "Some may not be directly product-related."
        )
    if retrieval_mode == "fallback_relaxed":
        caveats.append(
            "Broadened matching was used to find enough conversations. "
            "Some results may reference related products."
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
        total_candidate_conversations=total_candidates,
        total_messages_analysed=total_analysed,
        retrieval_mode=retrieval_mode,
        themes=themes,
        headline=synthesis.get("headline", ""),
        so_what=synthesis.get("so_what", ""),
        recommended_action=synthesis.get("recommended_action", ""),
        caveats=caveats,
    )
