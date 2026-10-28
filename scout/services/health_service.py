"""
Customer-health report for general `/voc`.

Runs the existing source modules concurrently, turns each result into a
scorecard entry (``scout.health.sources``), computes the versioned score
(``scout.health.score``), and synthesises cited cross-source themes
(``scout.health.themes``). A source that fails or times out becomes an
unavailable entry with its reason; it never fails the reply or turns into
a zero.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from queries.csat import run_csat
from queries.nps import get_nps_scores, get_nps_trend, get_open_text_evidence
from queries.pps import get_attribution, get_new_pps, get_returning_pps
from queries.returns import run_return_rates
from queries.reviews import run_reviews
from scout.health import sources, themes
from scout.health.components import ScorecardEntry
from scout.health.score import DEFAULT_CONFIG, HealthScoreConfig, compute_health_score
from scout.repositories.health_repository import (
    KNOCOMMERCE_RESPONSE,
    RICHPANEL_CONVERSATIONS,
    fetch_opening_messages,
    fetch_sync_times,
)
from scout.routing.command_parser import ParsedCommand
from scout.services.product_catalog_service import get_catalog

logger = logging.getLogger("scout.services.health")

SOURCE_TIMEOUT_SECONDS = 120

TICKET_SAMPLE = 120
NPS_COMMENT_SAMPLE = 60
PPS_COMMENT_SAMPLE = 40

SCORECARD_ORDER = ("support_csat", "nps_survey", "reviews", "returns", "returning_pps", "new_pps", "attribution")

_FILTER_KEYS = ("channel", "tag", "agent")


def _run_all(tasks: Mapping[str, Callable[[], Any]], timeout: float) -> Tuple[Dict[str, Any], Dict[str, str]]:
    results: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="health")
    futures = {pool.submit(fn): name for name, fn in tasks.items()}
    done, pending = wait(futures, timeout=timeout)
    for future in done:
        name = futures[future]
        try:
            results[name] = future.result()
        except Exception as e:
            logger.warning("Health source %s failed: %s: %s", name, type(e).__name__, e)
            errors[name] = f"{type(e).__name__} reading this source"
    for future in pending:
        errors[futures[future]] = f"timed out after {timeout:.0f}s"
    pool.shutdown(wait=False, cancel_futures=True)
    return results, errors


def build_health_report(
    cmd: ParsedCommand,
    voc_data: Optional[Mapping[str, Any]] = None,
    config: HealthScoreConfig = DEFAULT_CONFIG,
    timeout: float = SOURCE_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    days = cmd.days
    filters = {k: str(cmd.filters[k]) for k in _FILTER_KEYS if cmd.filters and cmd.filters.get(k)}
    if cmd.start_date is None or cmd.end_date is None:
        raise ValueError("build_health_report needs a command with a resolved window")
    start = cmd.start_date.strftime("%Y-%m-%d %H:%M:%S")
    end = cmd.end_date.strftime("%Y-%m-%d %H:%M:%S")

    tasks: Dict[str, Callable[[], Any]] = {
        "richpanel_sync": lambda: fetch_sync_times(RICHPANEL_CONVERSATIONS),
        "knocommerce_sync": lambda: fetch_sync_times(KNOCOMMERCE_RESPONSE),
        "csat": lambda: run_csat(cmd),
        "nps_scores": lambda: get_nps_scores(days),
        "nps_trend": lambda: get_nps_trend(days),
        "returning_pps": lambda: get_returning_pps(days),
        "new_pps": lambda: get_new_pps(days),
        "attribution": lambda: get_attribution(days),
        "reviews": lambda: run_reviews(days),
        "returns": lambda: run_return_rates(days),
        "ticket_evidence": lambda: fetch_opening_messages(start, end, filters, TICKET_SAMPLE),
        "nps_evidence": lambda: get_open_text_evidence(days, "NPS Survey", NPS_COMMENT_SAMPLE),
        "returning_pps_evidence": lambda: get_open_text_evidence(days, "Returning Customer PPS", PPS_COMMENT_SAMPLE),
        "new_pps_evidence": lambda: get_open_text_evidence(days, "PPS - New Customers", PPS_COMMENT_SAMPLE),
        "catalog": get_catalog,
    }
    results, errors = _run_all(tasks, timeout)

    def err(name: str) -> str:
        return errors.get(name, "")

    richpanel_fresh = sources.freshness_from(results.get("richpanel_sync"), config.stale_after_hours)
    knocommerce_fresh = sources.freshness_from(results.get("knocommerce_sync"), config.stale_after_hours)
    ticket_window = f"{cmd.timeframe_label}, complete UTC days"
    survey_window = sources.knocommerce_window(days)

    entries: Dict[str, ScorecardEntry] = {
        "support_csat": sources.support_csat_entry(results.get("csat"), ticket_window, richpanel_fresh,
                                                   bool(filters), err("csat")),
        "nps_survey": sources.nps_entry(results.get("nps_scores"), results.get("nps_trend"), survey_window,
                                        knocommerce_fresh, err("nps_scores")),
        "reviews": sources.reviews_entry(results.get("reviews"), days, err("reviews")),
        "returns": sources.returns_entry(results.get("returns"), err("returns")),
        "returning_pps": sources.pps_entry("returning_pps", "Returning Customer PPS", "Returning Customer PPS",
                                           results.get("returning_pps"), survey_window, knocommerce_fresh,
                                           err("returning_pps")),
        "new_pps": sources.pps_entry("new_pps", "New Customer PPS", "New Customer PPS",
                                     results.get("new_pps"), survey_window, knocommerce_fresh, err("new_pps")),
        "attribution": sources.attribution_entry(results.get("attribution"), survey_window, knocommerce_fresh,
                                                 err("attribution")),
    }
    if filters:
        note = "Survey, review, and returns data cover all customers; the /voc filters apply to tickets only."
        for key in ("nps_survey", "reviews", "returns", "returning_pps", "new_pps", "attribution"):
            entry = entries[key]
            entries[key] = replace(entry, coverage_note=f"{entry.coverage_note} {note}".strip())

    score = compute_health_score(entries, config)

    synthesis = _themes(results, errors, entries, voc_data, filters)

    return {
        "version": config.version,
        "window": cmd.timeframe_label,
        "days": days,
        "filters": filters,
        "scorecard": [entries[k].to_dict() for k in SCORECARD_ORDER],
        "score": score.to_dict(),
        "score_config": config_summary(config),
        "themes": synthesis.to_dict(),
        "errors": errors,
    }


def _themes(results: Mapping[str, Any], errors: Mapping[str, str], entries: Mapping[str, ScorecardEntry],
            voc_data: Optional[Mapping[str, Any]], filters: Mapping[str, str]) -> themes.ThemeSynthesis:
    ticket_rows = results.get("ticket_evidence") or []
    nps_rows = results.get("nps_evidence") or []
    rpps_rows = results.get("returning_pps_evidence") or []
    npps_rows = results.get("new_pps_evidence") or []
    review_rows = (results.get("reviews") or {}).get("evidence") or []

    total_tickets = None
    if voc_data:
        total_tickets = int((voc_data.get("volume") or {}).get("TOTAL_CONVERSATIONS") or 0) or None

    def eligible(key: str) -> Optional[int]:
        entry = entries.get(key)
        return entry.eligible if entry and entry.eligible is not None else (entry.sample if entry else None)

    samples: List[themes.SourceSample] = [
        themes.SourceSample("tickets", total_tickets, "tickets in the window" + (" (filtered)" if filters else ""),
                            len(ticket_rows), TICKET_SAMPLE,
                            note="Opening customer message per ticket, spread across the window"
                            + ("; " + errors["ticket_evidence"] if "ticket_evidence" in errors else "")),
        themes.SourceSample("nps", eligible("nps_survey"), "survey responses", len(nps_rows), NPS_COMMENT_SAMPLE,
                            note=errors.get("nps_evidence", "")),
        themes.SourceSample("returning_pps", eligible("returning_pps"), "survey responses", len(rpps_rows),
                            PPS_COMMENT_SAMPLE, note=errors.get("returning_pps_evidence", "")),
        themes.SourceSample("new_pps", eligible("new_pps"), "survey responses", len(npps_rows), PPS_COMMENT_SAMPLE,
                            note=errors.get("new_pps_evidence", "")),
        themes.SourceSample("reviews", eligible("reviews"), "approved reviews", len(review_rows),
                            2 * 30, note="Most recent 1–3★ and 4–5★ reviews with text, up to 30 each"),
    ]
    items = {
        "tickets": themes.ticket_items(ticket_rows),
        "nps": themes.survey_items("nps", nps_rows),
        "returning_pps": themes.survey_items("returning_pps", rpps_rows),
        "new_pps": themes.survey_items("new_pps", npps_rows),
        "reviews": themes.review_items(review_rows),
    }
    return themes.synthesize(items, samples, catalog=results.get("catalog"))


def config_summary(config: HealthScoreConfig) -> Dict[str, object]:
    return {
        "version": config.version,
        "provisional": config.provisional,
        "min_weight_coverage": config.min_weight_coverage,
        "min_components": config.min_components,
        "stale_after_hours": config.stale_after_hours,
        "reference_note": config.reference_note,
        "components": [
            {
                "key": c.key,
                "label": c.label,
                "weight": c.weight,
                "normalization": c.normalization.label,
                "direction": c.normalization.direction,
                "min_sample": c.min_sample,
                "sample_label": c.sample_label,
                "accepts": list(c.accept_statuses),
                "rationale": c.rationale,
            }
            for c in config.components
        ],
    }
