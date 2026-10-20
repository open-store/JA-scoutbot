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
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from scout.llm import MODEL, get_client

from scout.taxonomy.tags import tag_names_for_group
from scout.taxonomy.products import ProductCatalog, ProductResolution, alias_only_catalog, resolve_product
from scout.models.orders import OrderRecord
from scout.models.product_feedback import (
    BODY_ONLY,
    EMPTY,
    INFERRED,
    STRICT,
    ProductAttribution,
    ProductFeedbackRequest,
    ProductFeedbackScope,
    ProductFeedbackResult,
    ProductFeedbackTheme,
)
from scout.repositories.product_feedback_repository import (
    fetch_agent_messages,
    fetch_conversation_context,
    fetch_strict_candidates,
    fetch_fallback_body_only_candidates,
    fetch_customer_messages,
    fetch_tagged_candidates_outside_scope,
    resolve_tag_names_to_uuids,
)
from scout.services.attribution_service import (
    LOOKBACK_DAYS,
    Attributor,
    ConversationContext,
    ProductTarget,
    as_aware,
    order_numbers_in,
    verified_email,
)
from scout.services.evidence_service import process_evidence_with_stats, scrub_personal_details
from scout.services.order_lookup import OrderLookup, UnavailableOrderLookup

logger = logging.getLogger("scout.services.product_feedback")

# Messages sent to the model in one synthesis call.
SYNTHESIS_MAX_MESSAGES = 150

# Bound on product-feedback tickets outside the scope checked for the inferred tier.
INFERRED_CANDIDATE_LIMIT = 200

_SCOPE_FILTER_KEYS = ("channel", "tag", "agent")

_MAX_QUOTE_CHARS = 220


def _tag_uuids(tag_groups: List[str]) -> List[str]:
    names: Set[str] = set()
    for group in tag_groups:
        try:
            names.update(tag_names_for_group(group))
        except KeyError:
            logger.warning("Unknown tag group: %s", group)
    return resolve_tag_names_to_uuids(names)


def _window(request: ProductFeedbackRequest) -> Tuple[datetime, datetime]:
    """Explicit window for the request, defaulting to the slash-parser window."""
    if request.start is not None and request.end is not None:
        return request.start, request.end
    tz = ZoneInfo(request.timezone)
    end = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return end - timedelta(days=request.timeframe_days), end


def _resolution(request: ProductFeedbackRequest) -> ProductResolution:
    if request.resolution is not None:
        return request.resolution
    return resolve_product(request.product_name, alias_only_catalog())


def _window_label(start: datetime, end: datetime) -> str:
    last_day = end - timedelta(days=1)
    return f"{start.strftime('%b %d')}–{last_day.strftime('%b %d')}, {last_day.year}"


# ---------------------------------------------------------------------------
# Step 1 — Scope discovery
# ---------------------------------------------------------------------------

