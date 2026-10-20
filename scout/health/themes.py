"""
Cross-source customer themes for the health report.

Evidence comes from each source separately: the opening customer message of
sampled support tickets, NPS Survey comments, Returning and New Customer PPS
comments, and Okendo reviews. The model reads numbered items and returns
theme labels, a one-sentence summary, an action, and the item numbers that
support each theme. Everything countable is computed here from those
citations:

- counts per source (distinct tickets, responses, or reviews), and the share
  of that source's analysed items. Counts are never added across sources,
  because one customer can appear in several.
- how many sources a theme appears in, the products named in cited items,
  and a quote that must appear verbatim in a cited item.

A generic label ("inquiry", "general feedback") is rejected: an inquiry
theme has to name its subject.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from scout.llm import MODEL, get_client
from scout.services.evidence_service import process_evidence_with_stats, scrub_personal_details
from scout.taxonomy.products import FAMILY, STYLE, ProductCatalog, find_product_mentions, resolve_product

logger = logging.getLogger("scout.health.themes")

SOURCE_LABELS = {
    "tickets": "Support tickets",
    "nps": "NPS Survey comments",
    "returning_pps": "Returning Customer PPS comments",
    "new_pps": "New Customer PPS comments",
    "reviews": "Okendo reviews",
}
SOURCE_SHORT = {
    "tickets": "tickets",
    "nps": "NPS",
    "returning_pps": "returning PPS",
    "new_pps": "new PPS",
    "reviews": "reviews",
}
UNIT_LABELS = {
    "tickets": "tickets",
    "nps": "responses",
    "returning_pps": "responses",
    "new_pps": "responses",
    "reviews": "reviews",
}

TEXT_CHARS = 400
MAX_THEMES = 8
_MAX_QUOTE = 180

_GENERIC_LABEL = re.compile(
    r"^\s*(?:general|other|misc(?:ellaneous)?|customer|product|support)?\s*"
    r"(?:inquir(?:y|ies)|questions?|feedback|requests?|comments?|issues?|concerns?)\s*$",
    re.I,
)

FRICTION = "friction"
POSITIVE = "positive"


@dataclass(frozen=True)
class EvidenceItem:
    ref: str
    source: str
    unit_id: str
    text: str
    rating: Optional[float] = None
    product: Optional[str] = None


@dataclass
class SourceSample:
    """Eligible, retrieved, and analysed counts for one source."""

    source: str
    eligible: Optional[int]
    eligible_label: str
    retrieved: int
    retrieval_cap: Optional[int]
    analysed: int = 0
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "source": self.source,
            "label": SOURCE_LABELS.get(self.source, self.source),
            "eligible": self.eligible,
            "eligible_label": self.eligible_label,
            "retrieved": self.retrieved,
            "retrieval_cap": self.retrieval_cap,
            "analysed": self.analysed,
            "note": self.note,
        }


@dataclass(frozen=True)
class HealthTheme:
    label: str
    kind: str
    summary: str
    action: str
    counts: Dict[str, int]
    shares: Dict[str, float]
    products: Tuple[str, ...]
    evidence_refs: Tuple[str, ...]
    quote: Optional[str] = None
    quote_ref: Optional[str] = None

    @property
    def source_count(self) -> int:
        return len(self.counts)

    def to_dict(self) -> Dict[str, object]:
        return {
            "label": self.label,
            "kind": self.kind,
            "summary": self.summary,
            "action": self.action,
            "counts": dict(self.counts),
            "shares": dict(self.shares),
            "source_count": self.source_count,
            "cross_source": self.source_count > 1,
            "products": list(self.products),
            "evidence_refs": list(self.evidence_refs),
            "quote": self.quote,
            "quote_ref": self.quote_ref,
        }


@dataclass
class ThemeSynthesis:
    status: str  # "ok", "no_evidence", "unavailable"
    themes: List[HealthTheme] = field(default_factory=list)
    so_what: str = ""
    samples: List[SourceSample] = field(default_factory=list)
    dropped: Dict[str, int] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "themes": [t.to_dict() for t in self.themes],
            "so_what": self.so_what,
            "samples": [s.to_dict() for s in self.samples],
            "dropped": dict(self.dropped),
            "error": self.error,
            "analysed_total_by_source": {s.source: s.analysed for s in self.samples},
        }


# ---------------------------------------------------------------------------
# Evidence from each source
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def _dedupe(items: Iterable[EvidenceItem]) -> List[EvidenceItem]:
    seen = set()
    out = []
    for item in items:
        key = (item.source, _norm(item.text))
        if key[1] and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def ticket_items(rows: Sequence[Mapping[str, Any]]) -> List[EvidenceItem]:
    cleaned, _ = process_evidence_with_stats([dict(r) for r in rows], [], body_key="BODY",
                                             max_output=len(rows) or 1)
    return _dedupe(
        EvidenceItem(
            ref=f"ticket:{m.get('MESSAGE_ID')}",
            source="tickets",
            unit_id=str(m.get("CONVERSATION_ID")),
            text=str(m.get("cleaned_body", ""))[:TEXT_CHARS],
        )
        for m in cleaned
    )


def survey_items(source: str, rows: Sequence[Mapping[str, Any]]) -> List[EvidenceItem]:
    return _dedupe(
        EvidenceItem(
            ref=f"{source}:{r.get('response_id')}",
            source=source,
            unit_id=str(r.get("response_id")),
            text=str(r.get("comment") or "").strip()[:TEXT_CHARS],
            rating=float(r["score"]) if r.get("score") is not None else None,
        )
        for r in rows
        if str(r.get("comment") or "").strip()
    )


def review_items(evidence: Sequence[Mapping[str, Any]]) -> List[EvidenceItem]:
    return _dedupe(
        EvidenceItem(
            ref=f"review:{r.get('review_id')}",
            source="reviews",
            unit_id=str(r.get("review_id")),
            text=str(r.get("text") or "").strip()[:TEXT_CHARS],
            rating=float(r["rating"]) if r.get("rating") is not None else None,
            product=str(r.get("product") or "") or None,
        )
        for r in evidence
        if str(r.get("text") or "").strip()
    )


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You analyse customer evidence for Jack Archer, a men's apparel brand.
Numbered items come from separate sources: support tickets (the customer's opening message),
NPS Survey comments, Returning and New Customer post-purchase survey comments, and Okendo
product reviews. Each item is tagged with its source; survey and review items show a score.

Return ONLY JSON:
{
  "themes": [
    {"label": "Specific theme name",
     "kind": "friction" or "positive",
     "summary": "One sentence on what customers are saying",
     "evidence": [item numbers],
     "quote": "Words copied exactly from one cited item",
     "action": "One specific action this theme supports"}
  ],
  "so_what": "One sentence on what matters most for the business"
}

Rules:
- Ground each theme in the items. Cite every item that supports it, and only those.
- Name the real subject. Never use a generic label such as "inquiry", "questions",
  "general feedback", or "customer service request"; say what the inquiry is about,
  for example "Sizing questions before ordering bottoms".
- Include what is working (positive themes) as well as friction.
- Do not state counts or percentages; they are computed from your citations.
- A theme cited by one item is a single report; do not call it common or widespread.
- At most 8 themes."""


