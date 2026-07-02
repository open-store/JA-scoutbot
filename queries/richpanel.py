"""
Richpanel API client — fetches daily CS KPI metrics.

Metrics pulled via POST /v1/reports/query:
  - closed_conversations          (tickets closed with agent reply)
  - first_response_time_bh        (avg FRT, business hours, ms)
  - p50_first_response_time_bh    (median FRT, business hours, ms)
  - time_to_first_resolution_bh   (avg RT, business hours, ms)
  - p50_time_to_first_resolution_bh (median RT, business hours, ms)
  - csat_score                    (avg CSAT score 1-5)
  - csat_surveys_sent
  - csat_surveys_rated
  - csat_score_percentage         (% positive)

All time metrics are in milliseconds and converted to human-readable strings.

Agent names are resolved via GET /v1/users (cached at module level per process).
Negative CSAT conversations (rating 1-3) are counted and their subject lines
fetched from Snowflake for LLM analysis (reuses existing CONVERSATIONS table).

API key: RICHPANEL_API_KEY environment variable.
"""
import os
import logging
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.richpanel.com/v1"

# Roles that are actual CX agents (exclude Richpanel staff / system accounts)
_AGENT_ROLES = {"TENANT_AGENT_FULL", "TENANT_ADMIN", "TENANT_COLLABORATOR"}
# Richpanel internal staff to exclude even if role matches
_EXCLUDE_EMAILS = {
    "amit@richpanel.com",
    "deepan@richpanel.com",
    "kamaljeet@richpanel.com",
    "shreyash@richpanel.com",
    "ram@richpanel.com",
}

# Module-level cache so agent list is only fetched once per container lifetime
_agent_cache: Optional[dict[str, str]] = None  # {id: display_name}


def _headers() -> dict:
    key = os.environ.get("RICHPANEL_API_KEY", "")
    if not key:
        raise EnvironmentError("RICHPANEL_API_KEY environment variable is not set")
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "x-richpanel-key": key,
    }


def _ms_to_human(ms: Optional[float]) -> str:
    """Convert milliseconds to a human-readable string."""
    if ms is None or ms == 0:
        return "—"
    s = ms / 1000
    if s < 60:
        return f"{s:.0f}s"
    if s < 3600:
        return f"{s / 60:.1f}m"
    if s < 86400:
        return f"{s / 3600:.1f}h"
    return f"{s / 86400:.1f}d"


def _get_agent_map() -> dict[str, str]:
    """Fetch agent ID → display name map. Cached per process."""
    global _agent_cache
    if _agent_cache is not None:
        return _agent_cache

    try:
        r = requests.get(f"{_BASE_URL}/users", headers=_headers(), timeout=10)
        r.raise_for_status()
        users = r.json().get("user", [])
        mapping = {}
        for u in users:
            uid = u.get("id", "")
            email = u.get("email", "")
            role = u.get("role", "")
            if not uid:
                continue
            if email in _EXCLUDE_EMAILS:
                continue
            if role not in _AGENT_ROLES:
                continue
            name = (u.get("name") or "").strip()
            if not name or name.lower() == "undefined":
                name = email.split("@")[0].replace(".", " ").title()
            mapping[uid] = name
        _agent_cache = mapping
        return mapping
    except Exception as e:
        logger.warning(f"Could not fetch Richpanel agent list: {e}")
        return {}


