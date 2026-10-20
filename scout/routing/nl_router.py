"""
Scout Natural Language Router
Uses an LLM to parse free-form user questions into structured Scout commands.
Falls back to keyword-based routing if the LLM is unavailable.

Both paths end in ``_finalise``, which validates the command, keeps an
explicit timeframe from the question, and keeps the product and topic. The
product comes from the product catalog when the question names a catalog
product, so a product question still carries its product when the model
call fails or the model leaves the product out.
"""

import json
import logging
import re
from typing import Callable, Dict, List, Optional

from scout.llm import MODEL, get_client
from scout.services.product_catalog_service import get_catalog
from scout.taxonomy.products import (
    FAMILY,
    PHRASE,
    STYLE,
    ProductCatalog,
    find_product_mentions,
    resolve_product,
)

logger = logging.getLogger("scout.nl_router")

SYSTEM_PROMPT = """You are Scout's intent classifier. Your job is to map a user's natural language question into a structured Scout command.

Scout supports these commands:
- CSAT: questions about customer satisfaction scores, CSAT %, ratings, satisfaction trends
- VOC: questions about voice of customer, top themes, contact reasons, ticket volume, what customers are saying, product feedback, customer comments about a specific product, design feedback, fit/sizing feedback
- Errors: questions about bugs, errors, broken features, checkout issues, payment issues, discount code failures, site issues
- NPS: questions about Net Promoter Score, customer loyalty, promoters, detractors, NPS themes, why people rate us low/high, NPS score for a period. Jack Archer runs 3 NPS-type surveys (NPS Survey, Returning Customer PPS, New Customer PPS) — all are shown together in the NPS report.
- PPS: questions specifically about why returning customers came back, what almost stopped them from purchasing, what returning customers want to see, returning customer survey insights. Use filters: {"segment": "returning"}. Also use PPS with filters: {"segment": "new"} for the new-customer PPS report: new customer PPS, new-customer post-purchase feedback, and new customer open-text that isn't about how they found us.
- Attribution: questions about how customers found us, acquisition channels, where new customers come from, what brought them to the site, consideration window (how long they knew us before buying), who they bought for. This is the New Customer PPS attribution breakdown. Keep these on Attribution even if the user says "new customer survey".
- Returns: questions about returns, refunds, exchanges, return reasons, return rates
- Reviews: questions about product reviews, star ratings, review sentiment
- Help: user is asking how to use Scout or what commands are available

Supported timeframes: L7 (7 days), L30 (30 days), L180 (180 days). Default to L30 if not specified (most queries benefit from a broader window). If the user states an explicit window such as "last 90 days" or "past 6 months", use L<number of days> (L90, L180).

IMPORTANT RULES:
1. If the user asks about a SPECIFIC PRODUCT or product family (e.g. "Anytime Crewneck", "Jetsetter Pant", "Everyday Hoodie", "Jetsetter"), always include a "product" key in filters with the product name exactly as the user wrote it. Never shorten it to one word.
2. Questions about what customers are saying, comments, callouts, feedback, complaints, fit, sizing, length, quality about a product → VOC with product filter.
3. If the question names an aspect of the product (fit, sizing, sleeve length, inseam, waistband, fabric, quality, color, durability), add a "topic" key in filters with that aspect.
4. Confidence should be "high" when intent is clear (even if the message is long/conversational). Only use "low" when you genuinely cannot determine the intent.
5. For long conversational messages, focus on the CORE QUESTION being asked, not the surrounding context.
6. "How are people finding us" or "acquisition" or "where are customers coming from" → Attribution (not NPS or VOC).
7. "Why do customers come back" or "what made them return" or "returning customer survey" → PPS with segment:returning.
8. "NPS themes", "why are people rating us low", "what are detractors saying" → NPS (not VOC).
9. "New customer PPS" or new-customer post-purchase feedback that is not about acquisition → PPS with segment:new. "How are people finding us", "acquisition channel", "how did you hear", and "new customer survey" → Attribution, not PPS.

Respond with ONLY a JSON object in this exact format:
{
  "command": "CSAT" | "VOC" | "Errors" | "NPS" | "PPS" | "Attribution" | "Returns" | "Reviews" | "Help",
  "timeframe": "L7" | "L30" | "L180" | "L<days>",
  "filters": {},
  "confidence": "high" | "medium" | "low",
  "reasoning": "brief explanation"
}

Examples:
- "what's our CSAT this week?" → {"command": "CSAT", "timeframe": "L7", "filters": {}, "confidence": "high", "reasoning": "CSAT question, this week = L7"}
- "show me top customer complaints last month" → {"command": "VOC", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "VOC/complaints question, last month = L30"}
- "are there any checkout errors?" → {"command": "Errors", "timeframe": "L7", "filters": {}, "confidence": "high", "reasoning": "Error/checkout question, default L7"}
- "what are customers saying about the hoodie?" → {"command": "VOC", "timeframe": "L30", "filters": {"product": "hoodie"}, "confidence": "high", "reasoning": "VOC question with product filter"}
- "what's our NPS this month?" → {"command": "NPS", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "NPS score question"}
- "what are the NPS themes?" → {"command": "NPS", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "NPS theme analysis"}
- "why are people rating us low?" → {"command": "NPS", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "NPS detractor themes question"}
- "how are people finding us past 30 days?" → {"command": "Attribution", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "Attribution/acquisition channel question"}
- "what's our top acquisition channel?" → {"command": "Attribution", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "Attribution question"}
- "why do customers come back?" → {"command": "PPS", "timeframe": "L30", "filters": {"segment": "returning"}, "confidence": "high", "reasoning": "Returning customer PPS question"}
- "what almost stopped customers from buying?" → {"command": "PPS", "timeframe": "L30", "filters": {"segment": "returning"}, "confidence": "high", "reasoning": "Returning customer PPS friction question"}
- "show me new customer PPS" → {"command": "PPS", "timeframe": "L30", "filters": {"segment": "new"}, "confidence": "high", "reasoning": "New customer PPS report, not attribution"}
- "what are new customers saying in the post-purchase survey?" → {"command": "PPS", "timeframe": "L30", "filters": {"segment": "new"}, "confidence": "high", "reasoning": "New customer PPS open-text, not acquisition"}
- "Are you able to let me know if we get comments or callouts on the anytime crewneck sleeve length? I'm working on a V neck version" → {"command": "VOC", "timeframe": "L30", "filters": {"product": "anytime crewneck", "topic": "sleeve length"}, "confidence": "high", "reasoning": "Product feedback question about Anytime Crewneck sleeve length, VOC with product filter"}
- "do customers mention body length on the jetsetter pant over the past 6 months?" → {"command": "VOC", "timeframe": "L180", "filters": {"product": "jetsetter pant", "topic": "body length"}, "confidence": "high", "reasoning": "Product-specific feedback question, 6 months = L180"}
"""

