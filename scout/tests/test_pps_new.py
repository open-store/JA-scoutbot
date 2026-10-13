"""New-customer PPS: query, formatter, slash parsing, and keyword routing.

Snowflake and OpenAI are mocked. Nothing here opens a live connection.
"""

import os

os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-signing-secret")

from scout.formatting.pps import format_attribution, format_pps_new, format_pps_returning
from scout.integrations.knocommerce import SURVEY_IDS, fqn
from scout.routing.command_parser import build_pps_command, parse_command
from scout.routing.nl_router import _keyword_fallback, build_command_from_routing


NEW_SURVEY_ID = SURVEY_IDS["PPS - New Customers"]
RETURNING_SURVEY_ID = SURVEY_IDS["Returning Customer PPS"]


class _FakeCursor:
    def __init__(self):
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)

    def fetchone(self):
        sql = self.queries[-1]
        if "COUNT(*)" in sql:
            return (4,)
        return (4, 3, 1, 0)

    def fetchall(self):
        return [("love the fabric",), ("shipping took too long",)]

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


def test_get_new_pps_reads_new_customer_survey(monkeypatch):
    """get_new_pps uses the new-customer survey id and the existing question filters."""
    from queries import pps

    cursor = _FakeCursor()
    conn = _FakeConn(cursor)
    monkeypatch.setattr(pps, "get_connection", lambda: conn)
    monkeypatch.setattr(pps, "_extract_pps_themes", lambda comments, survey_type="returning": [
        {"theme": "Fit", "count": 1, "pct": 50.0, "verbatims": ["love the fabric"]},
    ] if survey_type == "new" else [])

    data = pps.get_new_pps(30)

    assert data["days"] == 30
    assert data["response_count"] == 4
    assert data["nps_breakdown"]["nps"] == 75.0
    assert data["nps_breakdown"]["promoters"] == 3
    assert data["open_text_count"] == 2
    assert data["open_text_themes"][0]["theme"] == "Fit"
    assert conn.closed is True

    sql = "\n".join(cursor.queries)
    assert NEW_SURVEY_ID in sql
    assert RETURNING_SURVEY_ID not in sql
    assert fqn("RESPONSE") in sql
    assert fqn("RESPONSE_ANSWER") in sql
    assert "ANALYTICS" in sql
    assert "KNOCOMMERCE__NPS___SURVEYS_" in sql
    assert "ILIKE '%recommend%'" in sql
    assert "TYPE IN ('Text', 'TextArea')" in sql
    # Returning-only and attribution questions stay on their own reports.
    assert "come back" not in sql.lower()
    assert "stop you" not in sql.lower()
    assert "hear about" not in sql.lower()
    assert "brought you" not in sql.lower()


def test_format_pps_new_renders_nps_and_themes():
    text = format_pps_new({
        "days": 30,
        "response_count": 12,
        "nps_breakdown": {
            "nps": 40.0,
            "promoter_pct": 60.0,
            "passive_pct": 20.0,
            "detractor_pct": 20.0,
            "total": 10,
        },
        "open_text_themes": [
            {"theme": "Fabric feel", "pct": 40.0, "verbatims": ["softest tee I own"]},
        ],
        "open_text_count": 8,
    })
    assert "New Customer PPS — Last 30 Days" in text
    assert "n=12" in text
    assert "NPS:" in text
    assert "+40.0" in text
    assert "Fabric feel" in text
    assert "softest tee I own" in text
    assert "ANALYTICS.KNOCOMMERCE__NPS___SURVEYS_" in text
    assert "Why did they come back?" not in text
    assert "almost stop" not in text


def test_format_pps_new_empty():
    text = format_pps_new({"days": 7, "response_count": 0})
    assert "No New Customer PPS data found" in text


def test_build_pps_command_segments():
    assert build_pps_command("") == "/PPS segment:returning"
    assert build_pps_command("L30") == "/PPS L30 segment:returning"
    assert build_pps_command("new") == "/PPS new segment:new"
    assert build_pps_command("new L7") == "/PPS new L7 segment:new"
    assert build_pps_command("L90 first") == "/PPS L90 first segment:new"

    bare = parse_command(build_pps_command(""))
    assert bare.command == "pps"
    assert bare.filters["segment"] == "returning"
    assert bare.days == 7

    new_cmd = parse_command(build_pps_command("new L30"))
    assert new_cmd.command == "pps"
    assert new_cmd.filters["segment"] == "new"
    assert new_cmd.days == 30

    returning = parse_command("/PPS L30")
    assert returning.command == "pps"
    assert "segment" not in returning.filters