def get_candidate_scope(request: ProductFeedbackRequest) -> ProductFeedbackScope:
    """Discover candidate conversations for the given product request.

    Tries strict retrieval first, then the body-only fallback, unless
    ``request.retrieval_ceiling`` pins the widest pass. Returns a
    ProductFeedbackScope with the authoritative conversation IDs.

    This function does NOT fetch message bodies or call the LLM.
    It is intentionally fast and side-effect-free beyond Snowflake reads.
    """
    resolution = _resolution(request)
    product = resolution.canonical_name or request.product_name
    aliases = list(request.product_aliases) if request.product_aliases else list(resolution.match_phrases)
    tag_groups = list(request.tag_groups)
    start, end = _window(request)
    filters = {k: str(v) for k, v in request.filters.items() if k in _SCOPE_FILTER_KEYS and v}
    cap = request.max_conversations

    tag_uuids = _tag_uuids(tag_groups)

    logger.info(
        "Scope discovery (%s): product=%s, phrases=%s, tag_uuids=%d, window=%s..%s, filters=%s",
        request.period, product, aliases, len(tag_uuids), start.isoformat(), end.isoformat(), filters,
    )

    caveats: List[str] = list(resolution.caveats)
    capped = False

    # --- Strict pass ---
    candidates = fetch_strict_candidates(
        tag_uuids=tag_uuids,
        aliases=aliases,
        start=start,
        end=end,
        filters=filters,
        max_conversations=cap,
    )
    capped = capped or len(candidates) >= cap
    retrieval_mode = STRICT

    # --- Fallback: body/subject only (no tag requirement) ---
    if request.retrieval_ceiling is None:
        widen = len(candidates) < request.min_sample_size
    else:
        widen = request.retrieval_ceiling == BODY_ONLY
    if widen:
        logger.info(
            "Strict returned %d (min %d, ceiling %s), adding body-only pass",
            len(candidates), request.min_sample_size, request.retrieval_ceiling,
        )
        fallback = fetch_fallback_body_only_candidates(
            aliases=aliases,
            start=start,
            end=end,
            filters=filters,
            max_conversations=cap,
        )
        capped = capped or len(fallback) >= cap
        seen_ids = {c["CONVERSATION_ID"] for c in candidates}
        added = 0
        for c in fallback:
            if c["CONVERSATION_ID"] not in seen_ids:
                candidates.append(c)
                seen_ids.add(c["CONVERSATION_ID"])
                added += 1
        retrieval_mode = BODY_ONLY
        if added:
            caveats.append(
                "Results include conversations without product-feedback tags. "
                "Some may not be directly product-related."
            )

    if len(candidates) > cap:
        candidates = candidates[:cap]
        capped = True
    if capped:
        caveats.append(
            f"Scope capped at the {cap} most recent matching conversations; "
            "conversation counts are a lower bound."
        )

    conversation_ids = [c["CONVERSATION_ID"] for c in candidates]
    logger.info(
        "Scope discovery complete (%s): %d conversations (mode=%s, capped=%s)",
        request.period, len(conversation_ids), retrieval_mode, capped,
    )

    if not conversation_ids:
        retrieval_mode = EMPTY

    return ProductFeedbackScope(
        product_name=product,
        conversation_ids=conversation_ids,
        aliases_used=aliases,
        tag_groups_used=tag_groups,
        retrieval_mode=retrieval_mode,
        timeframe_days=request.timeframe_days,
        caveats=caveats,
        start=start,
        end=end,
        timezone=request.timezone,
        filters=filters,
        resolution=resolution,
        topic=request.topic,
        period=request.period,
        capped=capped,
        max_conversations=cap,
    )


def retrieval_ceiling_for(scope: ProductFeedbackScope) -> str:
    """Widest pass a comparison scope should use to match *scope*.

    An empty scope already tried every pass, so the comparison does too.
    """
    return STRICT if scope.retrieval_mode == STRICT else BODY_ONLY


# ---------------------------------------------------------------------------
# Step 2 — Evidence fetching + synthesis (takes a pre-resolved scope)
# ---------------------------------------------------------------------------