def _query(payload: dict) -> dict:
    """POST /v1/reports/query and return the data dict."""
    r = requests.post(
        f"{_BASE_URL}/reports/query",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("data", {})


def _extract_totals(data: dict) -> dict:
    return {m["name"]: m["value"] for m in data.get("aggregations", {}).get("totals", [])}


def _extract_breakdowns(data: dict) -> list[dict]:
    """Return list of {agent_id, metrics_dict} from breakdowns."""
    result = []
    for b in data.get("breakdowns", []):
        dims = b.get("dimensions", [])
        if not dims:
            continue
        agent_id = dims[0].get("value", "")
        metrics = {m["name"]: m["value"] for m in b.get("metrics", [])}
        result.append({"agent_id": agent_id, "metrics": metrics})
    return result


def get_daily_report(target_date: Optional[date] = None) -> dict:
    """
    Fetch all metrics for the /daily command.

    Args:
        target_date: The date to report on. Defaults to yesterday.

    Returns a dict with:
        date_label       str   e.g. "Tuesday, Jul 1"
        totals           dict  {metric_name: value}
        by_agent         list  [{name, closed, frt_avg, frt_med, rt_avg, rt_med, csat_pct, surveys_sent, surveys_rated}]
        negatives_count  int   number of low-CSAT ratings (1-3) on that day
        source           str
    """
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    date_str = target_date.isoformat()
    date_label = target_date.strftime("%A, %b %-d")

    agent_map = _get_agent_map()

    # ── Query 1: volume + FRT + RT by agent ──────────────────────────────────
    volume_data = _query({
        "startDate": date_str,
        "endDate": date_str,
        "metrics": [
            "closed_conversations",
            "first_response_time_bh",
            "p50_first_response_time_bh",
            "time_to_first_resolution_bh",
            "p50_time_to_first_resolution_bh",
        ],
        "dimensions": "agent",
        "timezone": "America/New_York",
    })

    # ── Query 2: CSAT by agent ────────────────────────────────────────────────
    csat_data = _query({
        "startDate": date_str,
        "endDate": date_str,
        "metrics": [
            "csat_score",
            "csat_surveys_sent",
            "csat_surveys_rated",
            "csat_score_percentage",
        ],
        "dimensions": "agent",
        "timezone": "America/New_York",
    })

    # ── Query 3: negative CSAT count (rating 1-3) ────────────────────────────
    neg_data = _query({
        "startDate": date_str,
        "endDate": date_str,
        "metrics": ["csat_surveys_rated"],
        "filters": [{"field": "rating", "operator": "any_of", "value": ["1", "2", "3"]}],
        "timezone": "America/New_York",
    })
    neg_totals = _extract_totals(neg_data)
    negatives_count = int(neg_totals.get("csat_surveys_rated", 0))

    # ── Aggregate totals ──────────────────────────────────────────────────────
    vol_totals = _extract_totals(volume_data)
    csat_totals = _extract_totals(csat_data)

    totals = {
        "closed": int(vol_totals.get("closed_conversations", 0)),
        "frt_avg": _ms_to_human(vol_totals.get("first_response_time_bh")),
        "frt_med": _ms_to_human(vol_totals.get("p50_first_response_time_bh")),
        "rt_avg": _ms_to_human(vol_totals.get("time_to_first_resolution_bh")),
        "rt_med": _ms_to_human(vol_totals.get("p50_time_to_first_resolution_bh")),
        "csat_pct": csat_totals.get("csat_score_percentage"),
        "csat_score": csat_totals.get("csat_score"),
        "surveys_sent": int(csat_totals.get("csat_surveys_sent", 0)),
        "surveys_rated": int(csat_totals.get("csat_surveys_rated", 0)),
    }

    # ── Per-agent breakdown ───────────────────────────────────────────────────
    # Build lookup dicts keyed by agent_id
    vol_by_agent: dict[str, dict] = {
        b["agent_id"]: b["metrics"] for b in _extract_breakdowns(volume_data)
    }
    csat_by_agent: dict[str, dict] = {
        b["agent_id"]: b["metrics"] for b in _extract_breakdowns(csat_data)
    }

    # Merge — only include agents that appear in the volume breakdown
    by_agent = []
    for agent_id, vol_metrics in vol_by_agent.items():
        name = agent_map.get(agent_id, agent_id[:8])
        closed = int(vol_metrics.get("closed_conversations", 0))
        if closed == 0:
            continue  # skip agents with no activity
        csat_metrics = csat_by_agent.get(agent_id, {})
        by_agent.append({
            "name": name,
            "closed": closed,
            "frt_avg": _ms_to_human(vol_metrics.get("first_response_time_bh")),
            "frt_med": _ms_to_human(vol_metrics.get("p50_first_response_time_bh")),
            "rt_avg": _ms_to_human(vol_metrics.get("time_to_first_resolution_bh")),
            "rt_med": _ms_to_human(vol_metrics.get("p50_time_to_first_resolution_bh")),
            "csat_pct": csat_metrics.get("csat_score_percentage"),
            "surveys_sent": int(csat_metrics.get("csat_surveys_sent", 0)),
            "surveys_rated": int(csat_metrics.get("csat_surveys_rated", 0)),
        })

    # Sort by closed desc
    by_agent.sort(key=lambda x: x["closed"], reverse=True)

    return {
        "date_label": date_label,
        "date": date_str,
        "totals": totals,
        "by_agent": by_agent,
        "negatives_count": negatives_count,
        "source": "Richpanel API",
    }
