"""Golden cases for natural-language routing.

The OpenAI client and the product catalog are mocked. Product names come
from ``catalog_fixture``.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from scout.routing import nl_router
from scout.routing.command_parser import parse_command
from scout.routing.nl_router import (
    build_command_from_routing,
    extract_timeframe,
    extract_topic,
    route_natural_language,
)
from scout.taxonomy.products import alias_only_catalog
from scout.tests.catalog_fixture import fixture_catalog


def _failing_client():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("OPENAI_API_KEY is not set")
    return client


def _llm_client(payload):
    client = MagicMock()
    content = payload if isinstance(payload, str) else json.dumps(payload)
    client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=content))]
    return client


def _route(text, client, catalog=None):
    provider = MagicMock(return_value=catalog or fixture_catalog())
    with patch.object(nl_router, "get_client", return_value=client):
        return route_natural_language(text, catalog_provider=provider), provider


def _parsed(routing):
    return parse_command(build_command_from_routing(routing))


# ---------------------------------------------------------------------------
# LLM failure keeps product, intent, and timeframe
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, command, days, product, topic", [
    ("What are customers saying about the Atlas Short?", "voc", 30, "Atlas Short", None),
    ("Any complaints about fit on the Charter Twill Pant in the last 180 days?", "voc", 180,
     "Charter Twill Pant", "fit"),
    ("what do people think of the atlas shorts this month", "voc", 30, "Atlas Short", None),
    ("How do customers like the Atlas Short waistband over the past 6 months?", "voc", 180,
     "Atlas Short", "waistband"),
    ("How are returns trending for the Charter Twill Pant?", "returns", 30, "Charter Twill Pant", None),
    ("Show me reviews for the Atlas Short last 90 days", "reviews", 90, "Atlas Short", None),
    ("What's the CSAT on Atlas Short tickets this week?", "voc", 7, "Atlas Short", None),
    ("What are people saying about the Jetsetter line?", "voc", 30, "Jetsetter", None),
    ("Any sleeve length callouts on the anytime crewneck?", "voc", 30, "Anytime Crewneck", "sleeve length"),
])
def test_llm_failure_preserves_scope(text, command, days, product, topic):
    routing, _ = _route(text, _failing_client())
    assert routing["router"] == "keyword_fallback"
    cmd = _parsed(routing)
    assert cmd.is_valid
    assert cmd.command == command
    assert cmd.days == days
    assert cmd.filters.get("product") == product
    assert cmd.filters.get("topic") == topic


def test_llm_failure_keeps_unknown_product_phrase():
    routing, _ = _route("What do people think of the Mystery Pant?", _failing_client())
    assert routing["command"] == "VOC"
    assert routing["filters"]["product"] == "mystery pant"


def test_llm_failure_keeps_family_ambiguity_for_the_service_to_ask():
    routing, _ = _route("Any feedback on the jetsetter pant?", _failing_client())
    assert routing["filters"]["product"] == "jetsetter pant"


def test_llm_failure_with_catalog_outage_keeps_typed_product():
    routing, _ = _route("What are customers saying about the Atlas Short?", _failing_client(),
                        catalog=alias_only_catalog(error="ProgrammingError"))
    assert routing["command"] == "VOC"
    assert routing["filters"]["product"] == "atlas short"


def test_llm_failure_general_questions_unchanged():
    routing, provider = _route("what's our NPS last month?", _failing_client())
    assert routing["command"] == "NPS" and routing["filters"] == {}
    provider.assert_not_called()
    routing, _ = _route("why do customers come back as returning customers?", _failing_client())
    assert routing["command"] == "PPS" and routing["filters"] == {"segment": "returning"}
    routing, _ = _route("show me top complaints", _failing_client())
    assert routing["command"] == "VOC" and routing["filters"] == {} and routing["confidence"] == "low"
    assert routing["timeframe"] == "L30"


def test_returning_customers_is_not_a_returns_question():
    routing, _ = _route("what do returning customers want to see more of", _failing_client())
    assert routing["command"] != "Returns"


# ---------------------------------------------------------------------------
# LLM output validation
# ---------------------------------------------------------------------------

def test_llm_omitted_product_is_added_from_catalog():
    client = _llm_client({"command": "VOC", "timeframe": "L30", "filters": {}, "confidence": "high", "reasoning": "x"})
    routing, _ = _route("Do customers mention the inseam on the Charter Twill Pant?", client)
    assert routing["router"] == "llm"
    assert routing["filters"] == {"product": "Charter Twill Pant", "topic": "inseam"}


def test_llm_first_word_product_is_replaced_by_catalog_match():
    client = _llm_client({"command": "VOC", "timeframe": "L30", "filters": {"product": "atlas"}, "confidence": "high"})
    routing, _ = _route("what are customers saying about the atlas short", client)
    assert routing["filters"]["product"] == "Atlas Short"


def test_llm_product_is_canonicalised():
    client = _llm_client({"command": "Reviews", "timeframe": "L30", "filters": {"product": "charter twill pants"}})
    routing, _ = _route("reviews for charter twill pants", client)
    assert routing["filters"]["product"] == "Charter Twill Pant"


def test_explicit_window_overrides_llm_timeframe():
    client = _llm_client({"command": "VOC", "timeframe": "L30", "filters": {"product": "atlas short"}})
    routing, _ = _route("atlas short feedback over the last 90 days", client)
    assert routing["timeframe"] == "L90"


@pytest.mark.parametrize("payload", [
    {"command": "Daily", "timeframe": "L7", "filters": {}},
    {"command": None},
    "[1, 2, 3]",
    "not json",
])
def test_invalid_llm_output_falls_back_with_scope(payload):
    routing, _ = _route("What are customers saying about the Atlas Short?", _llm_client(payload))
    assert routing["router"] == "keyword_fallback"
    assert routing["filters"]["product"] == "Atlas Short"


def test_llm_null_filters_and_bad_timeframe_are_normalised():
    client = _llm_client({"command": "csat", "timeframe": "last week", "filters": None, "confidence": "sure"})
    routing, provider = _route("how is csat looking", client)
    assert routing["command"] == "CSAT"
    assert routing["timeframe"] == "L30"
    assert routing["filters"] == {}
    assert routing["confidence"] == "medium"


def test_product_dropped_for_commands_that_cannot_use_it():
    client = _llm_client({"command": "NPS", "timeframe": "L30", "filters": {"product": "Atlas Short"}})
    routing, _ = _route("NPS from Atlas Short buyers", client)
    assert routing["filters"] == {}


# ---------------------------------------------------------------------------
# Extraction helpers and command building
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("last 180 days", "L180"), ("past 6 months", "L180"), ("in the last two weeks", "L14"),
    ("this month", "L30"), ("this week", "L7"), ("last quarter", "L90"), ("L45 please", "L45"),
    ("past year", "L365"), ("half a year", "L180"), ("what are customers saying", None),
    ("someone weekly", "L7"),
])
def test_extract_timeframe(text, expected):
    assert extract_timeframe(text) == expected


def test_extract_topic_prefers_longer_terms():
    assert extract_topic("sleeve length and fit on the tee") == "sleeve length, fit"
    assert extract_topic("is the fabric pilling") == "fabric, pilling"
    assert extract_topic("what are customers saying") is None


def test_build_command_strips_quotes_and_round_trips():
    routing = {"command": "VOC", "timeframe": "L30",
               "filters": {"product": 'Atlas Short 7"', "topic": "sleeve length"}}
    cmd = _parsed(routing)
    assert cmd.filters == {"product": "Atlas Short 7", "topic": "sleeve length"}
