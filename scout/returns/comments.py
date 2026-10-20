"""What customers mean in their Redo return comments.

Comments are scrubbed of personal details before they reach the model, and
repeats within one return are collapsed. The same words from two different
returns stay separate, because they are two customers' evidence. The model proposes
themes and cites comment numbers; ``_link_themes`` from the product-feedback
service keeps only themes with valid citations, keeps a quote only if it is
in a cited comment, and computes counts in code. Here a theme's count is the
number of distinct returns, since each comment's "conversation" is its
return. A theme from one return is an emerging signal, not a dominant theme.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Sequence

from scout.llm import MODEL, get_client
from scout.returns.metrics import MetricStatus
from scout.returns.reasons import ReturnItem, unique_items
from scout.returns.redo_fields import COMMENT_FIELDS, RedoField, missing_fields, unverified_reason
from scout.services.evidence_service import collapse_repeats, scrub_personal_details
from scout.services.product_feedback_service import _link_themes

logger = logging.getLogger("scout.returns.comments")

SYNTHESIS_MAX_COMMENTS = 150
MEANINGS = ("fit", "quality", "expectation_gap", "return_process", "other")

Synthesizer = Callable[[List[str], str], Mapping[str, Any]]


@dataclass(frozen=True)
class ReturnTheme:
    label: str
    meaning: str
    returns: int
    comments: int
    products: tuple[tuple[str, int], ...]
    quote: str | None
    quote_item_id: str | None
    evidence_item_ids: tuple[str, ...]

    @property
    def single_report(self) -> bool:
        return self.returns <= 1


@dataclass(frozen=True)
class CommentSynthesis:
    status: MetricStatus
    population: str
    themes: tuple[ReturnTheme, ...] = ()
    comments_in_scope: int = 0
    comments_analysed: int = 0
    duplicates_removed: int = 0
    themes_dropped: int = 0
    caveats: tuple[str, ...] = ()
    reason: str = ""

    @property
    def sample_based(self) -> bool:
        return self.comments_analysed < self.comments_in_scope


def unavailable_comment_synthesis(
    population: str,
    fields: dict[str, RedoField] | None = None,
) -> CommentSynthesis | None:
    missing = missing_fields(COMMENT_FIELDS, fields)
    if not missing:
        return None
    return CommentSynthesis(
        status=MetricStatus.UNAVAILABLE,
        population=population,
        reason=unverified_reason(missing),
    )


def _evidence(items: Sequence[ReturnItem]) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        text = " ".join((item.comment or "").split())
        if not text:
            continue
        out.append({
            "CONVERSATION_ID": str(item.return_id),
            "MESSAGE_ID": str(item.item_id),
            "AUTHOR_ID": str(item.return_id),
            "cleaned_body": scrub_personal_details(text),
            "product": item.product or "Unknown product",
        })
    return out


def synthesize_return_comments(
    items: Sequence[ReturnItem],
    population: str,
    synthesize: Synthesizer | None = None,
    max_comments: int = SYNTHESIS_MAX_COMMENTS,
) -> CommentSynthesis:
    """Themes from customer return comments, each tied to the comments that support it."""
    kept, _ = unique_items(items)
    evidence = _evidence(kept)
    unique, duplicates = collapse_repeats(evidence)
    analysed = unique[:max_comments]

    caveats: List[str] = []
    if not analysed:
        return CommentSynthesis(
            status=MetricStatus.PROVISIONAL,
            population=population,
            duplicates_removed=duplicates,
            caveats=("No customer comments in scope.",),
        )
    if len(unique) > len(analysed):
        caveats.append(
            f"Themes are sample-based: {len(analysed)} of {len(unique)} distinct comments were analysed."
        )

    run = synthesize or _synthesize_with_llm
    raw = run([m["cleaned_body"] for m in analysed], population)
    if not isinstance(raw, Mapping):
        raw = {"themes": [], "error": "output was not a JSON object"}
    raw_themes = raw.get("themes") or []
    if not isinstance(raw_themes, list):
        raw_themes = []
    if raw.get("error"):
        caveats.append(f"Comment synthesis unavailable ({raw['error']}).")

    meaning_by_label: Dict[str, str] = {}
    for t in raw_themes:
        if isinstance(t, Mapping):
            label = str(t.get("label", "")).strip()
            meaning = str(t.get("meaning", "")).strip().lower()
            meaning_by_label.setdefault(label, meaning if meaning in MEANINGS else "other")

    linked, dropped = _link_themes(raw_themes, analysed)
    if dropped:
        caveats.append(f"{dropped} theme(s) without valid comment references were dropped.")

    product_of = {m["MESSAGE_ID"]: m["product"] for m in analysed}
    themes = []
    for t in linked:
        products = Counter(product_of.get(i, "Unknown product") for i in t.evidence_message_ids)
        themes.append(ReturnTheme(
            label=t.label,
            meaning=meaning_by_label.get(t.label, "other"),
            returns=t.mention_count,
            comments=t.message_count,
            products=tuple(sorted(products.items(), key=lambda kv: (-kv[1], kv[0]))),
            quote=t.representative_quote,
            quote_item_id=t.quote_message_id,
            evidence_item_ids=t.evidence_message_ids,
        ))
    if any(t.single_report for t in themes):
        caveats.append("Themes from a single return are emerging signals, not dominant themes.")

    return CommentSynthesis(
        status=MetricStatus.PROVISIONAL,
        population=population,
        themes=tuple(themes),
        comments_in_scope=len(unique),
        comments_analysed=len(analysed),
        duplicates_removed=duplicates,
        themes_dropped=dropped,
        caveats=tuple(caveats),
    )


_SYSTEM_PROMPT = """You analyse customer return comments for a menswear brand.
You receive numbered comments that customers wrote when returning items.
Group them into themes that say what the customer means.

Return ONLY a JSON object:
{"themes": [{"label": "Short theme name", "meaning": "fit|quality|expectation_gap|return_process|other",
             "evidence": [comment numbers], "representative_quote": "Exact words from one cited comment"}]}

Rules:
- meaning is fit (size or cut), quality (defect, wear, material), expectation_gap (looked or felt
  different than expected), return_process (friction with the return itself), or other.
- evidence lists every comment that supports the theme, and only those.
- representative_quote is copied word for word from a cited comment.
- Do not invent themes. A theme cited by one comment is a single report.
- At most 7 themes."""


def _synthesize_with_llm(comments: List[str], population: str) -> Mapping[str, Any]:
    numbered = "\n".join(f"[{i + 1}] {c[:500]}" for i, c in enumerate(comments))
    try:
        resp = get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Population: {population}\n\nComments ({len(comments)}):\n\n{numbered}"},
            ],
            temperature=0.2,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        parsed = json.loads((resp.choices[0].message.content or "").strip())
        if not isinstance(parsed, dict):
            raise ValueError("synthesis output is not a JSON object")
        return parsed
    except Exception as e:  # noqa: BLE001 - any model or parsing failure becomes a caveat
        logger.error("Return comment synthesis failed: %s", type(e).__name__)
        return {"themes": [], "error": type(e).__name__}
