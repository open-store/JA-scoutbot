"""`/returns`: refund volume and mix, plus return and exchange rates.

Refund volume and mix read ``ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS``,
a Shopify refund export dated by refund date. It mixes Redo and non-Redo
refunds, includes cancellations, and has no open returns, so its shares are
shares of refund lines, not rates against orders.

Return and exchange rates are Redo units over eligible delivered Shopify units
for a delivery cohort (``scout.returns.metrics``). They stay unavailable until
``ANALYTICS.RETURN_REDO`` and the Shopify order lines are verified and the
cohort maturity horizon is known. ``/returns`` never marks a rate validated.
"""

from scout.integrations.snowflake import execute_query_dict, get_connection
from scout.returns import cohorts, metrics
from scout.returns.metrics import (
    CohortScope,
    RatesReport,
    UnitCounts,
    evaluate_rates,
    rollup_query,
    source_blockers,
    unavailable_results,
)

TABLE = "ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS"
RATE_SCOPE_LABEL = "online store, DTC"


def run_return_rates(days: int, product_filter: str | None = None) -> RatesReport:
    """Rates for the mature ``L{days}`` delivery cohort, or why they are unavailable."""
    blockers = list(dict.fromkeys(source_blockers().values()))
    maturity = cohorts.MATURITY_DAYS
    if maturity is None:
        blockers.append("cohort maturity rule not yet confirmed from `SHOPIFY_AGG_POST_PURCHASE`")
    relation = metrics.COHORT_UNITS_RELATION
    if relation is None:
        blockers.append("the Redo-to-Shopify join is not built until the Redo table is profiled")
    if product_filter:
        blockers.append("product-level rates need SKUs resolved from the catalog")

    window = cohorts.mature_window(days, maturity, cohorts.today_et()) if maturity else None
    if blockers or window is None or relation is None:
        return RatesReport(
            results=unavailable_results("; ".join(blockers)),
            window=window,
            scope_label=RATE_SCOPE_LABEL,
            blockers=tuple(blockers),
        )

    sql, params = rollup_query(relation, CohortScope(window=window))
    rows = execute_query_dict(sql, params)
    counts = UnitCounts.from_row(rows[0]) if rows else UnitCounts()
    results = evaluate_rates(counts, mature=window.mature, reconciled=False)
    return RatesReport(results=results, window=window, scope_label=RATE_SCOPE_LABEL)