ALLOWED_COMMANDS = ("CSAT", "VOC", "Errors", "NPS", "PPS", "Attribution", "Returns", "Reviews", "Help")
_COMMAND_LOOKUP = {c.lower(): c for c in ALLOWED_COMMANDS}

# Commands whose query accepts a product filter.
PRODUCT_COMMANDS = ("VOC", "Returns", "Reviews")

_FILTER_KEYS = ("product", "segment", "channel", "tag", "topic")
_CONFIDENCE = ("high", "medium", "low")

CatalogProvider = Callable[[], ProductCatalog]


def route_natural_language(text: str, catalog_provider: Optional[CatalogProvider] = None) -> dict:
    """
    Use LLM to classify a natural language query into a Scout command.
    Returns a dict with keys: command, timeframe, filters, confidence, reasoning, router.
    Falls back to keyword routing on failure or on output that fails validation.

    This is the plug-in point for a future question-type router. Replace
    this function; do not change slash-command dispatch. See
    docs/architecture/question-router.md.
    """
    catalog = _LazyCatalog(catalog_provider or get_catalog)
    try:
        client = get_client()
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content or "")
        result = _finalise(_validate_llm_output(raw), text, catalog, router="llm")
        logger.info(f"LLM routed '{text}' → {result}")
        return result
    except Exception as e:
        logger.warning(f"LLM routing failed for '{text}': {e}. Falling back to keyword routing.")
        return _keyword_fallback(text, catalog)


class _LazyCatalog:
    """Loads the catalog on first use, so questions without a product never touch Snowflake."""

    def __init__(self, provider: CatalogProvider):
        self._provider = provider
        self._catalog: Optional[ProductCatalog] = None

    def get(self) -> ProductCatalog:
        if self._catalog is None:
            self._catalog = self._provider()
        return self._catalog


def _validate_llm_output(raw: object) -> dict:
    """Normalise model JSON. Raises ValueError when the command is unusable."""
    if not isinstance(raw, dict):
        raise ValueError("routing output is not a JSON object")
    command = _COMMAND_LOOKUP.get(str(raw.get("command", "")).strip().lower())
    if command is None:
        raise ValueError(f"unknown command {raw.get('command')!r}")
    raw_filters = raw.get("filters")
    filters: Dict[str, str] = {}
    if isinstance(raw_filters, dict):
        for key in _FILTER_KEYS:
            value = raw_filters.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                filters[key] = str(value).strip()
    confidence = str(raw.get("confidence", "")).lower()
    return {
        "command": command,
        "timeframe": _valid_timeframe(raw.get("timeframe")),
        "filters": filters,
        "confidence": confidence if confidence in _CONFIDENCE else "medium",
        "reasoning": str(raw.get("reasoning", "")),
    }