def get_product_feedback_for_scope(
    scope: ProductFeedbackScope,
    request: ProductFeedbackRequest,
    catalog: Optional[ProductCatalog] = None,
    order_lookup: Optional[OrderLookup] = None,
) -> ProductFeedbackResult:
    """Fetch customer messages for the given scope, attribute them, and synthesise themes.

    Requires a ProductFeedbackScope from get_candidate_scope().
    Does NOT re-run scope discovery — it uses scope.conversation_ids directly.

    Only customer-authored messages are read; authorship is never relaxed to
    reach a sample size. Every message gets an attribution tier. Only tiers in
    ``scope.attribution_tiers`` (confirmed and order-linked by default) go to
    synthesis. Inferred evidence is reported separately.
    """
    product = scope.product_name
    aliases = scope.aliases_used
    catalog = catalog if catalog is not None else alias_only_catalog()
    lookup: OrderLookup = order_lookup if order_lookup is not None else UnavailableOrderLookup(
        reason="Order data is not connected, so the order-linked and inferred tiers were not evaluated.")
    if scope.start is not None and scope.end is not None:
        timeframe_label = _window_label(scope.start, scope.end)
    else:
        timeframe_label = f"Last {request.timeframe_days} complete days"

    if scope.is_empty:
        return ProductFeedbackResult(
            product_name=product,
            timeframe_label=timeframe_label,
            total_candidate_conversations=0,
            total_messages_analysed=0,
            retrieval_mode=scope.retrieval_mode,
            headline=f"No customer conversations found for '{product}'.",
            so_what="No data available for this product in this timeframe.",
            recommended_action="Try a longer timeframe (e.g., L180) or check the product name.",
            caveats=list(scope.caveats) + ["No conversations matched the product filter."],
            low_sample=True,
            retrieval_capped=scope.capped,
            topic=scope.topic,
            order_data=_order_data(lookup),
        )

    # --- Customer-authored messages, plus agent text used only for quote removal ---
    contexts = _contexts(scope.conversation_ids)
    raw_messages = fetch_customer_messages(
        conversation_ids=scope.conversation_ids,
        max_messages=request.max_messages,
        strict_customer=True,
    )
    messages_capped = len(raw_messages) >= request.max_messages
    agent_messages = fetch_agent_messages(scope.conversation_ids)

    # --- Evidence pipeline ---
    cleaned_messages, stats = process_evidence_with_stats(
        raw_messages=raw_messages,
        aliases=aliases,
        body_key="BODY",
        max_output=request.max_messages,
        agent_messages=agent_messages,
    )

    # --- Attribution ---
    target = ProductTarget(scope.resolution or _resolution(request), catalog)
    attributions = _attribute(cleaned_messages, contexts, target, lookup)
    tier_of = [a.tier for a in attributions]
    evidence = [m for m, tier in zip(cleaned_messages, tier_of) if tier in scope.attribution_tiers]
    inferred_extra = _inferred_outside_scope(scope, target, lookup) if lookup.available else []

    analysed = [m for m in evidence if m.get("cleaned_body")][:SYNTHESIS_MAX_MESSAGES]
    total_analysed = len(analysed)
    logger.info(
        "Messages: %d retrieved, %d cleaned, %d product evidence, %d analysed",
        len(raw_messages), len(cleaned_messages), len(evidence), total_analysed,
    )

    # --- Build caveats (merge scope caveats + synthesis caveats) ---
    caveats: List[str] = list(scope.caveats)
    if messages_capped:
        caveats.append(
            f"Message retrieval stopped at the {request.max_messages} most recent customer "
            "messages; themes are sample-based."
        )
    if len(evidence) > total_analysed:
        caveats.append(
            f"Themes use the {total_analysed} highest-ranked of {len(evidence)} "
            "attributed messages."
        )
    if not lookup.available:
        caveats.append(lookup.reason)
    low_sample = total_analysed < request.min_sample_size
    if low_sample:
        caveats.append(
            f"Low sample size ({total_analysed} attributed messages). "
            "Results are directional only — try a longer timeframe for more data."
        )

    # --- LLM synthesis ---
    unlinked = 0
    if total_analysed > 0:
        synthesis = _synthesize_themes(analysed, product, topic=scope.topic)
        themes, unlinked = _link_themes(synthesis.get("themes", []), analysed)
        if unlinked:
            caveats.append(
                f"{unlinked} synthesised theme(s) without valid evidence references were dropped."
            )
    else:
        reason = (
            "No customer message could be attributed to this product."
            if cleaned_messages else
            "Messages were found but contained only auto-replies, surveys, or agent responses."
        )
        synthesis = {
            "headline": f"No usable customer messages attributed to '{product}'.",
            "so_what": reason,
            "recommended_action": "Try a longer timeframe or review tickets manually.",
        }
        themes = []

    return ProductFeedbackResult(
        product_name=product,
        timeframe_label=timeframe_label,
        total_candidate_conversations=scope.total_conversations,
        total_messages_analysed=total_analysed,
        retrieval_mode=scope.retrieval_mode,
        themes=themes,
        headline=synthesis.get("headline", ""),
        so_what=synthesis.get("so_what", ""),
        recommended_action=synthesis.get("recommended_action", ""),
        caveats=caveats,
        candidate_conversation_ids=scope.conversation_ids,
        retrieved_message_count=len(raw_messages),
        cleaned_message_count=len(cleaned_messages),
        analysed_message_ids=[str(m.get("MESSAGE_ID", "")) for m in analysed],
        low_sample=low_sample,
        retrieval_capped=scope.capped or messages_capped,
        topic=scope.topic,
        attributions=attributions,
        inferred_attributions=inferred_extra,
        evidence_stats=stats.to_dict(),
        order_data=_order_data(lookup),
        unlinked_theme_count=unlinked,
    )


