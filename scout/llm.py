"""Shared OpenAI client.

Every Scout call that used to construct ``OpenAI()`` and send
``gpt-4.1-mini`` goes through ``get_client`` and ``MODEL``.
The client is created on first use and reused, which is what the
natural-language router already did.
"""

from openai import OpenAI

MODEL = "gpt-4.1-mini"

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Return the process-wide OpenAI client. Reads ``OPENAI_API_KEY`` from the environment."""
    global _client
    if _client is None:
        _client = OpenAI()
    return _client