def run_returns(days: int, product_filter: str | None = None) -> dict:
    """Run returns analysis for the given time window.

    Returns a dict with: total_returns, total_qty_returned,
    three-way type breakdown (exchange / cancellation / straight return),
    redo vs manual within exchanges, avg_days_to_return, top_products,
    top_categories, top_refund_notes, period_start, period_end, and changes.
    """
    conn = get_connection()
    cur = conn.cursor()

    product_clause = ""
    if product_filter:
        safe = product_filter.replace("'", "''")
        product_clause = f"AND LOWER(PRODUCT_TITLE) LIKE '%{safe.lower()}%'"

    # ── Current period — three-way breakdown ───────────────────────
    # Priority: Cancellation > Exchange > Straight Return
    # This avoids double-counting (a row can be IS_EXCHANGE=True AND IS_CANCELLATION=True)
    cur.execute(f"""
        SELECT
            COUNT(*)                                          AS total_lines,
            SUM(QTY_RETURNED)                                 AS total_qty,
            SUM(CASE WHEN IS_CANCELLATION THEN 1 ELSE 0 END) AS cancellations,
            SUM(CASE WHEN IS_CANCELLATION THEN QTY_RETURNED ELSE 0 END) AS cancel_qty,
            SUM(CASE WHEN NOT IS_CANCELLATION AND IS_EXCHANGE THEN 1 ELSE 0 END) AS exchanges,
            SUM(CASE WHEN NOT IS_CANCELLATION AND IS_EXCHANGE THEN QTY_RETURNED ELSE 0 END) AS exchange_qty,
            SUM(CASE WHEN NOT IS_CANCELLATION AND NOT IS_EXCHANGE THEN 1 ELSE 0 END) AS straight_returns,
            SUM(CASE WHEN NOT IS_CANCELLATION AND NOT IS_EXCHANGE THEN QTY_RETURNED ELSE 0 END) AS straight_qty,
            SUM(CASE WHEN IS_REDO_RETURN THEN 1 ELSE 0 END)  AS redo_returns,
            ROUND(AVG(DAYS_TO_RETURN), 1)                     AS avg_days,
            MIN(REFUND_DATE_ET)                                AS period_start,
            MAX(REFUND_DATE_ET)                                AS period_end
        FROM {TABLE}
        WHERE REFUND_DATE_ET >= DATEADD(day, -{days}, CURRENT_DATE())
          AND REFUND_DATE_ET < CURRENT_DATE()
          {product_clause}
    """)
    row = cur.fetchone()
    total_lines = row[0] or 0
    total_qty = row[1] or 0
    cancellations = row[2] or 0
    cancel_qty = row[3] or 0
    exchanges = row[4] or 0
    exchange_qty = row[5] or 0
    straight_returns = row[6] or 0
    straight_qty = row[7] or 0
    redo_returns = row[8] or 0
    avg_days = row[9]
    period_start = row[10]
    period_end = row[11]

    # Top returned products
    cur.execute(f"""
        SELECT PRODUCT_TITLE,
               SUM(QTY_RETURNED) AS qty,
               COUNT(*)          AS lines
        FROM {TABLE}
        WHERE REFUND_DATE_ET >= DATEADD(day, -{days}, CURRENT_DATE())
          AND REFUND_DATE_ET < CURRENT_DATE()
          {product_clause}
        GROUP BY PRODUCT_TITLE
        ORDER BY qty DESC
        LIMIT 5
    """)
    top_products = [
        {"product": r[0], "qty": r[1], "lines": r[2]}
        for r in cur.fetchall()
    ]

    # Top categories
    cur.execute(f"""
        SELECT COALESCE(CATEGORY, 'Uncategorized'),
               SUM(QTY_RETURNED) AS qty
        FROM {TABLE}
        WHERE REFUND_DATE_ET >= DATEADD(day, -{days}, CURRENT_DATE())
          AND REFUND_DATE_ET < CURRENT_DATE()
          {product_clause}
        GROUP BY CATEGORY
        ORDER BY qty DESC
        LIMIT 5
    """)
    top_categories = [
        {"category": r[0], "qty": r[1]}
        for r in cur.fetchall()
    ]

    # Top refund notes (excluding generic ones)
    cur.execute(f"""
        SELECT REFUND_NOTE, COUNT(*) AS cnt
        FROM {TABLE}
        WHERE REFUND_DATE_ET >= DATEADD(day, -{days}, CURRENT_DATE())
          AND REFUND_DATE_ET < CURRENT_DATE()
          AND REFUND_NOTE IS NOT NULL
          AND REFUND_NOTE != ''
          AND REFUND_NOTE NOT IN ('Redo', 'Order canceled')
          {product_clause}
        GROUP BY REFUND_NOTE
        ORDER BY cnt DESC
        LIMIT 5
    """)
    top_refund_notes = [
        {"note": r[0], "count": r[1]}
        for r in cur.fetchall()
    ]

    # Return platform breakdown
    cur.execute(f"""
        SELECT COALESCE(RETURN_PLATFORM, 'Unknown'),
               COUNT(*) AS cnt
        FROM {TABLE}
        WHERE REFUND_DATE_ET >= DATEADD(day, -{days}, CURRENT_DATE())
          AND REFUND_DATE_ET < CURRENT_DATE()
          {product_clause}
        GROUP BY RETURN_PLATFORM
        ORDER BY cnt DESC
    """)
    platforms = [
        {"platform": r[0], "count": r[1]}
        for r in cur.fetchall()
    ]

    # ── Prior period ────────────────────────────────────────────────
    cur.execute(f"""
        SELECT
            COUNT(*)          AS total_lines,
            SUM(QTY_RETURNED) AS total_qty
        FROM {TABLE}
        WHERE REFUND_DATE_ET >= DATEADD(day, -{days * 2}, CURRENT_DATE())
          AND REFUND_DATE_ET < DATEADD(day, -{days}, CURRENT_DATE())
          {product_clause}
    """)
    prior = cur.fetchone()
    prior_lines = prior[0] or 0
    prior_qty = prior[1] or 0

    conn.close()

    # ── Compute changes ─────────────────────────────────────────────
    volume_change_pct = None
    if prior_lines > 0:
        volume_change_pct = round((total_lines - prior_lines) / prior_lines * 100, 1)

    qty_change_pct = None
    if prior_qty > 0:
        qty_change_pct = round((total_qty - prior_qty) / prior_qty * 100, 1)

    # Percentages based on total lines
    safe_div = total_lines if total_lines > 0 else 1
    exchange_pct = round(exchanges / safe_div * 100, 1)
    cancel_pct = round(cancellations / safe_div * 100, 1)
    straight_pct = round(straight_returns / safe_div * 100, 1)
    redo_pct = round(redo_returns / safe_div * 100, 1)

    return {
        "total_lines": total_lines,
        "total_qty_returned": total_qty,
        "exchanges": exchanges,
        "exchange_qty": exchange_qty,
        "cancellations": cancellations,
        "cancel_qty": cancel_qty,
        "straight_returns": straight_returns,
        "straight_qty": straight_qty,
        "redo_returns": redo_returns,
        "exchange_pct": exchange_pct,
        "cancel_pct": cancel_pct,
        "straight_pct": straight_pct,
        "redo_pct": redo_pct,
        "avg_days_to_return": avg_days,
        "top_products": top_products,
        "top_categories": top_categories,
        "top_refund_notes": top_refund_notes,
        "platforms": platforms,
        "period_start": str(period_start) if period_start else "N/A",
        "period_end": str(period_end) if period_end else "N/A",
        "prior_lines": prior_lines,
        "prior_qty": prior_qty,
        "volume_change_pct": volume_change_pct,
        "qty_change_pct": qty_change_pct,
        "product_filter": product_filter,
        "rates": run_return_rates(days, product_filter),
    }
