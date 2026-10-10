"""Shared SQL fragments.

The LIKE list is the auto-reply exclusion used by general VOC and by the
product-feedback repository. ``alias`` is the table alias (``c`` in the
repository, none in VOC). ``indent`` keeps the surrounding query's
whitespace so the SQL string does not change.
"""

_AUTOREPLY_PATTERNS = (
    "%automatic reply%",
    "%autoreply%",
    "%auto-reply%",
    "%out of office%",
    "%vacation%",
    "%away from the office%",
    "%unsubscribe%",
)


def autoreply_exclusion(alias: str = "", indent: str = "      ") -> str:
    """Return the auto-reply ``AND NOT LIKE`` block, including surrounding newlines."""
    column = f"{alias}.SUBJECT" if alias else "SUBJECT"
    lines = [
        f"{indent}AND LOWER({column}) NOT LIKE '{pattern}'"
        for pattern in _AUTOREPLY_PATTERNS
    ]
    return "\n" + "\n".join(lines) + "\n"