def _order_data(lookup: OrderLookup) -> Dict[str, object]:
    return {"available": lookup.available, "source": lookup.source, "reason": lookup.reason}


def _contexts(conversation_ids: List[str]) -> Dict[str, ConversationContext]:
    rows = fetch_conversation_context(conversation_ids)
    return {str(r.get("CONVERSATION_ID")): ConversationContext.from_row(r) for r in rows}


def _prefetch_orders(
    messages: List[Dict[str, Any]],
    contexts: Dict[str, ConversationContext],
    lookup: OrderLookup,
) -> Tuple[Dict[str, OrderRecord], Dict[str, List[OrderRecord]]]:
    """Two bounded lookups: orders the customers reference, then their recent order history."""
    if not lookup.available:
        return {}, {}
    refs: Set[str] = set()
    for m in messages:
        refs.update(order_numbers_in(str(m.get("cleaned_body", ""))))
    for ctx in contexts.values():
        refs.update(order_numbers_in(ctx.subject))
    by_number = {o.order_number: o for o in lookup.orders_by_number(sorted(refs)) if o.order_number}

    emails = sorted({e for e in (verified_email(c.customer_email) for c in contexts.values()) if e})
    created = [t for t in (as_aware(c.created_at) for c in contexts.values()) if t is not None]
    history: Dict[str, List[OrderRecord]] = {}
    if emails and created:
        start = min(created) - timedelta(days=LOOKBACK_DAYS)
        for order in lookup.orders_for_emails(emails, start, max(created)):
            email = verified_email(order.customer_email)
            if email:
                history.setdefault(email, []).append(order)
    return by_number, history


def _attribute(
    messages: List[Dict[str, Any]],
    contexts: Dict[str, ConversationContext],
    target: ProductTarget,
    lookup: OrderLookup,
) -> List[ProductAttribution]:
    by_number, history = _prefetch_orders(messages, contexts, lookup)
    attributor = Attributor(target, contexts, by_number, history, lookup.available)
    return attributor.attribute(messages)


def _inferred_outside_scope(
    scope: ProductFeedbackScope,
    target: ProductTarget,
    lookup: OrderLookup,
) -> List[ProductAttribution]:
    """Inferred evidence from product-feedback tickets that never matched a product phrase.

    Only tickets whose customer bought this product before the ticket are
    read. These stay out of the scope's counts and themes.
    """
    if scope.start is None or scope.end is None:
        return []
    rows = fetch_tagged_candidates_outside_scope(
        tag_uuids=_tag_uuids(list(scope.tag_groups_used)),
        start=scope.start,
        end=scope.end,
        exclude_ids=scope.conversation_ids,
        filters=scope.filters,
        max_conversations=INFERRED_CANDIDATE_LIMIT,
    )
    ids = [str(r["CONVERSATION_ID"]) for r in rows]
    if not ids:
        return []
    contexts = _contexts(ids)
    _, history = _prefetch_orders([], contexts, lookup)
    probe = Attributor(target, contexts, {}, history, True)
    candidates = []
    for cid, ctx in contexts.items():
        orders = probe.history_for(ctx) or []
        if any(target.owns(target.product_of_line(line.product_title)) for o in orders for line in o.lines):
            candidates.append(cid)
    if not candidates:
        return []
    raw = fetch_customer_messages(candidates, max_messages=200, strict_customer=True)
    agent = fetch_agent_messages(candidates)
    cleaned, _ = process_evidence_with_stats(raw, [], agent_messages=agent)
    attributions = _attribute(cleaned, {c: contexts[c] for c in candidates}, target, lookup)
    return [a for a in attributions if a.tier == INFERRED]


