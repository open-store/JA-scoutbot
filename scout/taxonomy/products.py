"""
Product catalog resolution.

Resolves a product name from a ``product:`` filter or a natural-language
question against the product catalog. Catalog rows are loaded from
Snowflake by ``scout.services.product_catalog_service``. This module holds
the pure matching logic and does no I/O.

Resolution distinguishes a single style from a product family:

- An exact style name or explicit alias resolves to that style.
- An exact family name resolves to the whole family, labelled as such.
- A partial name that fits exactly one style resolves to it, with a caveat.
- A partial name that fits several styles is ambiguous. Scout asks which
  one the user meant instead of mixing them.
- A name that matches nothing is unknown. Scout does not broaden it to its
  first word, because that mixes a product with its wider family.

``PRODUCT_ALIASES`` holds hand-maintained spellings. A group attaches to a
catalog style when any of its spellings matches the style's name. When the
catalog cannot be loaded, those groups are the only names Scout recognises,
and any other name is matched as an exact phrase with a caveat.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# Explicit aliases (canonical name → spellings customers and staff use)
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

ALIAS_LIST_SOURCE = "Scout explicit alias list"

# Resolution statuses and levels.
RESOLVED = "resolved"
AMBIGUOUS = "ambiguous"
UNKNOWN = "unknown"

STYLE = "style"
FAMILY = "family"
PHRASE = "phrase"  # catalog unavailable; matched as typed

# Singular garment nouns. Used to fold plurals ("shorts" → "short") and to
# spot product-like phrases ("<name> <garment>") in free text.
GARMENT_NOUNS: FrozenSet[str] = frozenset({
    "short", "pant", "trouser", "chino", "jean", "jogger", "sweatpant", "legging",
    "polo", "tee", "shirt", "overshirt", "henley", "oxford", "tank", "top",
    "hoodie", "crewneck", "crew", "sweatshirt", "sweater", "pullover", "zip",
    "jacket", "vest", "blazer", "coat", "shacket",
    "sock", "belt", "hat", "cap", "boxer", "brief", "bottom",
})

# Tokens that break a product-like phrase in free text.
_PHRASE_BREAK_TOKENS: FrozenSet[str] = frozenset({
    "a", "an", "the", "my", "our", "your", "their", "his", "her", "its", "this", "that",
    "these", "those", "and", "or", "but", "vs", "versus", "with", "without", "in", "on",
    "for", "of", "to", "from", "about", "at", "by", "into", "over", "under", "than",
    "is", "are", "was", "were", "be", "been", "do", "does", "did", "have", "has", "had",
    "what", "whats", "which", "who", "how", "hows", "why", "when", "where", "any", "some",
    "too", "very", "so", "really", "more", "most", "less", "much", "many", "all",
    "i", "we", "you", "they", "it", "me", "us", "them",
    "customer", "customers", "people", "anyone", "someone",
    "say", "saying", "said", "says", "think", "feel", "mention", "mentioning",
    "complain", "complaining", "complaint", "complaints", "feedback", "comment", "comments",
    "review", "reviews", "return", "returns", "returned", "refund", "refunds", "exchange",
    "exchanges", "exchanged", "issue", "issues", "problem", "problems",
    "fit", "fitting", "sizing", "size", "quality", "length",
    "love", "loved", "like", "liked", "hate", "bought", "buy", "ordered", "order", "orders",
    "wear", "wearing", "wore", "got", "get", "need", "want", "try", "tried", "compare",
    "comparing", "show", "tell", "give", "find", "see", "look", "looking", "know",
    "new", "latest", "recent", "old", "current",
})

_LEADING_FILLER: FrozenSet[str] = frozenset({
    "the", "a", "an", "my", "our", "your", "their", "this", "that", "these", "those",
    "jack", "archer",
})

_FAMILY_SUFFIX: FrozenSet[str] = frozenset({
    "family", "collection", "line", "series", "range", "franchise", "style", "product",
})

_MAX_CANDIDATES = 8


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _fold_token(token: str) -> str:
    if token.endswith("s") and token[:-1] in GARMENT_NOUNS:
        return token[:-1]
    return token


def _tokens(text: str) -> List[str]:
    text = normalise(text)
    text = text.replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return [_fold_token(t) for t in text.split()]


def match_key(text: str) -> str:
    """Comparison key: normalised, punctuation removed, garment plurals folded.

    ``"Atlas Shorts"``, ``"atlas-short"`` and ``"ATLAS SHORT"`` share a key.
    """
    return " ".join(_tokens(text))


def _strip_query(tokens: List[str]) -> List[str]:
    while tokens and tokens[0] in _LEADING_FILLER:
        tokens = tokens[1:]
    return tokens


def sql_phrases(name: str) -> List[str]:
    """Lowercase phrases for substring matching in SQL and evidence text.

    Covers the spelling as written, a hyphen/space variant, and the singular
    form of a trailing plural garment noun. A substring match on the singular
    also matches the plural, so ``"atlas short"`` finds "Atlas Shorts".
    """
    base = normalise(name).replace("\u2019", "'")
    if not base:
        return []
    variants: Set[str] = {base, base.replace("-", " ")}
    if " " in base:
        variants.add(base.replace(" ", "-"))
    for v in list(variants):
        head, _, last = v.rpartition(" ")
        if head and last.endswith("s") and last[:-1] in GARMENT_NOUNS:
            variants.add(f"{head} {last[:-1]}")
    return sorted(v for v in variants if len(v) >= 3)


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CatalogStyle:
    """One style in the product catalog."""

    name: str
    family: Optional[str] = None
    category: Optional[str] = None
    product_class: Optional[str] = None
    subclass: Optional[str] = None
    seasons: Tuple[str, ...] = ()
    lifecycles: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    in_catalog: bool = True

    @property
    def key(self) -> str:
        return match_key(self.name)

    def token_set(self) -> FrozenSet[str]:
        return frozenset(_tokens(self.name))

    def full_token_set(self) -> FrozenSet[str]:
        tokens = set(_tokens(self.name))
        if self.family:
            tokens.update(_tokens(self.family))
        return frozenset(tokens)

    def phrases(self) -> List[str]:
        out: Set[str] = set(sql_phrases(self.name))
        for alias in self.aliases:
            out.update(sql_phrases(alias))
        return sorted(out)


@dataclass(frozen=True)
class ProductCatalog:
    """Styles Scout can resolve, plus where they came from.

    ``available`` is True only when the styles were read from the catalog
    source. The alias-list fallback sets it to False.
    """

    styles: Tuple[CatalogStyle, ...]
    source: str
    available: bool
    error: Optional[str] = None

    def families(self) -> Dict[str, List[CatalogStyle]]:
        out: Dict[str, List[CatalogStyle]] = {}
        for style in self.styles:
            if style.family:
                out.setdefault(style.family, []).append(style)
        return out


def _split_multi(value: object) -> Tuple[str, ...]:
    if value is None:
        return ()
    parts = [p.strip() for p in str(value).split("|")]
    return tuple(sorted({p for p in parts if p}))


def _clean(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def catalog_from_rows(rows: Iterable[Mapping[str, object]], source: str) -> ProductCatalog:
    """Build a catalog from repository rows (one or more rows per style).

    Rows for the same style name are merged. A style that appears under more
    than one family keeps no family, because assigning one would be a guess.
    """
    merged: Dict[str, Dict[str, Set[str]]] = {}
    display: Dict[str, str] = {}
    for row in rows:
        name = _clean(row.get("STYLE_NAME"))
        if not name:
            continue
        key = match_key(name)
        if not key:
            continue
        display.setdefault(key, name)
        slot = merged.setdefault(key, {
            "family": set(), "category": set(), "class": set(), "subclass": set(),
            "seasons": set(), "lifecycles": set(),
        })
        for col, bucket in (("FRANCHISE", "family"), ("CATEGORY", "category"),
                            ("CLASS", "class"), ("SUBCLASS", "subclass")):
            val = _clean(row.get(col))
            if val:
                slot[bucket].add(val)
        slot["seasons"].update(_split_multi(row.get("SEASONS")))
        slot["lifecycles"].update(_split_multi(row.get("LIFECYCLES")))

    def single(values: Set[str]) -> Optional[str]:
        return next(iter(values)) if len(values) == 1 else None

    styles = [
        CatalogStyle(
            name=display[key],
            family=single(slot["family"]),
            category=single(slot["category"]),
            product_class=single(slot["class"]),
            subclass=single(slot["subclass"]),
            seasons=tuple(sorted(slot["seasons"])),
            lifecycles=tuple(sorted(slot["lifecycles"])),
        )
        for key, slot in sorted(merged.items())
    ]
    return attach_explicit_aliases(ProductCatalog(styles=tuple(styles), source=source, available=True))


def attach_explicit_aliases(
    catalog: ProductCatalog,
    alias_groups: Optional[Mapping[str, FrozenSet[str]]] = None,
) -> ProductCatalog:
    """Attach explicit alias groups to matching catalog styles.

    A group whose spellings match no catalog style is kept as a style with
    ``in_catalog=False`` so resolution can say it was not found in the catalog.
    """
    groups = alias_groups if alias_groups is not None else PRODUCT_ALIASES
    by_key: Dict[str, int] = {s.key: i for i, s in enumerate(catalog.styles)}
    styles = list(catalog.styles)
    for canonical, aliases in groups.items():
        spellings = {canonical, *aliases}
        idx = next((by_key[match_key(s)] for s in spellings if match_key(s) in by_key), None)
        if idx is not None:
            existing = styles[idx]
            merged_aliases = tuple(sorted(set(existing.aliases) | {normalise(a) for a in spellings}))
            styles[idx] = CatalogStyle(
                name=existing.name, family=existing.family, category=existing.category,
                product_class=existing.product_class, subclass=existing.subclass,
                seasons=existing.seasons, lifecycles=existing.lifecycles,
                aliases=merged_aliases, in_catalog=existing.in_catalog,
            )
        else:
            styles.append(CatalogStyle(
                name=canonical.title(),
                aliases=tuple(sorted(normalise(a) for a in spellings)),
                in_catalog=False,
            ))
            by_key[match_key(canonical)] = len(styles) - 1
    return ProductCatalog(styles=tuple(styles), source=catalog.source,
                          available=catalog.available, error=catalog.error)


def alias_only_catalog(error: Optional[str] = None) -> ProductCatalog:
    """Catalog used when the catalog source cannot be read."""
    empty = ProductCatalog(styles=(), source=ALIAS_LIST_SOURCE, available=False, error=error)
    return attach_explicit_aliases(empty)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductResolution:
    """Outcome of resolving one product name against the catalog.

    ``match_phrases`` are the lowercase phrases retrieval may match. A style
    resolution never includes a bare first word of its name; a family
    resolution includes the family name on purpose.
    """

    query: str
    status: str
    level: Optional[str] = None
    canonical_name: Optional[str] = None
    family: Optional[str] = None
    styles: Tuple[str, ...] = ()
    match_phrases: Tuple[str, ...] = ()
    match_method: str = ""
    candidates: Tuple[str, ...] = ()
    seasons: Tuple[str, ...] = ()
    catalog_source: str = ""
    catalog_available: bool = False
    caveats: Tuple[str, ...] = ()

    @property
    def is_resolved(self) -> bool:
        return self.status == RESOLVED

    def to_dict(self) -> Dict[str, object]:
        return {
            "query": self.query,
            "status": self.status,
            "level": self.level,
            "canonical_name": self.canonical_name,
            "family": self.family,
            "styles": list(self.styles),
            "match_phrases": list(self.match_phrases),
            "match_method": self.match_method,
            "candidates": list(self.candidates),
            "seasons": list(self.seasons),
            "catalog_source": self.catalog_source,
            "catalog_available": self.catalog_available,
            "caveats": list(self.caveats),
        }


def _style_resolution(query: str, style: CatalogStyle, catalog: ProductCatalog,
                      method: str, caveats: Sequence[str] = ()) -> ProductResolution:
    notes = list(caveats)
    if not style.in_catalog:
        method = "alias_list"
        if catalog.available:
            notes.append(
                f"'{style.name}' is in Scout's alias list but was not found in the "
                f"product catalog ({catalog.source})."
            )
        else:
            notes.append(
                f"The product catalog could not be loaded, so '{style.name}' was "
                "matched from Scout's alias list."
            )
    return ProductResolution(
        query=query,
        status=RESOLVED,
        level=STYLE,
        canonical_name=style.name,
        family=style.family,
        styles=(style.name,),
        match_phrases=tuple(style.phrases()),
        match_method=method,
        seasons=style.seasons,
        catalog_source=catalog.source,
        catalog_available=catalog.available,
        caveats=tuple(notes),
    )


def _family_resolution(query: str, family: str, members: List[CatalogStyle],
                       catalog: ProductCatalog) -> ProductResolution:
    phrases: Set[str] = set(sql_phrases(family))
    seasons: Set[str] = set()
    for style in members:
        phrases.update(style.phrases())
        seasons.update(style.seasons)
    names = tuple(sorted(s.name for s in members))
    return ProductResolution(
        query=query,
        status=RESOLVED,
        level=FAMILY,
        canonical_name=family,
        family=family,
        styles=names,
        match_phrases=tuple(sorted(phrases)),
        match_method="catalog_family",
        seasons=tuple(sorted(seasons)),
        catalog_source=catalog.source,
        catalog_available=catalog.available,
        caveats=(
            f"Family-level answer: covers {len(names)} {family} style"
            f"{'s' if len(names) != 1 else ''} in the catalog.",
        ),
    )


def resolve_product(query: str, catalog: ProductCatalog) -> ProductResolution:
    """Resolve *query* to a style, a family, or an ambiguous/unknown outcome."""
    tokens = _strip_query(_tokens(query))
    key = " ".join(tokens)
    if not key:
        return ProductResolution(query=query, status=UNKNOWN, catalog_source=catalog.source,
                                 catalog_available=catalog.available)

    # 1. Exact style name or explicit alias.
    for style in catalog.styles:
        if style.key == key:
            return _style_resolution(query, style, catalog, "catalog_exact")
    for style in catalog.styles:
        if any(match_key(a) == key for a in style.aliases):
            return _style_resolution(query, style, catalog, "catalog_alias")
    token_set = frozenset(tokens)
    for style in catalog.styles:
        if style.in_catalog and style.family and style.full_token_set() == token_set:
            return _style_resolution(query, style, catalog, "catalog_exact")

    # 2. Exact family name ("Jetsetter", "the Jetsetter family").
    family_tokens = list(tokens)
    while len(family_tokens) > 1 and family_tokens[-1] in _FAMILY_SUFFIX:
        family_tokens = family_tokens[:-1]
    family_key = " ".join(family_tokens)
    for family, members in catalog.families().items():
        if match_key(family) == family_key:
            return _family_resolution(query, family, members, catalog)

    # 3. Partial: every query token appears in exactly one style.
    partial = [
        s for s in catalog.styles
        if (s.in_catalog or not catalog.available)
        and (token_set <= s.full_token_set()
             or any(token_set <= frozenset(_tokens(a)) for a in s.aliases))
    ]
    # A unique partial match only means something against the full catalog;
    # the alias list alone is not a complete product list.
    if len(partial) == 1 and catalog.available:
        style = partial[0]
        return _style_resolution(
            query, style, catalog, "catalog_partial",
            caveats=(f"Interpreted '{query}' as {style.name}.",),
        )
    if len(partial) > 1:
        partial.sort(key=lambda s: (len(s.full_token_set() - token_set), s.name))
        shared_families = {s.family for s in partial}
        shared_family = next(iter(shared_families)) if len(shared_families) == 1 else None
        return ProductResolution(
            query=query,
            status=AMBIGUOUS,
            family=shared_family,
            candidates=tuple(s.name for s in partial[:_MAX_CANDIDATES]),
            catalog_source=catalog.source,
            catalog_available=catalog.available,
        )

    # 4. No match.
    if not catalog.available:
        return ProductResolution(
            query=query,
            status=RESOLVED,
            level=PHRASE,
            canonical_name=" ".join(w.capitalize() for w in normalise(query).split()),
            styles=(),
            match_phrases=tuple(sql_phrases(query)),
            match_method="exact_phrase_unverified",
            catalog_source=catalog.source,
            catalog_available=False,
            caveats=(
                f"The product catalog could not be loaded, so '{query}' was matched as "
                "an exact phrase and not checked against the catalog.",
            ),
        )
    return ProductResolution(
        query=query,
        status=UNKNOWN,
        candidates=tuple(_suggestions(tokens, catalog)),
        catalog_source=catalog.source,
        catalog_available=True,
    )


def _suggestions(tokens: List[str], catalog: ProductCatalog, limit: int = 5) -> List[str]:
    key = " ".join(tokens)
    by_key = {s.key: s.name for s in catalog.styles if s.in_catalog}
    out: List[str] = []
    for k in difflib.get_close_matches(key, list(by_key), n=limit, cutoff=0.6):
        out.append(by_key[k])
    distinctive = {t for t in tokens if t not in GARMENT_NOUNS}
    for style in catalog.styles:
        if style.in_catalog and distinctive & style.full_token_set() and style.name not in out:
            out.append(style.name)
    return out[:limit]


# ---------------------------------------------------------------------------
# Finding products in free text
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductMention:
    """A product named in free text.

    ``level`` is ``style`` or ``family`` for a catalog match, or ``phrase``
    for a product-like phrase that is not in the catalog.
    """

    name: str
    level: str
    start: int
    phrase: str


def _phrase_index(catalog: ProductCatalog) -> List[Tuple[Tuple[str, ...], str, str]]:
    entries: Dict[Tuple[str, ...], Tuple[str, str]] = {}
    for style in catalog.styles:
        spellings = [style.name, *style.aliases]
        if style.family and not set(_tokens(style.family)) <= style.token_set():
            spellings.append(f"{style.family} {style.name}")
        for spelling in spellings:
            toks = tuple(_tokens(spelling))
            if toks:
                entries.setdefault(toks, (style.name, STYLE))
    for family in catalog.families():
        toks = tuple(_tokens(family))
        if toks:
            entries.setdefault(toks, (family, FAMILY))
    return sorted(
        ((toks, name, level) for toks, (name, level) in entries.items()),
        key=lambda e: (-len(e[0]), -len(" ".join(e[0]))),
    )


def _extend_to_garment(tokens: List[str], end: int, taken: List[bool], level: str) -> int:
    """Index after a garment noun that turns a match into a longer product name.

    A family name followed by a garment ("Atlas jogger") becomes a longer
    phrase. A style followed directly by a garment ("Clubhouse Polo shirt")
    stays the same style; only an intervening word ("Atlas Short sleeve
    shirt") makes it a different product.
    """
    j = end
    last_garment = end
    while j < len(tokens) and j < end + 3 and not taken[j] and tokens[j] not in _PHRASE_BREAK_TOKENS:
        if tokens[j] in GARMENT_NOUNS:
            if level == STYLE and j == end:
                return end
            last_garment = j + 1
        j += 1
    return last_garment


def _family_followup(tokens: List[str], i: int, n: int, taken: List[bool],
                     family: str, catalog: ProductCatalog) -> Tuple[int, str, str]:
    """Refine a family match using the words right after it.

    "Jetsetter tech" names one style; "Atlas jogger" names a product that is
    not in the catalog. Returns (end index, name, level).
    """
    following: List[str] = []
    j = i + n
    while j < len(tokens) and len(following) < 3 and not taken[j] and tokens[j] not in _PHRASE_BREAK_TOKENS:
        following.append(tokens[j])
        j += 1
    members = [s for s in catalog.styles if s.family == family]
    for k in range(len(following), 0, -1):
        wanted = frozenset(tokens[i:i + n] + following[:k])
        hits = [s for s in members if wanted <= s.full_token_set()]
        if len(hits) == 1:
            return i + n + k, hits[0].name, STYLE
        if len(hits) > 1:
            end = i + n + k
            return end, " ".join(tokens[i:end]), PHRASE
    garment_at = [k for k, tok in enumerate(following) if tok in GARMENT_NOUNS]
    if garment_at:
        end = i + n + garment_at[-1] + 1
        return end, " ".join(tokens[i:end]), PHRASE
    return i + n, family, FAMILY


def find_product_mentions(text: str, catalog: ProductCatalog) -> List[ProductMention]:
    """Products named in *text*, in order of appearance.

    Longer names win over shorter ones that they contain. A catalog match
    that runs straight into a longer product-like phrase ("Atlas" followed by
    "Jogger" when there is no Atlas Jogger) is reported as a ``phrase``
    mention, so it is not silently widened to the family.
    """
    tokens = _tokens(text)
    taken = [False] * len(tokens)
    found: List[ProductMention] = []

    for toks, name, level in _phrase_index(catalog):
        n = len(toks)
        i = 0
        while i <= len(tokens) - n:
            if not any(taken[i:i + n]) and tuple(tokens[i:i + n]) == toks:
                if level == FAMILY:
                    end, found_name, found_level = _family_followup(tokens, i, n, taken, name, catalog)
                else:
                    end = _extend_to_garment(tokens, i + n, taken, level)
                    found_name, found_level = name, level
                    if end > i + n:
                        found_name, found_level = " ".join(tokens[i:end]), PHRASE
                for k in range(i, end):
                    taken[k] = True
                found.append(ProductMention(name=found_name, level=found_level, start=i,
                                            phrase=" ".join(tokens[i:end])))
                i = end
            else:
                i += 1

    found.extend(_garment_phrases(tokens, taken))

    seen: Set[Tuple[str, str]] = set()
    ordered: List[ProductMention] = []
    for mention in sorted(found, key=lambda m: m.start):
        ident = (mention.level, mention.name)
        if ident not in seen:
            seen.add(ident)
            ordered.append(mention)
    return ordered


def _garment_phrases(tokens: List[str], taken: List[bool]) -> List[ProductMention]:
    """Product-like phrases ("<name> <garment>") outside catalog matches.

    Requires at least one name token before the garment noun, so a bare
    "shorts" or "too short" is not treated as a product.
    """
    out: List[ProductMention] = []
    run: List[int] = []

    def flush() -> None:
        garment_positions = [p for p in range(1, len(run)) if tokens[run[p]] in GARMENT_NOUNS]
        if garment_positions:
            g = garment_positions[-1]
            words = [tokens[k] for k in run[max(0, g - 3):g + 1]]
            phrase = " ".join(words)
            out.append(ProductMention(name=phrase, level=PHRASE, start=run[max(0, g - 3)], phrase=phrase))
        run.clear()

    for idx, tok in enumerate(tokens):
        if taken[idx] or tok in _PHRASE_BREAK_TOKENS or tok.isdigit():
            flush()
        else:
            run.append(idx)
    flush()
    return out


# ---------------------------------------------------------------------------
# Backward-compatible helpers
# ---------------------------------------------------------------------------

def resolve_aliases(product_name: str) -> List[str]:
    """Match phrases for *product_name* using the explicit alias list only.

    An unknown name returns its own spellings. It is never broadened to its
    first word.
    """
    resolution = resolve_product(product_name, alias_only_catalog())
    return list(resolution.match_phrases)


def canonical_name(product_name: str) -> str:
    """Canonical display name from the explicit alias list, else the title-cased input."""
    resolution = resolve_product(product_name, alias_only_catalog())
    return resolution.canonical_name or normalise(product_name).title()
