"""Regression tests for the source modules the health report reuses. Snowflake and Okendo are mocked."""
from unittest.mock import MagicMock, patch

import queries.nps as nps
import queries.reviews as reviews
from queries.voc import _build_filter_clause
from scout.repositories.product_feedback_repository import build_scope_filters
from scout.routing.command_parser import parse_command


def _connection(results):
    """A fake connection whose cursor returns *results* (one list per execute) in order."""
    cur = MagicMock()
    batches = iter(results)
    cur.fetchall.side_effect = lambda: next(batches)
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_nps_trend_runs_and_reports_prior_sample():
    current = [("NPS Survey", 100, 60, 30, 10)]
    prior = [("NPS Survey", 80, 40, 12)]
    conns = [_connection([current])[0], _connection([prior])[0]]
    with patch.object(nps, "get_connection", side_effect=conns):
        trend = nps.get_nps_trend(30)
    t = trend["NPS Survey"]
    assert t["current_nps"] == 50.0 and t["prior_nps"] == 35.0 and t["delta"] == 15.0
    assert t["prior_total"] == 80 and t["prior_start"] < t["prior_end"]


def test_open_text_evidence_keeps_response_ids_and_scores():
    conn, cur = _connection([[("resp-1", 3, "Sizing is off on the shorts", None), ("resp-2", 10, "  ", None)]])
    with patch.object(nps, "get_connection", return_value=conn):
        rows = nps.get_open_text_evidence(30, "Returning Customer PPS", limit=5)
    assert rows == [{"response_id": "resp-1", "score": 3, "comment": "Sizing is off on the shorts", "created_at": None}]
    sql = cur.execute.call_args.args[0]
    assert nps.SURVEY_IDS["Returning Customer PPS"] in sql and "LIMIT 5" in sql
    conn.close.assert_called_once()


def test_agent_filter_uses_the_assignee_column():
    sql, _ = _build_filter_clause(parse_command("/voc L7 agent:abc123"))
    assert "ASSIGNEE_ID" in sql and "ASSIGNED_AGENT_ID" not in sql
    scope_sql = build_scope_filters({"agent": "abc123"})
    assert "c.ASSIGNEE_ID" in scope_sql


def test_reviews_include_balanced_evidence_and_cap_flag():
    fetched = [{"reviewId": f"r{i}", "rating": 5 if i % 2 else 2, "productName": "Atlas Short",
                "title": "Title", "body": f"Body {i}"} for i in range(80)]
    with patch.object(reviews, "fetch_current_reviews", return_value=fetched), \
         patch.object(reviews, "fetch_prior_reviews", return_value=[]):
        result = reviews.run_reviews(30)
    evidence = result["evidence"]
    assert len(evidence) == 60
    assert sum(1 for e in evidence if e["rating"] <= 3) == 30
    assert evidence[0] == {"review_id": "r0", "rating": 2, "product": "Atlas Short", "text": "Title Body 0",
                           "created_at": None}
    assert result["fetch_capped"] is False
    with patch.object(reviews, "fetch_current_reviews", return_value=fetched * 25), \
         patch.object(reviews, "fetch_prior_reviews", return_value=[]):
        assert reviews.run_reviews(30)["fetch_capped"] is True