def _link_themes(
    raw_themes: List[Dict[str, Any]],
    analysed: List[Dict[str, Any]],
) -> Tuple[List[ProductFeedbackTheme], int]:
    """Turn model themes into evidence-linked themes; counts are computed here.

    ``mention_count`` is distinct tickets among valid cited messages. A quote
    is kept only if it appears in a cited message, then personal details are
    scrubbed. Themes with no valid citations are dropped and counted.
    """
    themes: List[ProductFeedbackTheme] = []
    dropped = 0
    for raw in raw_themes if isinstance(raw_themes, list) else []:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        refs = raw.get("evidence") or []
        idx = sorted({int(r) for r in refs if isinstance(r, (int, float)) or str(r).isdigit()}
                     if isinstance(refs, list) else set())
        cited = [analysed[i - 1] for i in idx if 1 <= i <= len(analysed)]
        if not cited:
            dropped += 1
            continue
        conversations = tuple(dict.fromkeys(str(m.get("CONVERSATION_ID")) for m in cited))
        quote, quote_id = _verified_quote(str(raw.get("representative_quote") or ""), cited)
        themes.append(ProductFeedbackTheme(
            label=str(raw.get("label", "")).strip(),
            mention_count=len(conversations),
            representative_quote=quote,
            message_count=len(cited),
            evidence_message_ids=tuple(str(m.get("MESSAGE_ID", "")) for m in cited),
            conversation_ids=conversations,
            quote_message_id=quote_id,
        ))
    themes.sort(key=lambda t: (-t.mention_count, -t.message_count))
    return themes, dropped


def _squash(text: str) -> str:
    return " ".join(text.lower().split())


def _verified_quote(quote: str, cited: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
    needle = _squash(quote.strip().strip('"').strip())
    if len(needle) < 8:
        return None, None
    for m in cited:
        if needle in _squash(str(m.get("cleaned_body", ""))):
            text = scrub_personal_details(quote.strip().strip('"').strip())
            if len(text) > _MAX_QUOTE_CHARS:
                text = text[:_MAX_QUOTE_CHARS].rsplit(" ", 1)[0] + "…"
            return text, str(m.get("MESSAGE_ID", ""))
    return None, None


# ---------------------------------------------------------------------------
# LLM synthesis (private)
# ---------------------------------------------------------------------------

def _synthesize_themes(
    cleaned_messages: List[Dict[str, Any]],
    product_name: str,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """Send cleaned message bodies to the LLM for theme synthesis.

    The caller decides which messages are analysed; every message passed in
    is sent, so the caller's count is the analysed count. Message ``[n]`` is
    ``cleaned_messages[n - 1]``, which is how ``_link_themes`` resolves the
    model's ``evidence`` numbers.
    """
    bodies = [str(m.get("cleaned_body", "")) for m in cleaned_messages]
    if not any(bodies):
        return {
            "headline": f"No usable customer messages found for {product_name}.",
            "themes": [],
            "so_what": "Insufficient data for analysis.",
            "recommended_action": "Try a longer timeframe or check product name.",
        }

    numbered = "\n".join(f"[{i+1}] {b[:500]}" for i, b in enumerate(bodies))

    system_prompt = """You are a customer-insights analyst for a premium menswear brand.
You will receive numbered customer messages about a specific product.
Your job is to synthesize the key themes and cite the messages that support each one.

Return ONLY valid JSON with this structure:
{
  "headline": "One-sentence summary of what customers are mainly saying",
  "themes": [
    {"label": "Theme name", "evidence": [message numbers], "representative_quote": "Exact customer quote"},
    ...
  ],
  "so_what": "One-sentence business implication",
  "recommended_action": "One specific recommended next step"
}

Rules:
- Themes must be grounded in the actual messages. Do not invent themes.
- evidence must list every numbered message that supports the theme, and only those.
- representative_quote must be copied word for word from one of the cited messages.
- A theme supported by one message is a single report; do not describe it as common.
- Maximum 7 themes.
- Keep headline, so_what, and recommended_action concise (one sentence each).
- If a focus topic is given, the headline must say what customers say about that topic,
  or state plainly that none of the messages address it. Do not stretch other feedback to fit it."""

    focus = f"\nFocus topic from the question: {topic}" if topic else ""
    user_prompt = f"Product: {product_name}{focus}\n\nCustomer messages ({len(bodies)} total):\n\n{numbered}"

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=1500,
        )
        raw = (resp.choices[0].message.content or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        if raw.startswith("json"):
            raw = raw[4:]
        parsed = json.loads(raw.strip())
        if not isinstance(parsed, dict):
            raise ValueError("synthesis output is not a JSON object")
        return parsed
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
