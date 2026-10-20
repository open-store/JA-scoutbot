"""Regression test: `/nps` must run end to end. Snowflake is mocked.

`get_nps_trend` uses ``datetime``, ``timedelta``, and ``timezone``; when the
import was missing, every `/nps` raised ``NameError``.
"""
from unittest.mock import MagicMock, patch

import queries.nps as nps


def _connection(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_nps_trend_compares_current_and_prior_windows():
    current = [("NPS Survey", 100, 60, 30, 10)]
    prior = [("NPS Survey", 80, 40, 12)]
    with patch.object(nps, "get_connection", side_effect=[_connection(current), _connection(prior)]):
        trend = nps.get_nps_trend(30)
    assert trend["NPS Survey"] == {"current_nps": 50.0, "prior_nps": 35.0, "delta": 15.0}


def test_full_nps_report_runs():
    current = [("NPS Survey", 100, 60, 30, 10)]
    prior = [("NPS Survey", 80, 40, 12)]
    comments = [("Too slim in the shoulders",)]
    connections = [_connection(current), _connection(current), _connection(prior), _connection(comments)]
    with patch.object(nps, "get_connection", side_effect=connections):
        report = nps.get_full_nps_report(30)
    assert report["scores"]["NPS Survey"]["nps"] == 50.0
    assert report["trend"]["NPS Survey"]["delta"] == 15.0
    assert report["detractor_comment_count"] == 1
    assert report["detractor_themes"] == []
