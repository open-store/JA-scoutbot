"""
Scout Product Feedback Synthesizer
When a VOC query includes a product filter, this module:
1. Pulls inbound customer messages from the MESSAGES table for matching conversations
2. Strips HTML from message bodies
3. Filters out auto-replies and marketing noise
4. Uses the LLM to synthesize the top qualitative themes from the messages

Overhaul (May 2026):
- Auto-reply exclusion applied to conversation and message queries
- Product matching uses broadened keyword (passed in from voc.py)
- Low-sample caveat flag added when < 5 real messages found
- Message cap increased to 100 for better synthesis quality
"""

import re
import logging
from openai import OpenAI

logger = logging.getLogger("scout.product_feedback")

# OpenAI client (uses OPENAI_API_KEY from environment)
_client = None

# Minimum message length to be considered substantive customer feedback
_MIN_MESSAGE_LENGTH = 40

# Low-sample threshold — below this, results are flagged as directional only
_LOW_SAMPLE_THRESHOLD = 5


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def strip_html(html: str) -> str:
    """
    Remove HTML tags and decode common entities from a message body.
    Returns clean plain text.
    """
    if not html:
        return ""
    # Remove style/script blocks
    text = re.sub(r"<(style|script)[^>]*>.*?</(style|script)>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block-level tags with newlines
    text = re.sub(r"<(br|p|div|li|tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"').replace("&#39;", "'")
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# Patterns that indicate an auto-reply, marketing blast reply, or noise message
_AUTOREPLY_PATTERNS = [
    r"automatic reply",
    r"autoreply",
    r"auto-reply",
    r"out of office",
    r"i am (currently )?out of",
    r"i('m| am) away",
    r"i will be (back|returning|out)",
    r"vacation",
    r"on leave",
    r"be back (on|in|by)",
    r"unsubscribe",
    r"stop$",                       # single-word unsubscribe reply
    r"^stop\b",
    r"this is an automated",
    r"do not reply to this",
    r"please do not reply",
    r"noreply",
    r"no-reply",
]

_AUTOREPLY_RE = re.compile("|".join(_AUTOREPLY_PATTERNS), re.IGNORECASE)


def _is_autoreply(text: str) -> bool:
    """Return True if the message body looks like an auto-reply or noise."""
    return bool(_AUTOREPLY_RE.search(text[:500]))


def synthesize_product_feedback(messages: list[str], product: str, conversation_count: int) -> dict:
    """
    Use the LLM to synthesize qualitative themes from a list of customer messages
    about a specific product.

    Args:
        messages: List of clean (HTML-stripped) customer message bodies
        product: The product name being analyzed (display name, not keyword)
        conversation_count: Total number of conversations (for context)

    Returns:
        dict with keys:
            themes (list of dicts with theme/count/example),
            summary (one-sentence overview),
            sample_count (number of messages analyzed),
            low_sample (bool — True when fewer than _LOW_SAMPLE_THRESHOLD messages)
    """
    low_sample = len(messages) < _LOW_SAMPLE_THRESHOLD

    if not messages:
        return {
            "themes": [],
            "summary": f"No substantive customer messages found for '{product}' in this period.",
            "sample_count": 0,
            "low_sample": True,
        }

    # Cap at 100 messages to stay within token limits while being representative
    sample = messages[:100]
    sample_count = len(sample)

    # Build the message corpus — truncate each message to 400 chars
    corpus = "\n---\n".join(
        f"[{i+1}] {msg[:400]}" for i, msg in enumerate(sample)
    )

    prompt = f"""You are analyzing {sample_count} customer support messages about the product: "{product}".
Total conversations in this period: {conversation_count}.

Customer messages:
{corpus}

Identify the top 5 qualitative themes or feedback patterns customers mention about this product.
For each theme:
- Give it a short descriptive name (e.g., "sleeve length too short", "sizing runs large", "fabric quality praised")
- Estimate how many of the {sample_count} messages mention it
- Quote the most representative short excerpt (max 100 chars)

Also write a 1-2 sentence executive summary of what customers are saying about this product.

Respond with ONLY a JSON object in this exact format:
{{
  "summary": "One to two sentence executive summary.",
  "themes": [
    {{"theme": "theme name", "count": 5, "example": "brief customer quote"}},
    ...
  ]
}}"""

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "You are a CX analyst synthesizing customer feedback. Be concise and specific. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=700,
            response_format={"type": "json_object"},
        )
        import json
        result = json.loads(response.choices[0].message.content)
        result["sample_count"] = sample_count
        result["low_sample"] = low_sample
        logger.info(f"Synthesized {sample_count} messages for '{product}': {len(result.get('themes', []))} themes (low_sample={low_sample})")
        return result
    except Exception as e:
        logger.error(f"LLM synthesis failed for '{product}': {e}")
        return {
            "themes": [],
            "summary": f"Could not synthesize feedback for '{product}' — LLM unavailable.",
            "sample_count": sample_count,
            "low_sample": low_sample,
        }


def get_product_messages(product_keyword: str, start: str, end: str, execute_query_fn) -> list[str]:
    """
    Pull inbound customer messages from the MESSAGES table for conversations
    matching the product keyword in the given time window.

    Uses a broadened keyword match (e.g. "clubhouse" instead of "clubhouse polo")
    and excludes auto-replies and marketing noise at both the SQL and Python level.

    Args:
        product_keyword: Broadened keyword to filter conversation subjects (e.g. "clubhouse")
        start: ISO datetime string for period start
        end: ISO datetime string for period end
        execute_query_fn: The execute_query_dict function from snowflake_client

    Returns:
        List of clean (HTML-stripped), non-autoreply message body strings
    """
    safe_keyword = product_keyword.replace("'", "''").lower()

    sql = f"""
    SELECT m.BODY
    FROM FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.MESSAGES m
    JOIN FIVETRAN_TEST_DATABASE.RICHPANEL_CONNECTOR.CONVERSATIONS c
      ON m.CONVERSATION_ID = c.ID
    WHERE c._FIVETRAN_DELETED = FALSE
      AND m._FIVETRAN_DELETED = FALSE
      AND c.CREATED_AT >= '{start}'
      AND c.CREATED_AT < '{end}'
      AND LOWER(c.SUBJECT) LIKE '%{safe_keyword}%'
      AND LOWER(c.SUBJECT) NOT LIKE '%automatic reply%'
      AND LOWER(c.SUBJECT) NOT LIKE '%autoreply%'
      AND LOWER(c.SUBJECT) NOT LIKE '%auto-reply%'
      AND LOWER(c.SUBJECT) NOT LIKE '%out of office%'
      AND LOWER(c.SUBJECT) NOT LIKE '%vacation%'
      AND LOWER(c.SUBJECT) NOT LIKE '%away from the office%'
      AND LOWER(c.SUBJECT) NOT LIKE '%unsubscribe%'
      AND m.BODY IS NOT NULL
      AND LENGTH(m.BODY) > 20
      AND m.AUTHOR_ID IS NOT NULL
    ORDER BY c.CREATED_AT DESC
    LIMIT 300
    """

    try:
        rows = execute_query_fn(sql)
        messages = []
        for row in rows:
            body = row.get("BODY", "") or ""
            clean = strip_html(body)
            # Skip very short messages (greetings, "thanks", etc.)
            if len(clean) < _MIN_MESSAGE_LENGTH:
                continue
            # Python-level auto-reply filter as a second pass
            if _is_autoreply(clean):
                continue
            messages.append(clean)
        logger.info(f"Product messages for '{product_keyword}': {len(rows)} raw rows → {len(messages)} after filtering")
        return messages
    except Exception as e:
        logger.error(f"Failed to fetch product messages for '{product_keyword}': {e}")
        return []
