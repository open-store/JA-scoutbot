"""
Evidence service — message cleaning and ranking.

Responsible for:
  - Stripping HTML from message bodies
  - Removing agent signatures, CSAT survey templates, quoted replies
  - Removing agent text a customer quoted back in their reply
  - Filtering out auto-reply content in message bodies
  - Removing empty / too-short messages after cleaning
  - Deduplicating repeated messages, quoted chains, and near-copies
  - Ranking messages by relevance to the product query
  - Scrubbing personal details from quotes before display
"""
from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

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
    re.compile(r"\n?\s*sent from my (?:iphone|ipad|android|samsung|galaxy|mobile device)[^\n]*", re.I),
    re.compile(r"\n?\s*get outlook for (?:ios|android)[^\n]*", re.I),
]

# CSAT survey template
_CSAT_SURVEY_RE = re.compile(
    r"we\s+value\s+your\s+feedback.*?(?:sincerely|jack\s+archer)",
    re.I | re.S,
)

# Quoted reply indicators
_QUOTED_REPLY_PATTERNS = [
    re.compile(r"on\s+\w+\s+\d+.*?wrote:.*", re.I | re.S),
    # Gmail / Apple Mail: "On Mon, Jan 5, 2026 at 10:00 AM Jack Archer <...> wrote:"
    re.compile(r"(?:^|\n)\s*on\s[^\n]{0,200}?(?:\n[^\n]{0,120}?)?\bwrote:.*", re.I | re.S),
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

# Personal details removed from displayed quotes
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")
_ORDER_REF_RE = re.compile(r"#\s?\d{3,}")
_LONG_NUMBER_RE = re.compile(r"\b\d{6,}\b")

# Agent sentences shorter than this are too generic to subtract safely.
_MIN_AGENT_SENTENCE = 30


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


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def strip_agent_text(body: str, agent_bodies: Iterable[str]) -> Tuple[str, bool]:
    """Remove sentences that also appear in an agent message of the same conversation.

    Customers often reply above, or paste, the agent's text, including
    product recommendations. That text is not customer testimony. Returns
    (remaining text, whether anything was removed).
    """
    agent_sentences = {
        _norm(s) for b in agent_bodies for s in _sentences(clean_message_body(b))
        if len(s) >= _MIN_AGENT_SENTENCE
    }
    if not agent_sentences:
        return body, False
    kept: List[str] = []
    removed = False
    for sentence in _sentences(body):
        if len(sentence) >= _MIN_AGENT_SENTENCE and _norm(sentence) in agent_sentences:
            removed = True
            continue
        kept.append(sentence)
    return (" ".join(kept), removed) if removed else (body, False)


def is_autoreply_body(body: str) -> bool:
    """Check if a message body looks like an auto-reply."""
    for pat in _AUTOREPLY_BODY_PATTERNS:
        if pat.search(body):
            return True
    return False


def is_too_short(body: str, min_chars: int = 15) -> bool:
    """Check if a cleaned body is too short to be useful evidence."""
    return len(body.strip()) < min_chars


def scrub_personal_details(text: str) -> str:
    """Replace emails, phone numbers, order references, and long numbers for display."""
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    text = _ORDER_REF_RE.sub("[order]", text)
    text = _LONG_NUMBER_RE.sub("[number]", text)
    return text


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


def collapse_repeats(
    messages: List[Dict[str, Any]],
    body_key: str = "cleaned_body",
    author_key: str = "AUTHOR_ID",
    similarity: float = 0.9,
) -> Tuple[List[Dict[str, Any]], int]:
    """Collapse one customer's repeated or quoted-chain messages.

    For the same author, a message whose text is contained in another of
    their messages, or that is at least *similarity* alike, is dropped in
    favour of the longer one. Returns (kept messages, number dropped).
    """
    by_author: Dict[str, List[int]] = {}
    for idx, msg in enumerate(messages):
        author = str(msg.get(author_key) or "")
        if author:
            by_author.setdefault(author, []).append(idx)
    drop: set = set()
    for indexes in by_author.values():
        ordered = sorted(indexes, key=lambda i: len(messages[i].get(body_key, "")), reverse=True)
        for pos, i in enumerate(ordered):
            if i in drop:
                continue
            longer = _norm(messages[i].get(body_key, ""))
            for j in ordered[pos + 1:]:
                if j in drop:
                    continue
                shorter = _norm(messages[j].get(body_key, ""))
                if not shorter:
                    continue
                if shorter in longer:
                    drop.add(j)
                    continue
                matcher = difflib.SequenceMatcher(None, shorter, longer)
                if matcher.real_quick_ratio() >= similarity and matcher.ratio() >= similarity:
                    drop.add(j)
    kept = [m for i, m in enumerate(messages) if i not in drop]
    return kept, len(drop)


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

@dataclass
class EvidenceStats:
    """What the pipeline removed, for disclosure."""

    raw: int = 0
    autoreplies: int = 0
    too_short: int = 0
    agent_text_removed: int = 0
    exact_duplicates: int = 0
    repeats_collapsed: int = 0
    kept: int = 0

    def to_dict(self) -> Dict[str, int]:
        return dict(self.__dict__)


def process_evidence_with_stats(
    raw_messages: List[Dict[str, Any]],
    aliases: List[str],
    body_key: str = "BODY",
    max_output: int = 200,
    agent_messages: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], EvidenceStats]:
    """Clean → strip quoted agent text → filter → dedup → rank, with counts.

    ``agent_messages`` are non-customer messages from the same conversations.
    They are used only to recognise agent text quoted inside customer
    messages, never as evidence.
    """
    stats = EvidenceStats(raw=len(raw_messages))
    agent_by_conversation: Dict[str, List[str]] = {}
    for msg in agent_messages or []:
        agent_by_conversation.setdefault(str(msg.get("CONVERSATION_ID")), []).append(msg.get(body_key, "") or "")

    cleaned: List[Dict[str, Any]] = []
    for msg in raw_messages:
        raw_body = msg.get(body_key, "") or ""
        body = clean_message_body(raw_body)
        agent_bodies = agent_by_conversation.get(str(msg.get("CONVERSATION_ID")), [])
        body, removed = strip_agent_text(body, agent_bodies)
        if removed:
            stats.agent_text_removed += 1

        if is_autoreply_body(body):
            stats.autoreplies += 1
            continue

        if is_too_short(body):
            stats.too_short += 1
            continue

        enriched = dict(msg)
        enriched["cleaned_body"] = body
        cleaned.append(enriched)

    unique = deduplicate_messages(cleaned)
    stats.exact_duplicates = len(cleaned) - len(unique)
    unique, stats.repeats_collapsed = collapse_repeats(unique)

    ranked = rank_by_product_relevance(unique, aliases)[:max_output]
    stats.kept = len(ranked)
    logger.info("Evidence pipeline: %s", stats.to_dict())
    return ranked, stats


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
    messages, _ = process_evidence_with_stats(raw_messages, aliases, body_key=body_key, max_output=max_output)
    return messages
