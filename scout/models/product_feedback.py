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
from typing import Dict, List, Optional

from scout.taxonomy.products import ProductResolution

BUSINESS_TIMEZONE = "America/New_York"

# Retrieval passes, narrowest first. A scope's ``retrieval_mode`` names the
# widest pass that contributed conversations.
STRICT = "strict"
BODY_ONLY = "fallback_body_only"
EMPTY = "empty"
RETRIEVAL_PASSES = (STRICT, BODY_ONLY)


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
        }


@dataclass(frozen=True)
class ProductFeedbackTheme:
    """A single synthesised theme from customer messages."""

    label: str
    mention_count: int
    representative_quote: Optional[str] = None


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