def test_keyword_fallback_routes_new_pps_and_keeps_attribution():
    new_pps = _keyword_fallback("show me new customer PPS")
    assert new_pps["command"] == "PPS"
    assert new_pps["filters"]["segment"] == "new"
    assert build_command_from_routing(new_pps) == "/PPS L30 segment:new"

    attribution = _keyword_fallback("how are people finding us")
    assert attribution["command"] == "Attribution"
    assert attribution["filters"] == {}

    survey = _keyword_fallback("what does the new customer survey say about channels")
    assert survey["command"] == "Attribution"

    hear = _keyword_fallback("how did you hear about us")
    assert hear["command"] == "Attribution"

    # A new-customer PPS question that also asks attribution stays on attribution.
    mixed = _keyword_fallback("new customer PPS attribution by channel")
    assert mixed["command"] == "Attribution"

    returning = _keyword_fallback("why do people come back")
    assert returning["command"] == "PPS"
    assert returning["filters"]["segment"] == "returning"


def test_execute_pps_new_and_leaves_attribution(monkeypatch):
    """Dispatch uses the new-customer report for segment:new and leaves attribution alone."""
    import slack_bolt

    class _FakeApp:
        def __init__(self, *args, **kwargs):
            pass

        def command(self, *args, **kwargs):
            def deco(fn):
                return fn
            return deco

        def event(self, *args, **kwargs):
            def deco(fn):
                return fn
            return deco

    monkeypatch.setattr(slack_bolt, "App", _FakeApp)
    import scout_bot

    calls = {}

    def _new(days):
        calls["new"] = days
        return {"days": days, "response_count": 3, "nps_breakdown": {}, "open_text_themes": [], "open_text_count": 0}

    def _returning(days):
        calls["returning"] = days
        return {"days": days, "response_count": 2, "nps_breakdown": {}, "return_reasons": [], "almost_stopped_pct": 0, "open_text_themes": [], "open_text_count": 0}

    def _attribution(days):
        calls["attribution"] = days
        return {
            "days": days,
            "response_count": 5,
            "how_heard": [{"label": "Instagram", "count": 5, "pct": 100.0}],
            "what_brought": [],
            "consideration_window": [],
            "who_for": [],
            "other_breakdown": [],
            "other_writeins_count": 0,
            "open_text_themes": [],
            "open_text_count": 0,
        }

    monkeypatch.setattr(scout_bot, "get_new_pps", _new)
    monkeypatch.setattr(scout_bot, "get_returning_pps", _returning)
    monkeypatch.setattr(scout_bot, "run_attribution", _attribution)

    new_text = scout_bot.execute_scout_command("/PPS new L30 segment:new")
    assert "new" in calls and calls["new"] == 30
    assert "returning" not in calls
    assert "New Customer PPS" in new_text

    calls.clear()
    returning_text = scout_bot.execute_scout_command("/PPS L30")
    assert calls == {"returning": 30}
    assert "Returning Customer PPS" in returning_text
    assert "coming soon" not in returning_text.lower()

    calls.clear()
    attribution_text = scout_bot.execute_scout_command("/Attribution L7")
    assert calls == {"attribution": 7}
    assert "Attribution" in attribution_text
    assert "How did they first hear about us?" in attribution_text


def test_formatters_stay_distinct():
    """Returning and attribution formatters are unchanged entry points."""
    returning = format_pps_returning({
        "days": 7,
        "response_count": 1,
        "nps_breakdown": {},
        "return_reasons": [],
        "almost_stopped_pct": 10,
        "open_text_themes": [],
        "open_text_count": 0,
    })
    attribution = format_attribution({
        "days": 7,
        "response_count": 1,
        "how_heard": [],
        "what_brought": [],
        "consideration_window": [],
        "who_for": [],
        "other_breakdown": [],
        "other_writeins_count": 0,
        "open_text_themes": [],
        "open_text_count": 0,
    })
    assert returning.startswith("*:repeat: Returning Customer PPS")
    assert attribution.startswith("*:mag: Attribution")
