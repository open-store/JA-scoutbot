"""Explore the returns Snowflake table."""
from dotenv import load_dotenv
load_dotenv()
from snowflake_client import get_connection

conn = get_connection()
cur = conn.cursor()

# Row count and date range using REFUND_DATE_ET
cur.execute("""
    SELECT COUNT(*), MIN(REFUND_DATE_ET), MAX(REFUND_DATE_ET)
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS
""")
row = cur.fetchone()
print(f"Rows: {row[0]}, Date range: {row[1]} to {row[2]}")

# Sample data
cur.execute("""
    SELECT PRODUCT_TITLE, SKU, QTY_RETURNED, GROSS_QUANTITY, 
           REFUND_NOTE, RESTOCK_TYPE, RETURN_PLATFORM, IS_EXCHANGE,
           IS_REDO_RETURN, DIVISION, DEPARTMENT, CATEGORY, REFUND_DATE_ET
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS
    ORDER BY REFUND_DATE_ET DESC
    LIMIT 10
""")
rows = cur.fetchall()
print("\n=== Sample rows (most recent) ===")
for r in rows:
    print(f"  Product: {r[0]}")
    print(f"  SKU: {r[1]}, Qty returned: {r[2]}, Gross qty: {r[3]}")
    print(f"  Refund note: {r[4]}")
    print(f"  Restock: {r[5]}, Platform: {r[6]}, Exchange: {r[7]}, Redo: {r[8]}")
    print(f"  Division: {r[9]}, Dept: {r[10]}, Category: {r[11]}")
    print(f"  Date: {r[12]}")
    print()

# Return rate overall last 30 days
cur.execute("""
    SELECT SUM(QTY_RETURNED) as total_returned,
           SUM(GROSS_QUANTITY) as total_gross,
           ROUND(SUM(QTY_RETURNED) * 100.0 / NULLIF(SUM(GROSS_QUANTITY), 0), 2) as return_pct,
           COUNT(DISTINCT PRODUCT_TITLE) as unique_products,
           COUNT(*) as total_lines
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS
    WHERE REFUND_DATE_ET >= DATEADD(day, -30, CURRENT_DATE())
""")
row = cur.fetchone()
print(f"=== Last 30 days ===")
print(f"Total returned: {row[0]}, Gross qty: {row[1]}, Return %: {row[2]}%")
print(f"Unique products: {row[3]}, Total line items: {row[4]}")

# Top returned products last 30 days
cur.execute("""
    SELECT PRODUCT_TITLE,
           SUM(QTY_RETURNED) as qty_returned,
           SUM(GROSS_QUANTITY) as gross_qty,
           ROUND(SUM(QTY_RETURNED) * 100.0 / NULLIF(SUM(GROSS_QUANTITY), 0), 2) as return_pct
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS
    WHERE REFUND_DATE_ET >= DATEADD(day, -30, CURRENT_DATE())
    GROUP BY PRODUCT_TITLE
    ORDER BY qty_returned DESC
    LIMIT 10
""")
rows = cur.fetchall()
print("\n=== Top returned products (L30) ===")
for r in rows:
    print(f"  {r[0]}: {r[1]} returned / {r[2]} gross = {r[3]}%")

# Check refund notes distribution
cur.execute("""
    SELECT REFUND_NOTE, COUNT(*) as cnt
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS
    WHERE REFUND_DATE_ET >= DATEADD(day, -30, CURRENT_DATE())
      AND REFUND_NOTE IS NOT NULL AND REFUND_NOTE != ''
    GROUP BY REFUND_NOTE
    ORDER BY cnt DESC
    LIMIT 10
""")
rows = cur.fetchall()
print("\n=== Top refund notes (L30) ===")
for r in rows:
    print(f"  {r[0]}: {r[1]}")

# Check exchange vs return split
cur.execute("""
    SELECT IS_EXCHANGE, IS_REDO_RETURN, IS_CANCELLATION, COUNT(*) as cnt
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS
    WHERE REFUND_DATE_ET >= DATEADD(day, -30, CURRENT_DATE())
    GROUP BY IS_EXCHANGE, IS_REDO_RETURN, IS_CANCELLATION
    ORDER BY cnt DESC
""")
rows = cur.fetchall()
print("\n=== Exchange/Redo/Cancel split (L30) ===")
for r in rows:
    print(f"  Exchange={r[0]}, Redo={r[1]}, Cancel={r[2]}: {r[3]}")

conn.close()