def _prompt(items: Sequence[EvidenceItem]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        tag = SOURCE_SHORT.get(item.source, item.source)
        if item.source == "reviews" and item.rating is not None:
            tag += f", {item.rating:.0f}★"
        elif item.rating is not None:
            tag += f", score {item.rating:.0f}/10"
        if item.product:
            tag += f", {item.product}"
        lines.append(f"[{i}] ({tag}) {item.text}")
    return "Customer evidence:\n\n" + "\n".join(lines)


def _call_model(items: Sequence[EvidenceItem], client_factory: Callable[[], Any]) -> Dict[str, Any]:
    client = client_factory()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _prompt(items)},
        ],
        temperature=0.2,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )
    parsed = json.loads(resp.choices[0].message.content or "")
    if not isinstance(parsed, dict):
        raise ValueError("synthesis output is not a JSON object")
    return parsed


def _products(cited: Sequence[EvidenceItem], catalog: Optional[ProductCatalog]) -> Tuple[str, ...]:
    counter: Counter = Counter()
    for item in cited:
        names = set()
        if item.product:
            resolved = resolve_product(item.product, catalog) if catalog else None
            names.add(resolved.canonical_name if resolved and resolved.is_resolved and resolved.canonical_name
                      else item.product)
        if catalog:
            names.update(m.name for m in find_product_mentions(item.text, catalog) if m.level in (STYLE, FAMILY))
        counter.update(names)
    return tuple(name for name, _ in counter.most_common(3))


