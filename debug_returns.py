"""Debug the returns data to understand exchange vs return classification."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()
from snowflake_client import get_connection

conn = get_connection()
cur = conn.cursor()

# Check all columns available
cur.execute("SELECT * FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS LIMIT 1")
cols = [desc[0] for desc in cur.description]
print("ALL COLUMNS:")
for c in cols:
    print(f"  {c}")
print()

# Check the IS_EXCHANGE column distribution
cur.execute("""
    SELECT IS_EXCHANGE, COUNT(*) as cnt 
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS 
    WHERE REFUND_DATE_UTC >= DATEADD(day, -30, CURRENT_DATE())
    GROUP BY IS_EXCHANGE
    ORDER BY cnt DESC
""")
print("IS_EXCHANGE distribution (L30):")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")
print()

# Check what IS_EXCHANGE actually contains
cur.execute("""
    SELECT DISTINCT IS_EXCHANGE 
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS 
    LIMIT 20
""")
print("DISTINCT IS_EXCHANGE values:")
for row in cur.fetchall():
    print(f"  '{row[0]}' (type: {type(row[0]).__name__})")
print()

# Check IS_REDO distribution
cur.execute("""
    SELECT IS_REDO_RETURN, COUNT(*) as cnt 
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS 
    WHERE REFUND_DATE_UTC >= DATEADD(day, -30, CURRENT_DATE())
    GROUP BY IS_REDO_RETURN
    ORDER BY cnt DESC
""")
print("IS_REDO_RETURN distribution (L30):")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")
print()

# Check IS_CANCELLATION distribution
cur.execute("""
    SELECT IS_CANCELLATION, COUNT(*) as cnt 
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS 
    WHERE REFUND_DATE_UTC >= DATEADD(day, -30, CURRENT_DATE())
    GROUP BY IS_CANCELLATION
    ORDER BY cnt DESC
""")
print("IS_CANCELLATION distribution (L30):")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")
print()

# Check RETURN_PLATFORM distribution
cur.execute("""
    SELECT RETURN_PLATFORM, COUNT(*) as cnt 
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS 
    WHERE REFUND_DATE_UTC >= DATEADD(day, -30, CURRENT_DATE())
    GROUP BY RETURN_PLATFORM
    ORDER BY cnt DESC
""")
print("RETURN_PLATFORM distribution (L30):")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")
print()

# Look at a sample of non-exchange, non-redo, non-cancellation rows
cur.execute("""
    SELECT REFUND_ID, PRODUCT_TITLE, IS_EXCHANGE, IS_REDO_RETURN, IS_CANCELLATION, 
           RETURN_PLATFORM, REFUND_NOTE, QTY_RETURNED, GROSS_QUANTITY
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS 
    WHERE REFUND_DATE_UTC >= DATEADD(day, -30, CURRENT_DATE())
      AND IS_EXCHANGE = FALSE
    LIMIT 10
""")
print("Sample NON-exchange rows:")
for row in cur.fetchall():
    print(f"  Order: {row[0]}, Product: {row[1][:40]}, Exchange: {row[2]}, Redo: {row[3]}, Cancel: {row[4]}, Platform: {row[5]}, Note: {str(row[6])[:60]}, Qty: {row[7]}/{row[8]}")
print()

# Get the actual breakdown: exchange vs straight return vs cancellation
cur.execute("""
    SELECT 
        CASE 
            WHEN IS_CANCELLATION = TRUE THEN 'Cancellation'
            WHEN IS_EXCHANGE = TRUE THEN 'Exchange'
            ELSE 'Straight Return/Refund'
        END as return_type,
        COUNT(*) as cnt,
        SUM(QTY_RETURNED) as units
    FROM ANALYTICS.DBT_EXPORTS_OS.EXPORT_CSX__RETURNS 
    WHERE REFUND_DATE_UTC >= DATEADD(day, -30, CURRENT_DATE())
    GROUP BY return_type
    ORDER BY cnt DESC
""")
print("Proper return type breakdown (L30):")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]} lines, {row[2]} units")

cur.close()
conn.close()