def _valid_timeframe(value: object) -> Optional[str]:
    match = re.fullmatch(r"[Ll](\d+)", str(value or "").strip())
    if match and int(match.group(1)) > 0:
        return f"L{int(match.group(1))}"
    return None


def _default_timeframe(command: str) -> str:
    return "L7" if command in ("Errors", "Help") else "L30"


# ---------------------------------------------------------------------------
# Timeframe, product, and topic extraction from the question text
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fourteen": 14,
    "thirty": 30, "sixty": 60, "ninety": 90,
}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}
_NUM = r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")"


def extract_timeframe(text: str) -> Optional[str]:
    """An explicit window in the question, as ``L<days>``, or None."""
    t = text.lower()
    match = re.search(r"\bl(\d+)\b", t)
    if match and int(match.group(1)) > 0:
        return f"L{int(match.group(1))}"
    match = re.search(_NUM + r"[\s-]+(day|week|month|year)s?\b", t)
    if match:
        raw = match.group(1)
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        if n > 0:
            return f"L{n * _UNIT_DAYS[match.group(2)]}"
    if re.search(r"\bhalf[\s-]+(?:a\s+)?year\b", t):
        return "L180"
    if re.search(r"\b(?:this|last|past|previous|prior)\s+quarter\b|\bquarterly\b", t):
        return "L90"
    if re.search(r"\b(?:this|last|past|previous|prior)\s+year\b|\bytd\b|\byear to date\b", t):
        return "L365"
    if re.search(r"\b(?:this|last|past|previous|prior)\s+month\b|\bmonthly\b|\ba month\b", t):
        return "L30"
    if re.search(r"\b(?:this|last|past|previous|prior)\s+week\b|\bweekly\b|\ba week\b", t):
        return "L7"
    return None


_TOPIC_TERMS = (
    "sleeve length", "body length", "inseam", "waistband", "rise", "fit", "sizing", "size",
    "length", "fabric", "material", "quality", "durability", "pilling", "shrinkage",
    "shrink", "color", "colour", "comfort", "stretch", "pocket", "wrinkle", "breathability",
    "warmth", "thickness", "stitching", "seam", "zipper", "button", "price", "value",
)


def extract_topic(text: str) -> Optional[str]:
    """Product aspects the question asks about ("fit", "sleeve length"), or None."""
    t = text.lower()
    found: List[tuple] = []
    for term in sorted(_TOPIC_TERMS, key=len, reverse=True):
        for match in re.finditer(r"\b" + re.escape(term) + r"s?\b", t):
            span = match.span()
            if not any(s < span[1] and span[0] < e for s, e, _ in found):
                found.append((span[0], span[1], term))
    if not found:
        return None
    terms: List[str] = []
    for _, _, term in sorted(found):
        if term not in terms:
            terms.append(term)
    return ", ".join(terms[:3])


def _product_mentions(text: str, catalog: _LazyCatalog):
    return find_product_mentions(text, catalog.get())


def _canonical_product(name: str, catalog: _LazyCatalog) -> str:
    resolution = resolve_product(name, catalog.get())
    if resolution.is_resolved and resolution.level in (STYLE, FAMILY) and resolution.canonical_name:
        return resolution.canonical_name
    return name


def _finalise(routing: dict, text: str, catalog: _LazyCatalog, router: str) -> dict:
    """Apply the shared contract to a routing dict from either path.

    - An explicit window in the question wins; otherwise the routed value,
      then the per-command default.
    - Product-capable commands keep a product named in the question. A
      catalog match wins over the model's string; a product-like phrase is
      kept when the model gave none.
    - CSAT about a product becomes product VOC, which reports CSAT for that
      product's tickets instead of dropping the product.
    """
    command = routing["command"]
    filters = dict(routing.get("filters") or {})
    notes: List[str] = []

    timeframe = extract_timeframe(text) or routing.get("timeframe") or _default_timeframe(command)

    if command in PRODUCT_COMMANDS or command == "CSAT":
        mentions = _product_mentions(text, catalog)
        product = filters.get("product")
        catalog_mentions = [m for m in mentions if m.level != PHRASE]
        if catalog_mentions:
            if not product or _differs(product, catalog_mentions[0].name, catalog):
                notes.append(f"product from catalog: {catalog_mentions[0].name}")
            product = catalog_mentions[0].name
        elif not product and mentions:
            product = mentions[0].name
            notes.append(f"product phrase from text: {product}")
        if len({m.name for m in mentions}) > 1:
            notes.append("several products named; scoped to the first")
        if product:
            filters["product"] = _canonical_product(product, catalog)
            if command == "CSAT":
                command = "VOC"
                notes.append("CSAT for a product comes from product-scoped VOC")
    else:
        filters.pop("product", None)

    if command == "VOC" and filters.get("product"):
        topic = filters.get("topic") or extract_topic(text)
        if topic:
            filters["topic"] = topic
    else:
        filters.pop("topic", None)

    reasoning = routing.get("reasoning", "")
    if notes:
        reasoning = f"{reasoning}; " if reasoning else ""
        reasoning += "; ".join(notes)

    return {
        "command": command,
        "timeframe": timeframe,
        "filters": filters,
        "confidence": routing.get("confidence", "medium"),
        "reasoning": reasoning,
        "router": router,
    }


