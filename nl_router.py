"""
Scout Natural Language Router
Uses an LLM to parse free-form user questions into structured Scout commands.
Falls back to keyword-based routing if the LLM is unavailable.
"""

import os
import json
import logging
from openai import OpenAI

logger = logging.getLogger("scout.nl_router")

# OpenAI client — always uses the direct OpenAI API endpoint.
# We explicitly set base_url to avoid picking up OPENAI_BASE_URL (Manus proxy)
# which is blocked on Railway's IP. The OPENAI_API_KEY env var is still used.
_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url="https://api.openai.com/v1",
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
    return _client


SYSTEM_PROMPT = """You are Scout's intent classifier. Your job is to map a user's natural language question into a structured Scout command.

Scout supports these commands:
- CSAT: questions about customer satisfaction scores, CSAT %, ratings, satisfaction trends
- VOC: questions about voice of customer, top themes, contact reasons, ticket volume, what customers are saying, product feedback, customer comments about a specific product, design feedback, fit/sizing feedback
- Errors: questions about bugs, errors, broken features, checkout issues, payment issues, discount code failures, site issues
- NPS: questions about Net Promoter Score, promoters, detractors, survey comments
- Returns: questions about returns, refunds, exchanges, return reasons, return rates
- Reviews: questions about product reviews, star ratings, review sentiment
- Help: user is asking how to use Scout or what commands are available

Supported timeframes: L7 (7 days), L30 (30 days), L180 (180 days). Default to L30 if not specified (most queries benefit from a broader window).

IMPORTANT RULES:
1. If the user asks about a SPECIFIC PRODUCT (e.g. "Anytime Crewneck", "Jetsetter Pant", "Everyday Hoodie"), always include a "product" key in filters with the product name extracted from the query.
2. Questions about what customers are saying, comments, callouts, feedback, complaints, fit, sizing, length, quality about a product → VOC with product filter.
3. Confidence should be "high" when intent is clear (even if the message is long/conversational). Only use "low" when you genuinely cannot determine the intent.
4. For long conversational messages, focus on the CORE QUESTION being asked, not the surrounding context.

Respond with ONLY a JSON object in this exact format:
{
  "command": "CSAT" | "VOC" | "Errors" | "NPS" | "Returns" | "Reviews" | "Help",
  "timeframe": "L7" | "L30" | "L180",
  "filters": {},
  "confidence": "high" | "medium" | "low",
  "reasoning": "brief explanation"
}

Examples:
- "what's our CSAT this week?" → {"command": "CSAT", "timeframe": "L7", "filters": {}, "confidence": "high", "reasoning": "CSAT question, this week = L7"}
- "show me top customer complaints last month" → {"command": "VOC", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "VOC/complaints question, last month = L30"}
- "are there any checkout errors?" → {"command": "Errors", "timeframe": "L7", "filters": {}, "confidence": "high", "reasoning": "Error/checkout question, default L7"}
- "what are customers saying about the hoodie?" → {"command": "VOC", "timeframe": "L30", "filters": {"product": "hoodie"}, "confidence": "high", "reasoning": "VOC question with product filter"}
- "Are you able to let me know if we get comments or callouts on the anytime crewneck sleeve length? I'm working on a V neck version" → {"command": "VOC", "timeframe": "L30", "filters": {"product": "anytime crewneck"}, "confidence": "high", "reasoning": "Product feedback question about Anytime Crewneck, VOC with product filter"}
- "do customers mention body length on the jetsetter pant?" → {"command": "VOC", "timeframe": "L30", "filters": {"product": "jetsetter pant"}, "confidence": "high", "reasoning": "Product-specific feedback question"}
"""


def route_natural_language(text: str) -> dict:
    """
    Use LLM to classify a natural language query into a Scout command.
    Returns a dict with keys: command, timeframe, filters, confidence, reasoning.
    Falls back to keyword routing on failure.
    """
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        logger.info(f"LLM routed '{text}' → {result}")
        return result
    except Exception as e:
        logger.warning(f"LLM routing failed for '{text}': {e}. Falling back to keyword routing.")
        return _keyword_fallback(text)


def _keyword_fallback(text: str) -> dict:
    """Simple keyword-based fallback router."""
    text_lower = text.lower()

    if any(kw in text_lower for kw in ["nps", "promoter", "detractor", "survey"]):
        return {"command": "NPS", "timeframe": "L30", "filters": {}, "confidence": "medium", "reasoning": "keyword: nps/promoter"}

    if any(kw in text_lower for kw in ["return", "refund", "exchange"]):
        return {"command": "Returns", "timeframe": "L30", "filters": {}, "confidence": "medium", "reasoning": "keyword: return/refund"}

    if any(kw in text_lower for kw in ["review", "star rating", "okendo"]):
        return {"command": "Reviews", "timeframe": "L30", "filters": {}, "confidence": "medium", "reasoning": "keyword: review"}

    if any(kw in text_lower for kw in ["csat", "satisfaction", "customer sat"]):
        timeframe = "L30" if "month" in text_lower or "30" in text_lower else "L7"
        return {"command": "CSAT", "timeframe": timeframe, "filters": {}, "confidence": "medium", "reasoning": "keyword: csat"}

    if any(kw in text_lower for kw in ["error", "bug", "broken", "checkout", "payment", "discount code"]):
        return {"command": "Errors", "timeframe": "L7", "filters": {}, "confidence": "medium", "reasoning": "keyword: error/bug"}

    if any(kw in text_lower for kw in ["help", "how do", "what can", "commands"]):
        return {"command": "Help", "timeframe": "L7", "filters": {}, "confidence": "high", "reasoning": "keyword: help"}

    # Default to VOC
    timeframe = "L30" if "month" in text_lower or "30" in text_lower else "L7"
    return {"command": "VOC", "timeframe": timeframe, "filters": {}, "confidence": "low", "reasoning": "default: VOC"}


def build_command_from_routing(routing: dict) -> str:
    """Convert a routing dict into a Scout command string."""
    command = routing.get("command", "VOC")
    timeframe = routing.get("timeframe", "L7")
    filters = routing.get("filters", {})

    parts = [f"/{command}", timeframe]
    for key, value in filters.items():
        if " " in str(value):
            parts.append(f'{key}:"{value}"')
        else:
            parts.append(f"{key}:{value}")

    return " ".join(parts)
