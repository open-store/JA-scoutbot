"""
Product alias resolver.

Maps canonical product names to a list of aliases used for fuzzy matching
in ticket subjects and message bodies.  Aliases are normalised to lowercase
before comparison.

To extend: add entries to ``PRODUCT_ALIASES`` or call ``register_aliases``.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, FrozenSet, List, Set

# ---------------------------------------------------------------------------
# Known product alias map  (canonical name → set of aliases)
# ---------------------------------------------------------------------------

PRODUCT_ALIASES: Dict[str, FrozenSet[str]] = {
    "anytime crewneck": frozenset({
        "anytime crewneck",
        "anytime crew neck",
        "anytime crew",
    }),
    "anytime tee": frozenset({
        "anytime tee",
        "anytime t-shirt",
        "anytime t shirt",
    }),
    "anytime hoodie": frozenset({
        "anytime hoodie",
    }),
    "anytime jogger": frozenset({
        "anytime jogger",
        "anytime joggers",
    }),
    "anytime short": frozenset({
        "anytime short",
        "anytime shorts",
    }),
    "clubhouse polo": frozenset({
        "clubhouse polo",
        "clubhouse polos",
        "clubhouse performance polo",
    }),
    "everyday pant": frozenset({
        "everyday pant",
        "everyday pants",
        "everyday chino",
        "everyday chinos",
    }),
    "everyday short": frozenset({
        "everyday short",
        "everyday shorts",
    }),
    "traveler pant": frozenset({
        "traveler pant",
        "traveler pants",
        "traveller pant",
        "traveller pants",
    }),
}

# Reverse index: alias → canonical name
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _canon, _aliases in PRODUCT_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias.lower()] = _canon


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def resolve_aliases(product_name: str) -> List[str]:
    """Return a list of aliases for *product_name* (always includes the
    original normalised name).

    If the product is not in the known alias map, returns a list containing
    only the normalised input plus a "first-word" broadened variant.
    """
    key = normalise(product_name)

    # Direct match on canonical name
    if key in PRODUCT_ALIASES:
        return sorted(PRODUCT_ALIASES[key])

    # Match via alias → canonical
    canon = _ALIAS_TO_CANONICAL.get(key)
    if canon:
        return sorted(PRODUCT_ALIASES[canon])

    # Unknown product — return the input + first-word broadened variant
    aliases: Set[str] = {key}
    first_word = key.split()[0] if key.split() else key
    if first_word != key:
        aliases.add(first_word)
    return sorted(aliases)


def canonical_name(product_name: str) -> str:
    """Return the canonical product name, or the normalised input if unknown."""
    key = normalise(product_name)
    if key in PRODUCT_ALIASES:
        return key
    canon = _ALIAS_TO_CANONICAL.get(key)
    return canon if canon else key


def register_aliases(canonical: str, aliases: FrozenSet[str]) -> None:
    """Register or extend aliases for a product at runtime."""
    canonical = normalise(canonical)
    existing = set(PRODUCT_ALIASES.get(canonical, frozenset()))
    existing.update(normalise(a) for a in aliases)
    PRODUCT_ALIASES[canonical] = frozenset(existing)
    for alias in existing:
        _ALIAS_TO_CANONICAL[alias] = canonical
