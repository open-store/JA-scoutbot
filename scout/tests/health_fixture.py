"""Fixture source outputs for customer-health tests.

Shapes match what each query module returns. Numbers, comments, and
reviews are invented test data, not Jack Archer results.
"""
from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Iterator, Optional
from unittest.mock import MagicMock, patch

from queries.returns import run_return_rates
from scout.tests.catalog_fixture import fixture_catalog

NOW = datetime.now(timezone.utc)


def source_outputs() -> Dict[str, Any]:
    return {
        "richpanel_sync": {"LAST_SYNCED": NOW - timedelta(hours=2), "LATEST_RECORD": NOW - timedelta(hours=1)},
        "knocommerce_sync": {"LAST_SYNCED": NOW - timedelta(hours=5), "LATEST_RECORD": NOW - timedelta(hours=6)},
        "csat": {
            "current": {"TOTAL_CONVERSATIONS": 4400, "TOTAL_RATED": 412, "POSITIVE_RATINGS": 376,
                        "NEGATIVE_RATINGS": 36, "CSAT_PCT": Decimal("91.3")},
            "previous": {"TOTAL_CONVERSATIONS": 4180, "TOTAL_RATED": 398, "CSAT_PCT": Decimal("90.1")},
        },
        "nps_scores": {
            "NPS Survey": {"total": 310, "promoters": 192, "passives": 87, "detractors": 31, "nps": 51.6,
                           "promoter_pct": 61.9, "passive_pct": 28.1, "detractor_pct": 10.0},
            "Returning Customer PPS": {"total": 120, "nps": 61.0},
            "New Customer PPS": {"total": 95, "nps": 57.9},
        },
        "nps_trend": {
            "NPS Survey": {"current_nps": 51.6, "prior_nps": 47.2, "delta": 4.4, "prior_total": 288,
                           "prior_start": "2026-08-21", "prior_end": "2026-09-20"},
        },
        "returning_pps": {
            "response_count": 128,
            "nps_breakdown": {"total": 120, "nps": 61.0, "promoter_pct": 70.0, "detractor_pct": 9.0},
            "return_reasons": [{"label": "Product quality", "count": 53, "pct": 44.2}],
            "almost_stopped_pct": 18.0,
            "open_text_themes": [{"theme": "More colours", "count": 6, "pct": 15.0}],
            "open_text_count": 40,
        },
        "new_pps": {
            "response_count": 102,
            "nps_breakdown": {"total": 95, "nps": 57.9, "promoter_pct": 66.0, "detractor_pct": 8.1},
            "open_text_themes": [],
            "open_text_count": 30,
        },
        "attribution": {
            "response_count": 180,
            "how_heard": [
                {"label": "Instagram", "count": 40, "pct": 22.2},
                {"label": "Word of Mouth / Referral", "count": 32, "pct": 17.8},
                {"label": "Search Engine", "count": 22, "pct": 12.2},
            ],
        },
        "reviews": {
            "avg_rating": 4.6, "total_reviews": 230, "prior_avg_rating": 4.52, "prior_total_reviews": 210,
            "sentiment_breakdown": {"positive": 190, "negative": 22}, "fetch_capped": False,
            "evidence": [
                {"review_id": "r1", "rating": 2, "product": "Atlas Short",
                 "text": "Waist runs small. I had to size up twice before they fit."},
                {"review_id": "r2", "rating": 5, "product": "Clubhouse Polo",
                 "text": "Fabric is soft and holds its shape after many washes."},
            ],
        },
        "returns": run_return_rates(30),
        "ticket_evidence": [
            {"MESSAGE_ID": "m1", "CONVERSATION_ID": "c1", "AUTHOR_ID": "a1",
             "BODY": "Is the Atlas Short true to size? Reviews say the waist runs small and I am between sizes."},
            {"MESSAGE_ID": "m2", "CONVERSATION_ID": "c2", "AUTHOR_ID": "a2",
             "BODY": "Where is my order? Tracking has not updated in five days."},
            {"MESSAGE_ID": "m3", "CONVERSATION_ID": "c3", "AUTHOR_ID": "a3",
             "BODY": "I need to exchange the Charter Twill Pant for a bigger waist size, it is too tight."},
        ],
        "nps_evidence": [
            {"response_id": "n1", "score": 3, "comment": "Sizing is inconsistent between styles, the shorts run small."},
            {"response_id": "n2", "score": 10, "comment": "Best polos I own, the fabric is great and lasts."},
        ],
        "returning_pps_evidence": [
            {"response_id": "p1", "score": 9, "comment": "Came back because the quality held up after a year."},
        ],
        "new_pps_evidence": [],
        "catalog": fixture_catalog(),
    }