def _differs(product: str, catalog_name: str, catalog: _LazyCatalog) -> bool:
    return _canonical_product(product, catalog).lower() != catalog_name.lower()


_RETURNS_RE = re.compile(r"\b(?:returns?|returned|refunds?|refunded|exchanges?|exchanged)\b")


def _keyword_fallback(text: str, catalog: Optional[_LazyCatalog] = None) -> dict:
    """Keyword-based fallback router.

    Keeps the product, topic, and explicit timeframe through ``_finalise``.
    """
    catalog = catalog or _LazyCatalog(get_catalog)
    text_lower = text.lower()

    def done(command: str, confidence: str, reasoning: str, filters: Optional[dict] = None) -> dict:
        routing = {
            "command": command,
            "timeframe": None,
            "filters": dict(filters or {}),
            "confidence": confidence,
            "reasoning": reasoning,
        }
        return _finalise(routing, text, catalog, router="keyword_fallback")

    # New-customer PPS before attribution. "new customer survey" stays attribution.
    new_pps_phrases = (
        "new customer pps", "new customers pps", "new-customer pps", "pps new",
        "pps_new", "pps for new", "new customer post-purchase", "new customer post purchase",
    )
    attribution_phrases = (
        "how are people finding", "attribution", "acquisition channel",
        "where are customers coming from", "how did you hear",
        "new customer survey", "first time buyer", "first-time buyer",
    )
    is_new_pps = any(phrase in text_lower for phrase in new_pps_phrases)
    is_attribution = any(phrase in text_lower for phrase in attribution_phrases)
    if is_new_pps and not is_attribution:
        return done("PPS", "medium", "keyword: pps new", {"segment": "new"})

    # Attribution (check before NPS — "survey" alone should not trigger attribution)
    if is_attribution:
        return done("Attribution", "medium", "keyword: attribution")

    # PPS returning (check before NPS)
    if any(kw in text_lower for kw in ["why do people come back", "why did they come back",
                                        "why do customers come back",
                                        "what made customers come back", "returning customer survey",
                                        "what almost stopped", "pps returning"]):
        return done("PPS", "medium", "keyword: pps returning", {"segment": "returning"})

    if any(kw in text_lower for kw in ["nps", "promoter", "detractor", "net promoter", "customer loyalty"]):
        return done("NPS", "medium", "keyword: nps/promoter")

    # "returning customers" is not a returns question.
    if _RETURNS_RE.search(text_lower):
        return done("Returns", "medium", "keyword: return/refund")

    if any(kw in text_lower for kw in ["review", "star rating", "okendo"]):
        return done("Reviews", "medium", "keyword: review")

    if any(kw in text_lower for kw in ["csat", "satisfaction", "customer sat"]):
        return done("CSAT", "medium", "keyword: csat")

    # A named product is product feedback, ahead of the error/help keywords
    # ("how do customers like the Atlas Short" is not a help request).
    if _product_mentions(text, catalog):
        return done("VOC", "medium", "keyword: product mention")

    if any(kw in text_lower for kw in ["error", "bug", "broken", "checkout", "payment", "discount code"]):
        return done("Errors", "medium", "keyword: error/bug")

    if any(kw in text_lower for kw in ["help", "how do", "what can", "commands"]):
        return done("Help", "high", "keyword: help")

    # Default to VOC
    return done("VOC", "low", "default: VOC")


def build_command_from_routing(routing: dict) -> str:
    """Convert a routing dict into a Scout command string."""
    command = routing.get("command", "VOC")
    timeframe = routing.get("timeframe") or "L7"
    filters = routing.get("filters") or {}

    parts = [f"/{command}", timeframe]
    for key, value in filters.items():
        text = str(value).replace('"', "").strip()
        if not text:
            continue
        if " " in text:
            parts.append(f'{key}:"{text}"')
        else:
            parts.append(f"{key}:{text}")

    return " ".join(parts)
