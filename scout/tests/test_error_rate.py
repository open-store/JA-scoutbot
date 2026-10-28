"""Support error rate: Issue sub-category tags ÷ all conversations.

Snowflake is mocked. Nothing here opens a connection.
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from queries import errors
from scout.formatting.errors import format_errors
from scout.health import error_rate as er
from scout.returns.metrics import MetricStatus
from scout.routing.command_parser import ParsedCommand
from scout.taxonomy import issue_tags
from scout.taxonomy.issue_tags import (
    ERROR_RATE_GROUP,
    ISSUE,
    ISSUE_SUBCATEGORIES,
    issue_groups,
    issue_tag_names,
    resolve_issue_tags,
)
from scout.taxonomy.tag_ids import TAG_NAME_TO_UUIDS
from scout.taxonomy.tags import tag_names_for_group

ET = ZoneInfo("America/New_York")
COUNTS = {"L7": (3, 800), "L30": (21, 3556), "L180": (152, 18530), "current": (21, 3556), "previous": (16, 3200)}


def fake_error_rate_rows(sql, params=None):
    params = params or {}
    labels = [v for k, v in params.items() if k.startswith("label_")]
    return [{"LABEL": label, "TOTAL_CONVERSATIONS": COUNTS[label][1], "TAGGED_CONVERSATIONS": COUNTS[label][0]}
            for label in labels]


def _keyword_rows(sql, params=None):
    if "error_category" in sql:
        return [{"ERROR_CATEGORY": "Discount/Promo Code Issues", "CNT": 7}]
    if "ticket_type" in sql:
        return []
    if "LIMIT 15" in sql:
        return []
    return [{"TOTAL_CONVERSATIONS": 3550, "ERROR_CONVERSATIONS": 60, "ERROR_RATE_PCT": 1.7}]


def fake_errors_query(sql, params=None):
    if "TAGGED_CONVERSATIONS" in sql.upper():
        return fake_error_rate_rows(sql, params)
    return _keyword_rows(sql, params)


def _cmd(days=30):
    end = datetime(2026, 10, 27, tzinfo=ET)
    return ParsedCommand(command="errors", days=days, start_date=end - timedelta(days=days), end_date=end)


# ── Tag set ──────────────────────────────────────────────────────

def test_default_group_is_storefront_ux_and_every_group_resolves():
    assert ERROR_RATE_GROUP == "Storefront / UX"
    assert ERROR_RATE_GROUP in issue_groups() and issue_groups()[-1] == ISSUE
    for group in issue_groups():
        uuids, matched, _ = resolve_issue_tags(group)
        assert uuids and matched, group


def test_every_live_tag_is_in_the_uuid_map():
    for sub in ISSUE_SUBCATEGORIES.values():
        assert all(TAG_NAME_TO_UUIDS.get(name) for name in sub.live_tags), sub.name


def test_storefront_ux_matches_the_web_errors_scope_without_changing_it():
    live = ISSUE_SUBCATEGORIES["Storefront / UX"].live_tags
    assert live == tag_names_for_group("web_errors") == frozenset({"web_issues", "storefront-experience"})
    uuids, matched, unresolved = resolve_issue_tags("Storefront / UX")
    assert matched == ("storefront-experience", "web_issues") and len(uuids) == 3
    assert "Tech Issue - Website Error" in unresolved and len(unresolved) == 9


def test_all_of_issue_adds_the_category_tag_to_every_sub_category():
    names = issue_tag_names(ISSUE)
    assert "issue" in names
    for sub in ISSUE_SUBCATEGORIES.values():
        assert sub.tag_names <= names
    with pytest.raises(KeyError):
        issue_tag_names("Not a group")


# ── Windows and SQL ──────────────────────────────────────────────

def test_standard_windows_are_complete_new_york_days():
    l7, l30, l180 = er.standard_rate_windows(date(2026, 10, 27))
    assert (l7.label, l7.start.isoformat(), l7.end.isoformat()) == (
        "L7", "2026-10-20T00:00:00-04:00", "2026-10-27T00:00:00-04:00")
    assert l30.start.date() == date(2026, 9, 27) and l180.start.date() == date(2026, 4, 30)
    assert l7.describe() == "Oct 20 – Oct 26, 2026 (ET)"
    across_dst = er.standard_rate_windows(date(2026, 11, 10))[1]
    assert across_dst.start.utcoffset() == timedelta(hours=-4)
    assert across_dst.end.utcoffset() == timedelta(hours=-5)


def test_query_binds_bounds_and_tag_uuids():
    windows = er.standard_rate_windows(date(2026, 10, 27))
    sql, params = er.error_rate_query(windows, ERROR_RATE_GROUP)
    uuids, _, _ = resolve_issue_tags(ERROR_RATE_GROUP)
    assert sorted(v for k, v in params.items() if k.startswith("tag_")) == sorted(uuids)
    assert params["start_0"] == "2026-10-20T00:00:00-04:00"
    assert not any(u in sql for u in uuids) and "2026-10" not in sql
    assert "_FIVETRAN_DELETED = FALSE" in sql and "PARSE_JSON(c.TAGS)" in sql
    assert "COUNT(DISTINCT t.ID) AS tagged_conversations" in sql
    assert sql.startswith("WITH") and ";" not in sql
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE)\b", sql.upper())


def test_query_rejects_naive_bounds_and_empty_windows():
    naive = er.RateWindow("L7", datetime(2026, 10, 20), datetime(2026, 10, 27))
    with pytest.raises(ValueError):
        er.error_rate_query([naive], ERROR_RATE_GROUP)
    with pytest.raises(ValueError):
        er.error_rate_query([], ERROR_RATE_GROUP)


# ── Rates ────────────────────────────────────────────────────────

def test_run_error_rates_reports_provisional_rates(monkeypatch):
    monkeypatch.setattr(errors, "execute_query_dict", fake_error_rate_rows)
    report = errors.run_error_rates(er.standard_rate_windows(date(2026, 10, 27)))
    l30 = report.rate("L30")
    assert l30 is not None and (l30.tagged, l30.total) == (21, 3556)
    assert l30.value_pct == pytest.approx(100 * 21 / 3556)
    assert l30.status is MetricStatus.PROVISIONAL
    assert report.group == ERROR_RATE_GROUP and report.tags_matched == ("storefront-experience", "web_issues")


def test_missing_rows_and_zero_totals_are_unavailable():
    windows = er.standard_rate_windows(date(2026, 10, 27))
    report = er.report_from_rows(windows, [{"label": "L7", "total_conversations": 0, "tagged_conversations": 0}],
                                 ERROR_RATE_GROUP)
    assert [r.status for r in report.rates] == [MetricStatus.UNAVAILABLE] * 3
    assert report.rates[0].reason == "no conversations in the window"
    assert report.rates[1].reason == "count missing"
    with pytest.raises(ValueError):
        er.evaluate(windows[0], tagged=5, total=4)


def test_group_with_no_uuid_is_unavailable_without_a_query(monkeypatch):
    monkeypatch.setattr(issue_tags, "TAG_NAME_TO_UUIDS", {})

    def no_query(sql, params=None):
        raise AssertionError("no statement should run")

    monkeypatch.setattr(errors, "execute_query_dict", no_query)
    report = errors.run_error_rates(er.standard_rate_windows(date(2026, 10, 27)))
    assert {r.status for r in report.rates} == {MetricStatus.UNAVAILABLE}
    assert report.rates[0].reason == er.NO_TAGS_REASON and report.tags_matched == ()


# ── /errors ──────────────────────────────────────────────────────

def test_run_errors_uses_the_command_window_in_new_york(monkeypatch):
    seen = []

    def capture(sql, params=None):
        seen.append((sql, params))
        return fake_errors_query(sql, params)

    monkeypatch.setattr(errors, "execute_query_dict", capture)
    data = errors.run_errors(_cmd(30))
    rate_params = [p for s, p in seen if p and "tag_0" in p]
    assert len(rate_params) == 1
    assert rate_params[0]["start_0"] == "2026-09-27T00:00:00-04:00"
    assert rate_params[0]["end_1"] == "2026-09-27T00:00:00-04:00"
    current = data["error_rate"].rate("current")
    assert (current.tagged, current.total) == (21, 3556)


def test_errors_reply_shows_the_tag_rate_with_its_label(monkeypatch):
    monkeypatch.setattr(errors, "execute_query_dict", fake_errors_query)
    text = format_errors(errors.run_errors(_cmd(30)))
    assert "• *Error rate (Storefront / UX tags, provisional):* 0.6% of conversations (21 of 3,556)" in text
    assert "Error-related tickets represented 0.6% of support volume over the last 30 complete days" in text
    assert "• *Change:* +0.1 pts vs. previous 30 days" in text
    assert "• *Total error-related tickets:* 60" in text
    assert "• *Discount/Promo Code Issues:* 7 tickets" in text
    assert "1.7%" not in text
    assert "The error rate counts tickets carrying Storefront / UX taxonomy tags." in text
    assert "Total error-related tickets, categories, and CSAT impact use subject-line keyword matching" in text
    assert "require mapping" not in text


def test_errors_reply_says_when_the_rate_is_unavailable(monkeypatch):
    monkeypatch.setattr(issue_tags, "TAG_NAME_TO_UUIDS", {})
    monkeypatch.setattr(errors, "execute_query_dict", fake_errors_query)
    text = format_errors(errors.run_errors(_cmd(7)))
    assert f"• *Error rate (Storefront / UX tags, provisional):* unavailable ({er.NO_TAGS_REASON})" in text
    assert "support error rate is unavailable" in text
    assert "*Change:*" not in text
