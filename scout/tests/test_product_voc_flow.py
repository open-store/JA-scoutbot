"""Product VOC end to end: routing → voc_service → run_voc → format_voc.

Snowflake and OpenAI are mocked. ``FakeWarehouse`` answers the candidate,
message, and aggregate queries and records the SQL it was sent, so the tests
can check windows, filters, and which conversation IDs each period used.
"""
import ast
import json
import os
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

from scout.formatting import format_voc
from scout.routing import nl_router
from scout.routing.command_parser import parse_command
from scout.routing.nl_router import build_command_from_routing, route_natural_language
from scout.services import product_feedback_service
from scout.services.voc_service import run_voc_query
from scout.taxonomy.products import alias_only_catalog
from scout.tests.catalog_fixture import fixture_catalog
from scout.tests.fake_warehouse import FakeWarehouse


_COMPLAINTS = (
    "The {product} waistband rolls over after one wash and keeps folding down.",
    "I ordered my usual size in the {product} and the waist is far too tight to button.",
    "Love the colour of the {product}, but the inseam is shorter than the size chart says.",
    "The fabric on my {product} started pilling around the pockets within two weeks.",
    "Is there a way to exchange the {product} for a larger size? The thighs feel restrictive.",
    "My {product} shrank noticeably after a cold wash, which I did not expect at this price.",
)


def _messages(ids, per_conversation=2, product="Atlas Short"):
    """Distinct customer messages; each conversation has its own customer."""
    out: Dict[str, list] = {}
    n = 0
    for cid in ids:
        out[cid] = []
        for _ in range(per_conversation):
            body = _COMPLAINTS[n % len(_COMPLAINTS)].format(product=product)
            out[cid].append({
                "MESSAGE_ID": f"msg-{n}", "CONVERSATION_ID": cid, "AUTHOR_ID": f"cust-{cid}",
                "BODY": f"{body} I have had them for {n + 2} days.", "CREATED_AT": None,
            })
            n += 1
    return out


def _synthesis_client(themes=None, raises=False):
    client = MagicMock()
    if raises:
        client.chat.completions.create.side_effect = RuntimeError("OpenAI unavailable")
        return client
    payload = {
        "headline": "Customers say the Atlas Short waistband rolls over.",
        "themes": themes if themes is not None else [
            {"label": "Waistband rolls over", "evidence": [1, 2, 3], "representative_quote": "waistband rolls over"},
        ],
        "so_what": "Waistband construction drives most complaints.",
        "recommended_action": "Review the waistband spec with the factory.",
    }
    client.chat.completions.create.return_value.choices = [MagicMock(message=MagicMock(content=json.dumps(payload)))]
    return client


def _run(cmd, warehouse, llm=None, catalog=None):
    llm = llm or _synthesis_client()
    with patch("scout.repositories.product_feedback_repository.execute_query_dict", side_effect=warehouse.repo), \
         patch("queries.voc.execute_query_dict", side_effect=warehouse.voc), \
         patch.object(product_feedback_service, "get_client", return_value=llm), \
         patch("scout.services.voc_service.get_catalog", return_value=catalog or fixture_catalog()):
        data = run_voc_query(cmd)
    return data, format_voc(data), llm


CURRENT = [f"conv-{i}" for i in range(6)]
PREVIOUS = [f"prev-{i}" for i in range(4)]


# ---------------------------------------------------------------------------
# Scope, prior period, and formatter contract
# ---------------------------------------------------------------------------

