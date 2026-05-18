"""
Typed models for the product-feedback pipeline.

These are pure data containers with no business logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class ProductFeedbackRequest:
    """Inbound request describing what product feedback to retrieve."""

    product_name: str
    timeframe_days: int = 30
    tag_groups: tuple[str, ...] = ("product_feedback",)
    product_aliases: tuple[str, ...] = ()
    min_sample_size: int = 5
    max_messages: int = 200


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
    """Output of the product-feedback pipeline."""

    product_name: str
    timeframe_label: str
    total_candidate_conversations: int
    total_messages_analysed: int
    retrieval_mode: str  # "strict", "fallback_relaxed", "fallback_tag_only", "fallback_body_only"
    themes: List[ProductFeedbackTheme] = field(default_factory=list)
    representative_examples: List[str] = field(default_factory=list)
    headline: str = ""
    so_what: str = ""
    recommended_action: str = ""
    caveats: List[str] = field(default_factory=list)
    source: str = "Richpanel tickets via Snowflake"
    candidate_conversation_ids: List[str] = field(default_factory=list)