def synthesis_payload() -> Dict[str, Any]:
    # Item numbers follow source order: tickets [1-3], nps [4-5], returning PPS [6], reviews [7-8].
    return {
        "themes": [
            {"label": "Bottoms run small in the waist", "kind": "friction",
             "summary": "Customers say shorts and pants run small at the waist and need sizing up.",
             "evidence": [1, 3, 4, 7], "quote": "the waist runs small", "action": "Add waist guidance to the bottoms size chart."},
            {"label": "Order tracking delays", "kind": "friction",
             "summary": "One customer reports tracking that stopped updating.",
             "evidence": [2], "quote": "Tracking has not updated in five days", "action": "Check the carrier feed."},
            {"label": "Fabric quality and durability", "kind": "positive",
             "summary": "Customers praise fabric that lasts.", "evidence": [5, 6, 8],
             "quote": "the fabric is great and lasts", "action": "Lead launch copy with durability."},
            {"label": "Inquiry", "kind": "friction", "summary": "General questions.", "evidence": [2],
             "quote": "", "action": ""},
        ],
        "so_what": "Waist sizing on bottoms is the friction that shows up across tickets, surveys, and reviews.",
    }


def llm_client(payload: Optional[Dict[str, Any]] = None, raises: bool = False) -> MagicMock:
    client = MagicMock()
    if raises:
        client.chat.completions.create.side_effect = RuntimeError("OPENAI_API_KEY is not set")
    else:
        content = json.dumps(payload if payload is not None else synthesis_payload())
        client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=content))]
    return client


_TARGETS = {
    "richpanel_sync": None,
    "knocommerce_sync": None,
    "csat": "run_csat",
    "nps_scores": "get_nps_scores",
    "nps_trend": "get_nps_trend",
    "returning_pps": "get_returning_pps",
    "new_pps": "get_new_pps",
    "attribution": "get_attribution",
    "reviews": "run_reviews",
    "returns": "run_return_rates",
    "ticket_evidence": "fetch_opening_messages",
    "catalog": "get_catalog",
}


@contextmanager
def patched_sources(outputs: Dict[str, Any], llm: Optional[MagicMock] = None,
                    failures: Optional[Dict[str, Exception]] = None) -> Iterator[MagicMock]:
    """Patch every source the health service calls. ``failures`` makes a source raise."""
    failures = failures or {}
    llm = llm or llm_client()

    def value_or_raise(name: str):
        def fn(*args, **kwargs):
            if name in failures:
                raise failures[name]
            return outputs.get(name)
        return fn

    def sync(relation, *args, **kwargs):
        name = "richpanel_sync" if "RICHPANEL" in relation else "knocommerce_sync"
        if name in failures:
            raise failures[name]
        return outputs.get(name)

    def evidence(days, survey_name="NPS Survey", limit=60):
        name = {"NPS Survey": "nps_evidence", "Returning Customer PPS": "returning_pps_evidence",
                "PPS - New Customers": "new_pps_evidence"}[survey_name]
        if name in failures:
            raise failures[name]
        return outputs.get(name) or []

    with ExitStack() as stack:
        svc = "scout.services.health_service"
        for name, attr in _TARGETS.items():
            if attr:
                stack.enter_context(patch(f"{svc}.{attr}", side_effect=value_or_raise(name)))
        stack.enter_context(patch(f"{svc}.fetch_sync_times", side_effect=sync))
        stack.enter_context(patch(f"{svc}.get_open_text_evidence", side_effect=evidence))
        stack.enter_context(patch("scout.health.themes.get_client", return_value=llm))
        yield llm