def test_atlas_short_end_to_end():
    cmd = parse_command('/voc L30 product:"atlas shorts" channel:email')
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS, messages=_messages(CURRENT))
    data, out, llm = _run(cmd, wh)

    # Candidate SQL: explicit tz-aware window, shared channel filter, no first-word broadening.
    strict = wh.candidate_calls("current", "strict")[0]
    assert f"TO_TIMESTAMP_TZ('{cmd.start_date.isoformat()}')" in strict
    assert f"TO_TIMESTAMP_TZ('{cmd.end_date.isoformat()}')" in strict
    assert "LOWER(c.CHANNEL) = 'email'" in strict
    assert "'%atlas short%'" in strict
    assert "'%atlas%'" not in strict
    assert "DATEADD" not in strict

    # Previous period is its own scope over the previous window.
    prev_strict = wh.candidate_calls("previous", "strict")[0]
    assert f"TO_TIMESTAMP_TZ('{cmd.previous_end_date.isoformat()}')" in prev_strict
    prev_sql = wh.previous_volume_sql()
    assert all(f"'{p}'" in prev_sql for p in PREVIOUS)
    assert not any(f"'{c}'" in prev_sql for c in CURRENT)
    cur_sql = wh.current_volume_sql()
    assert all(f"'{c}'" in cur_sql for c in CURRENT)
    assert not any(f"'{p}'" in cur_sql for p in PREVIOUS)

    pf = data["product_feedback"]
    assert pf["product_name"] == "Atlas Short"
    assert pf["sample_count"] == 12
    assert pf["total_conversations"] == 6
    assert pf["previous_total_conversations"] == 4
    assert pf["scope"]["filters"] == {"channel": "email"}
    assert pf["scope"]["timezone"] == "America/New_York"
    assert pf["previous_scope"]["start"] == cmd.previous_start_date.isoformat()
    assert len(pf["evidence_message_ids"]) == 12

    # The formatter renders the service output, including themes.
    assert "Waistband rolls over" in out
    assert "12 customer messages from 6 of 6 product-related tickets" in out
    assert "+50.0% vs. previous 30 days" in out
    assert "Atlas Short (style in the Atlas family)" in out
    assert "(America/New_York)" in out
    assert "product: Atlas Short, channel: email" in out


def test_sample_count_is_what_synthesis_received():
    ids = [f"conv-{i}" for i in range(85)]
    cmd = parse_command('/voc L30 product:"Charter Twill Pant"')
    wh = FakeWarehouse(cmd, ids, PREVIOUS, messages=_messages(ids, per_conversation=2, product="Charter Twill Pant"))
    data, out, llm = _run(cmd, wh)
    prompt = llm.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "[150]" in prompt and "[151]" not in prompt
    pf = data["product_feedback"]
    assert pf["retrieved_message_count"] == 170
    assert pf["cleaned_message_count"] == 170
    assert pf["sample_count"] == 150
    assert any("150 highest-ranked of 170" in c for c in pf["caveats"])
    assert "150 customer messages from 75 of 85 product-related tickets" in out


def test_message_cap_is_disclosed_as_sample_based():
    ids = [f"conv-{i}" for i in range(100)]
    cmd = parse_command('/voc L30 product:"Charter Twill Pant"')
    wh = FakeWarehouse(cmd, ids, PREVIOUS, messages=_messages(ids, per_conversation=3, product="Charter Twill Pant"))
    data, out, _ = _run(cmd, wh)
    assert data["product_feedback"]["retrieved_message_count"] == 200
    assert "sample-based" in out


def test_previous_scope_uses_the_current_retrieval_pass():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    # Strict finds 2 (< 5), so the current period adds the body-only pass.
    wh = FakeWarehouse(cmd, CURRENT[:2], PREVIOUS[:1], current_body=CURRENT[2:], previous_body=PREVIOUS[1:],
                       messages=_messages(CURRENT))
    data, out, _ = _run(cmd, wh)
    assert data["product_feedback"]["retrieval_mode"] == "fallback_body_only"
    assert len(wh.candidate_calls("previous", "body")) == 1
    assert data["product_feedback"]["previous_total_conversations"] == 4
    assert "tag requirement was dropped to widen the search" in out


def test_previous_scope_stays_strict_when_current_is_strict():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS[:1], previous_body=PREVIOUS[1:], messages=_messages(CURRENT))
    data, _, _ = _run(cmd, wh)
    assert data["product_feedback"]["retrieval_mode"] == "strict"
    assert wh.candidate_calls("previous", "body") == []
    assert data["product_feedback"]["previous_total_conversations"] == 1


def test_zero_previous_conversations_is_reported():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    wh = FakeWarehouse(cmd, CURRENT, [], messages=_messages(CURRENT))
    _, out, _ = _run(cmd, wh)
    assert "none in the previous 30 days" in out


def test_topic_focuses_synthesis_and_is_shown():
    cmd = parse_command('/voc L30 product:"Atlas Short" topic:waistband')
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS, messages=_messages(CURRENT))
    data, out, llm = _run(cmd, wh)
    prompt = llm.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Focus topic from the question: waistband" in prompt
    assert "Question focus:* waistband" in out
    assert "topic" not in data["filter_label"]


