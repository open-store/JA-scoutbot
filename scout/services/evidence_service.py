"""
Evidence service — message cleaning and ranking.

Responsible for:
  - Stripping HTML from message bodies
  - Removing agent signatures, CSAT survey templates, quoted replies
  - Filtering out auto-reply content in message bodies
  - Removing empty / too-short messages after cleaning
  - Deduplicating near-identical messages
  - Ranking messages by relevance to the product query
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("scout.services.evidence")

# ---------------------------------------------------------------------------
# Patterns to strip / detect
# ---------------------------------------------------------------------------

# HTML tag removal
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Common agent signature patterns
_SIGNATURE_PATTERNS = [
    re.compile(r"(?:thanks|thank you|best|warm regards|regards|cheers|sincerely),?\s*\n.*?jack\s*archer", re.I | re.S),
    re.compile(r"\n\s*(?:—|--|--)\s*\n.*", re.S),
    re.compile(r"\nthe jack archer team.*", re.I | re.S),
]

# CSAT survey template
_CSAT_SURVEY_RE = re.compile(
    r"we\s+value\s+your\s+feedback.*?(?:sincerely|jack\s+archer)",
    re.I | re.S,
)

# Quoted reply indicators
_QUOTED_REPLY_PATTERNS = [
    re.compile(r"on\s+\w+\s+\d+.*?wrote:.*", re.I | re.S),
    re.compile(r"from:.*?sent:.*?subject:.*", re.I | re.S),
    re.compile(r"^>.*$", re.M),
    re.compile(r"-{3,}\s*original\s+message\s*-{3,}.*", re.I | re.S),
    re.compile(r"-{3,}\s*forwarded\s+message\s*-{3,}.*", re.I | re.S),
]

# Auto-reply body patterns
_AUTOREPLY_BODY_PATTERNS = [
    re.compile(r"i\s+am\s+(?:currently\s+)?out\s+of\s+(?:the\s+)?office", re.I),
    re.compile(r"automatic\s+reply", re.I),
    re.compile(r"auto[\s-]?reply", re.I),
    re.compile(r"i\s+(?:will\s+be|am)\s+(?:away|on\s+vacation|out)", re.I),
    re.compile(r"this\s+is\s+an?\s+auto(?:mated|matic)\s+(?:response|message)", re.I),
]

# URL removal (after HTML stripping, residual URLs)
_URL_RE = re.compile(r"https?://\S+")

# Collapse whitespace
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


# ---------------------------------------------------------------------------
# Cleaning pipeline
# ---------------------------------------------------------------------------

def clean_message_body(body: str) -> str:
    """Apply the full cleaning pipeline to a single message body.

    Returns the cleaned text, which may be empty if the message was
    entirely noise.
    """
    if not body:
        return ""

    text = body

    # 1. Strip HTML tags
    text = _HTML_TAG_RE.sub(" ", text)

    # 2. Remove CSAT survey templates
    text = _CSAT_SURVEY_RE.sub("", text)

    # 3. Remove quoted replies
    for pat in _QUOTED_REPLY_PATTERNS:
        text = pat.sub("", text)

    # 4. Remove agent signatures
    for pat in _SIGNATURE_PATTERNS:
        text = pat.sub("", text)

    # 5. Remove residual URLs
    text = _URL_RE.sub("", text)

    # 6. Collapse whitespace
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = text.strip()

    return text


def is_autoreply_body(body: str) -> bool:
    """Check if a message body looks like an auto-reply."""
    for pat in _AUTOREPLY_BODY_PATTERNS:
        if pat.search(body):
            return True
    return False


def is_too_short(body: str, min_chars: int = 15) -> bool:
    """Check if a cleaned body is too short to be useful evidence."""
    return len(body.strip()) < min_chars


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _normalise_for_dedup(text: str) -> str:
    """Normalise text for near-duplicate detection."""
    return re.sub(r"\s+", " ", text.lower().strip())


def deduplicate_messages(
    messages: List[Dict[str, Any]],
    body_key: str = "cleaned_body",
) -> List[Dict[str, Any]]:
    """Remove near-duplicate messages (same normalised body).

    Keeps the first occurrence (most recent, since input is sorted DESC).
    """
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for msg in messages:
        norm = _normalise_for_dedup(msg.get(body_key, ""))
        if norm and norm not in seen:
            seen.add(norm)
            unique.append(msg)
    return unique


# ---------------------------------------------------------------------------
# Relevance ranking
# ---------------------------------------------------------------------------

def rank_by_product_relevance(
    messages: List[Dict[str, Any]],
    aliases: List[str],
    body_key: str = "cleaned_body",
) -> List[Dict[str, Any]]:
    """Rank messages by how many product aliases appear in the body.

    Messages that mention the product directly are ranked higher.
    Messages with no mention are still included but ranked last.
    """
    def score(msg: Dict[str, Any]) -> int:
        body_lower = msg.get(body_key, "").lower()
        return sum(1 for alias in aliases if alias.lower() in body_lower)

    return sorted(messages, key=score, reverse=True)


# ---------------------------------------------------------------------------
# Full evidence pipeline
# ---------------------------------------------------------------------------

def process_evidence(
    raw_messages: List[Dict[str, Any]],
    aliases: List[str],
    body_key: str = "BODY",
    max_output: int = 200,
) -> List[Dict[str, Any]]:
    """Run the full evidence pipeline: clean → filter → dedup → rank.

    Adds a ``cleaned_body`` key to each surviving message dict.

    Parameters
    ----------
    raw_messages : list of dicts from the repository (must have *body_key*)
    aliases : product aliases for relevance ranking
    max_output : cap on returned messages

    Returns
    -------
    List of cleaned, filtered, deduplicated, ranked message dicts.
    """
    cleaned: List[Dict[str, Any]] = []
    autoreply_count = 0
    short_count = 0

    for msg in raw_messages:
        raw_body = msg.get(body_key, "") or ""
        body = clean_message_body(raw_body)

        if is_autoreply_body(body):
            autoreply_count += 1
            continue

        if is_too_short(body):
            short_count += 1
            continue

        enriched = dict(msg)
        enriched["cleaned_body"] = body
        cleaned.append(enriched)

    logger.info(
        "Evidence pipeline: %d raw → %d cleaned (removed %d auto-replies, %d too-short)",
        len(raw_messages), len(cleaned), autoreply_count, short_count,
    )

    # Deduplicate
    unique = deduplicate_messages(cleaned)
    logger.info("After dedup: %d unique messages", len(unique))

    # Rank by product relevance
    ranked = rank_by_product_relevance(unique, aliases)

    return ranked[:max_output]
