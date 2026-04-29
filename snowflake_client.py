"""
Scout Snowflake Client
Handles connection, query execution, and result formatting for Snowflake.
"""

import os
import snowflake.connector
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
from dotenv import load_dotenv
from typing import Any

# Load .env if present (local dev); Railway injects env vars directly
load_dotenv("/home/ubuntu/scout/.env")


def _get_private_key() -> bytes:
    """
    Load the RSA private key for Snowflake authentication.
    Supports two modes:
      1. SNOWFLAKE_RSA_KEY_PATH — path to a .p8 file (local dev)
      2. SNOWFLAKE_RSA_KEY_CONTENT — PEM content as a string (Railway/cloud)
    """
    passphrase_str = os.environ.get("SNOWFLAKE_RSA_KEY_PASSPHRASE", "")
    passphrase = passphrase_str.encode() if passphrase_str else None

    key_content = os.environ.get("SNOWFLAKE_RSA_KEY_CONTENT")
    if key_content:
        # Cloud deployment: key content passed as env var
        # Replace literal \n with actual newlines in case it was set that way
        key_bytes = key_content.replace("\\n", "\n").encode()
    else:
        # Local dev: read from file path
        key_path = os.environ["SNOWFLAKE_RSA_KEY_PATH"]
        with open(key_path, "rb") as f:
            key_bytes = f.read()

    p_key = serialization.load_pem_private_key(
        key_bytes,
        password=passphrase,
        backend=default_backend()
    )
    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )


def get_connection():
    """Create and return a Snowflake connection."""
    private_key_bytes = _get_private_key()
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=private_key_bytes,
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        schema=os.environ["SNOWFLAKE_SCHEMA"],
        role=os.environ["SNOWFLAKE_ROLE"],
    )


def execute_query(sql: str, params: dict = None) -> tuple[list[str], list[tuple]]:
    """
    Execute a read-only SQL query and return (column_names, rows).
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        col_names = [desc[0] for desc in cur.description]
        rows = cur.fetchall()
        cur.close()
        return col_names, rows
    finally:
        conn.close()


def execute_query_dict(sql: str, params: dict = None) -> list[dict[str, Any]]:
    """
    Execute a read-only SQL query and return a list of dicts.
    """
    col_names, rows = execute_query(sql, params)
    return [dict(zip(col_names, row)) for row in rows]