def test_synthesis_failure_still_reports_scope_and_counts():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS, messages=_messages(CURRENT))
    _, out, _ = _run(cmd, wh, llm=_synthesis_client(raises=True))
    assert "Theme synthesis unavailable" in out
    assert "12 customer messages from 6 of 6 product-related tickets" in out


# ---------------------------------------------------------------------------
# Style, family, ambiguity, unknown, and catalog outage
# ---------------------------------------------------------------------------

def test_family_question_uses_family_scope():
    cmd = parse_command("/voc L30 product:Jetsetter")
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS, messages=_messages(CURRENT, product="Jetsetter Tech Pant"))
    data, out, _ = _run(cmd, wh)
    strict = wh.candidate_calls("current", "strict")[0]
    assert "'%jetsetter%'" in strict
    assert data["product_feedback"]["product_level"] == "family"
    assert "Jetsetter family (3 styles" in out
    assert "Family-level answer" in out


def test_ambiguous_family_product_asks_and_queries_nothing():
    cmd = parse_command('/voc L30 product:"jetsetter pant"')
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS)
    data, out, _ = _run(cmd, wh)
    assert wh.repo_sql == [] and wh.voc_sql == []
    assert data["product_feedback"]["status"] == "ambiguous"
    assert "matches more than one product" in out
    assert "• Jetsetter Tech Pant" in out and "• Jetsetter Travel Pant" in out
    assert 'product:"Jetsetter"' in out


def test_unknown_product_explains_and_queries_nothing():
    cmd = parse_command('/voc L30 product:"Atlas Jogger"')
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS)
    data, out, _ = _run(cmd, wh)
    assert wh.repo_sql == [] and wh.voc_sql == []
    assert "couldn't find 'Atlas Jogger'" in out
    assert "• Atlas Short" in out
    assert "no other products were mixed" in out


def test_catalog_outage_matches_exact_phrase_with_caveat():
    cmd = parse_command('/voc L30 product:"Atlas Short"')
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS, messages=_messages(CURRENT))
    data, out, _ = _run(cmd, wh, catalog=alias_only_catalog(error="ProgrammingError"))
    strict = wh.candidate_calls("current", "strict")[0]
    assert "'%atlas short%'" in strict and "'%atlas%'" not in strict
    assert data["product_feedback"]["match_method"] == "exact_phrase_unverified"
    assert "could not be loaded" in out


def test_empty_product_scope_returns_no_data_with_product_label():
    cmd = parse_command('/voc L30 product:"Charter Twill Pant"')
    wh = FakeWarehouse(cmd, [], [])
    data, out, _ = _run(cmd, wh)
    assert data["product_feedback"]["retrieval_mode"] == "empty"
    assert "No VOC data found" in out
    assert "product: Charter Twill Pant" in out


# ---------------------------------------------------------------------------
# Natural language with the routing model down
# ---------------------------------------------------------------------------

def test_nl_question_with_llm_down_keeps_product_through_formatting():
    failing = MagicMock()
    failing.chat.completions.create.side_effect = RuntimeError("OPENAI_API_KEY is not set")
    with patch.object(nl_router, "get_client", return_value=failing):
        routing = route_natural_language(
            "Any complaints about fit on the Charter Twill Pant in the last 180 days?",
            catalog_provider=fixture_catalog,
        )
    cmd = parse_command(build_command_from_routing(routing))
    assert (cmd.command, cmd.days, cmd.filters["product"], cmd.filters["topic"]) == (
        "voc", 180, "Charter Twill Pant", "fit")
    wh = FakeWarehouse(cmd, CURRENT, PREVIOUS, messages=_messages(CURRENT, product="Charter Twill Pant"))
    _, out, llm = _run(cmd, wh, llm=_synthesis_client(raises=True))
    assert "product: Charter Twill Pant" in out
    assert "last 180 complete days" in out
    assert "Question focus:* fit" in out


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "scout/services/product_feedback_service.py",
    "scout/repositories/product_feedback_repository.py",
    "scout/services/product_catalog_service.py",
    "scout/repositories/product_catalog_repository.py",
])
def test_product_pipeline_does_not_import_voc_query(path):
    root = os.path.join(os.path.dirname(__file__), "..", "..")
    tree = ast.parse(open(os.path.join(root, path)).read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "queries.voc"
        if isinstance(node, ast.Import):
            assert all(a.name != "queries.voc" for a in node.names)