def _quote(raw: str, cited: Sequence[EvidenceItem]) -> Tuple[Optional[str], Optional[str]]:
    needle = " ".join(raw.strip().strip('"').lower().split())
    if len(needle) < 8:
        return None, None
    for item in cited:
        if needle in " ".join(item.text.lower().split()):
            text = scrub_personal_details(raw.strip().strip('"'))
            if len(text) > _MAX_QUOTE:
                text = text[:_MAX_QUOTE].rsplit(" ", 1)[0] + "…"
            return text, item.ref
    return None, None


def link_themes(raw_themes: Any, items: Sequence[EvidenceItem], analysed: Mapping[str, int],
                catalog: Optional[ProductCatalog]) -> Tuple[List[HealthTheme], Dict[str, int]]:
    """Model themes → themes with counts computed from valid citations."""
    themes: List[HealthTheme] = []
    dropped: Counter = Counter()
    for raw in raw_themes if isinstance(raw_themes, list) else []:
        if not isinstance(raw, dict):
            dropped["malformed"] += 1
            continue
        label = str(raw.get("label") or "").strip()
        if not label or _GENERIC_LABEL.match(label):
            dropped["generic_label"] += 1
            continue
        evidence = raw.get("evidence")
        refs: List[Any] = evidence if isinstance(evidence, list) else []
        idx = sorted({int(r) for r in refs if isinstance(r, (int, float)) or str(r).isdigit()})
        cited = [items[i - 1] for i in idx if 1 <= i <= len(items)]
        if not cited:
            dropped["no_valid_citations"] += 1
            continue
        units: Dict[str, set] = {}
        for item in cited:
            units.setdefault(item.source, set()).add(item.unit_id)
        counts = {s: len(u) for s, u in units.items()}
        shares = {s: round(n / analysed[s] * 100, 1) for s, n in counts.items() if analysed.get(s)}
        kind = str(raw.get("kind") or "").lower()
        quote, quote_ref = _quote(str(raw.get("quote") or ""), cited)
        themes.append(HealthTheme(
            label=label,
            kind=POSITIVE if kind == POSITIVE else FRICTION,
            summary=str(raw.get("summary") or "").strip(),
            action=str(raw.get("action") or "").strip(),
            counts=counts,
            shares=shares,
            products=_products(cited, catalog),
            evidence_refs=tuple(item.ref for item in cited),
            quote=quote,
            quote_ref=quote_ref,
        ))
    themes.sort(key=lambda t: _rank_key(t, analysed))
    return themes[:MAX_THEMES], dict(dropped)


def _rank_key(theme: HealthTheme, analysed: Mapping[str, int]) -> Tuple[int, float, str]:
    """More sources first, then the share in the theme's best-sampled source.

    Using the largest analysed sample keeps a 1-of-1 survey comment from
    outranking a theme seen in many tickets.
    """
    best = max(theme.counts, key=lambda s: (analysed.get(s, 0), theme.shares.get(s, 0.0)))
    return (-theme.source_count, -theme.shares.get(best, 0.0), theme.label)


def synthesize(
    items_by_source: Mapping[str, Sequence[EvidenceItem]],
    samples: List[SourceSample],
    catalog: Optional[ProductCatalog] = None,
    client_factory: Optional[Callable[[], Any]] = None,
) -> ThemeSynthesis:
    items: List[EvidenceItem] = [i for source in SOURCE_LABELS for i in items_by_source.get(source, [])]
    analysed = Counter(i.source for i in items)
    for sample in samples:
        sample.analysed = analysed.get(sample.source, 0)
    if not items:
        return ThemeSynthesis(status="no_evidence", samples=samples)
    try:
        raw = _call_model(items, client_factory or get_client)
    except Exception as e:
        logger.warning("Health theme synthesis failed: %s", type(e).__name__)
        return ThemeSynthesis(status="unavailable", samples=samples, error=type(e).__name__)
    themes, dropped = link_themes(raw.get("themes"), items, analysed, catalog)
    return ThemeSynthesis(status="ok", themes=themes, so_what=str(raw.get("so_what") or "").strip(),
                          samples=samples, dropped=dropped)
