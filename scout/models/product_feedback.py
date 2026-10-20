"""
Typed models for the product-feedback pipeline.

These are pure data containers with no business logic.

Models:
  ProductFeedbackRequest  — inbound request
  ProductFeedbackScope    — candidate conversation scope (IDs + metadata)
  ProductFeedbackTheme    — a single synthesised theme
  ProductFeedbackEvidence — a cleaned customer message
  ProductFeedbackResult   — full pipeline output
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from scout.taxonomy.products import ProductResolution

BUSINESS_TIMEZONE = "America/New_York"

# Retrieval passes, narrowest first. A scope's ``retrieval_mode`` names the
# widest pass that contributed conversations.
STRICT = "strict"
BODY_ONLY = "fallback_body_only"
EMPTY = "empty"
RETRIEVAL_PASSES = (STRICT, BODY_ONLY)

# Product-attribution tiers for one piece of customer evidence. These are
# evidence categories, not probabilities. Routing confidence is separate.
CONFIRMED = "confirmed"        # the customer named the product, or wrote a subject naming it
ORDER_LINKED = "order_linked"  # a referenced order resolves this specific item
INFERRED = "inferred"          # verified purchase history points to one plausible item
UNRESOLVED = "unresolved"      # several plausible items, conflicts, or not enough to attribute
ATTRIBUTION_TIERS = (CONFIRMED, ORDER_LINKED, INFERRED, UNRESOLVED)
# Evidence about a different product. Not one of the tiers above; kept out of this product's evidence.
OTHER_PRODUCT = "other_product"

# Tiers that count as product evidence for themes by default.
PRODUCT_EVIDENCE_TIERS = (CONFIRMED, ORDER_LINKED)


@dataclass(frozen=True)
class ProductAttribution:
    """How one customer message was connected (or not) to the product.

    ``method`` names the rule that decided the tier, and ``basis`` explains
    it in words. Identifiers are Richpanel conversation/message IDs and
    Shopify order/line IDs; customer emails are never stored here.
    """

    message_id: str
    conversation_id: str
    tier: str
    method: str
    basis: str
    product: Optional[str] = None
    candidates: Tuple[str, ...] = ()
    order_ids: Tuple[str, ...] = ()
    order_line_ids: Tuple[str, ...] = ()
    message_created_at: Optional[str] = None
    ticket_created_at: Optional[str] = None
    customer_identity_verified: bool = False

    def to_dict(self) -> Dict[str, object]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "tier": self.tier,
            "method": self.method,
            "basis": self.basis,
            "product": self.product,
            "candidates": list(self.candidates),
            "order_ids": list(self.order_ids),
            "order_line_ids": list(self.order_line_ids),
            "message_created_at": self.message_created_at,
            "ticket_created_at": self.ticket_created_at,
            "customer_identity_verified": self.customer_identity_verified,
        }


@dataclass(frozen=True)
class ProductFeedbackRequest:
    """Inbound request describing what product feedback to retrieve.

    ``start`` (inclusive) and ``end`` (exclusive) are timezone-aware. When
    omitted, the service uses the last ``timeframe_days`` complete days in
    ``BUSINESS_TIMEZONE``, the same window as the slash parser.

    ``retrieval_ceiling`` pins the widest retrieval pass. The previous-period
    scope uses the current scope's mode so the two periods are comparable.
    """

    product_name: str
    timeframe_days: int = 30
    tag_groups: tuple[str, ...] = ("product_feedback",)
    product_aliases: tuple[str, ...] = ()
    min_sample_size: int = 5
    max_messages: int = 200
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    timezone: str = BUSINESS_TIMEZONE
    filters: Dict[str, str] = field(default_factory=dict)
    resolution: Optional[ProductResolution] = None
    retrieval_ceiling: Optional[str] = None
    topic: Optional[str] = None
    period: str = "current"
    max_conversations: int = 500


@dataclass(frozen=True)
class ProductFeedbackScope:
    """Candidate conversation scope produced by scope-discovery step.

    This is the authoritative set of conversation IDs that should be used
    for ALL downstream queries (VOC aggregation, message fetching, synthesis).
    Passing this scope into voc.py ensures metrics and synthesis are consistent.

    The scope records how the product was resolved, the explicit window and
    timezone, and the filters applied, so metrics and synthesis can state
    the population they describe. The previous period has its own scope.
    """

    product_name: str
    conversation_ids: List[str]
    aliases_used: List[str]
    tag_groups_used: List[str]
    retrieval_mode: str  # "strict", "fallback_body_only", "empty"
    timeframe_days: int
    caveats: List[str] = field(default_factory=list)
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    timezone: str = BUSINESS_TIMEZONE
    filters: Dict[str, str] = field(default_factory=dict)
    resolution: Optional[ProductResolution] = None
    topic: Optional[str] = None
    period: str = "current"
    capped: bool = False
    max_conversations: int = 500
    attribution_tiers: Tuple[str, ...] = PRODUCT_EVIDENCE_TIERS

    @property
    def is_empty(self) -> bool:
        return len(self.conversation_ids) == 0

    @property
    def total_conversations(self) -> int:
        return len(self.conversation_ids)

    @property
    def product_level(self) -> Optional[str]:
        return self.resolution.level if self.resolution else None

    def summary(self) -> Dict[str, object]:
        """Plain-dict description of the population, for formatters and logs."""
        return {
            "period": self.period,
            "product_name": self.product_name,
            "product_level": self.product_level,
            "family": self.resolution.family if self.resolution else None,
            "styles": list(self.resolution.styles) if self.resolution else [],
            "match_method": self.resolution.match_method if self.resolution else "",
            "catalog_source": self.resolution.catalog_source if self.resolution else "",
            "catalog_available": self.resolution.catalog_available if self.resolution else False,
            "seasons": list(self.resolution.seasons) if self.resolution else [],
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "timezone": self.timezone,
            "filters": dict(self.filters),
            "topic": self.topic,
            "retrieval_mode": self.retrieval_mode,
            "tag_groups": list(self.tag_groups_used),
            "match_phrases": list(self.aliases_used),
            "conversation_count": self.total_conversations,
            "capped": self.capped,
            "max_conversations": self.max_conversations,
            "attribution_tiers": list(self.attribution_tiers),
        }


@dataclass(frozen=True)
class ProductFeedbackTheme:
    """A single synthesised theme from customer messages.

    ``mention_count`` is the number of distinct tickets among the cited
    evidence, computed in code from ``evidence_message_ids``. The quote is
    kept only when it appears verbatim in a cited message.
    """

    label: str
    mention_count: int
    representative_quote: Optional[str] = None
    message_count: int = 0
    evidence_message_ids: Tuple[str, ...] = ()
    conversation_ids: Tuple[str, ...] = ()
    quote_message_id: Optional[str] = None


@dataclass(frozen=True)
class ProductFeedbackEvidence:
    """A cleaned customer message used as evidence."""

    conversation_id: str
    message_id: str
    body: str
    author_id: str
    created_at: Optional[str] = None


@dataclass
class ProductFeedbackResult:
    """Output of the product-feedback pipeline.

    ``total_messages_analysed`` is the number of messages sent to synthesis.
    ``retrieved_message_count`` is what the repository returned before
    cleaning; ``cleaned_message_count`` is what survived cleaning.
    """

    product_name: str
    timeframe_label: str
    total_candidate_conversations: int
    total_messages_analysed: int
    retrieval_mode: str  # "strict", "fallback_body_only", "empty"
    themes: List[ProductFeedbackTheme] = field(default_factory=list)
    representative_examples: List[str] = field(default_factory=list)
    headline: str = ""
    so_what: str = ""
    recommended_action: str = ""
    caveats: List[str] = field(default_factory=list)
    source: str = "Richpanel tickets via Snowflake"
    candidate_conversation_ids: List[str] = field(default_factory=list)
    retrieved_message_count: int = 0
    cleaned_message_count: int = 0
    analysed_message_ids: List[str] = field(default_factory=list)
    low_sample: bool = False
    retrieval_capped: bool = False
    topic: Optional[str] = None
    attributions: List[ProductAttribution] = field(default_factory=list)
    inferred_attributions: List[ProductAttribution] = field(default_factory=list)
    evidence_stats: Dict[str, int] = field(default_factory=dict)
    order_data: Dict[str, object] = field(default_factory=dict)
    unlinked_theme_count: int = 0
