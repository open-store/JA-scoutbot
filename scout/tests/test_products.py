"""Tests for product catalog resolution and product mentions in free text."""
import pytest

from scout.taxonomy.products import (
    AMBIGUOUS,
    FAMILY,
    PHRASE,
    RESOLVED,
    STYLE,
    UNKNOWN,
    alias_only_catalog,
    canonical_name,
    catalog_from_rows,
    find_product_mentions,
    normalise,
    resolve_aliases,
    resolve_product,
    sql_phrases,
)
from scout.tests.catalog_fixture import CATALOG_ROWS, FIXTURE_SOURCE, fixture_catalog


@pytest.fixture
def catalog():
    return fixture_catalog()


# ---------------------------------------------------------------------------
# Explicit alias list (backward-compatible helpers)
# ---------------------------------------------------------------------------

def test_known_product():
    aliases = resolve_aliases("Anytime Crewneck")
    assert "anytime crewneck" in aliases
    assert "anytime crew neck" in aliases


def test_case_insensitive():
    aliases = resolve_aliases("anytime crewneck")
    assert "anytime crewneck" in aliases


def test_clubhouse_polo():
    aliases = resolve_aliases("Clubhouse polo")
    assert "clubhouse polo" in aliases
    assert "clubhouse performance polo" in aliases


def test_unknown_product_is_not_broadened_to_first_word():
    aliases = resolve_aliases("Mystery Product XYZ")
    assert "mystery product xyz" in aliases
    assert "mystery" not in aliases
    assert all(" " in a or "-" in a for a in aliases)


def test_canonical_name():
    assert canonical_name("anytime crewneck") == "Anytime Crewneck"
    assert canonical_name("clubhouse polo") == "Clubhouse Polo"
    assert canonical_name("some random thing") == "Some Random Thing"


def test_normalise():
    assert normalise("  Anytime  Crewneck  ") == "anytime crewneck"
    assert normalise("CLUBHOUSE POLO") == "clubhouse polo"


# ---------------------------------------------------------------------------
# Catalog resolution: styles beyond Clubhouse Polo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", ["Atlas Short", "atlas shorts", "the Atlas Short", "ATLAS-SHORT"])
def test_atlas_short_resolves_to_the_style(catalog, query):
    r = resolve_product(query, catalog)
    assert r.status == RESOLVED
    assert r.level == STYLE
    assert r.canonical_name == "Atlas Short"
    assert r.family == "Atlas"
    assert r.styles == ("Atlas Short",)
    assert r.catalog_source == FIXTURE_SOURCE
    assert "atlas short" in r.match_phrases
    assert "atlas" not in r.match_phrases
    assert r.seasons == ("SP26", "SU26")


@pytest.mark.parametrize("query", ["Charter Twill Pant", "charter twill pants", "Charter twill"])
def test_charter_twill_pant_resolves(catalog, query):
    r = resolve_product(query, catalog)
    assert r.status == RESOLVED
    assert r.level == STYLE
    assert r.canonical_name == "Charter Twill Pant"
    assert "charter" not in r.match_phrases
    assert all("charter twill pant" in p or "charter-twill-pant" in p for p in r.match_phrases)


def test_partial_name_with_one_match_is_resolved_with_caveat(catalog):
    r = resolve_product("charter twill", catalog)
    assert r.match_method == "catalog_partial"
    assert any("Interpreted 'charter twill' as Charter Twill Pant" in c for c in r.caveats)


def test_explicit_alias_attaches_to_catalog_style(catalog):
    r = resolve_product("anytime crew neck", catalog)
    assert r.status == RESOLVED
    assert r.canonical_name == "Anytime Crewneck"
    assert r.match_method == "catalog_alias"
    assert r.caveats == ()


def test_alias_group_missing_from_catalog_is_disclosed(catalog):
    r = resolve_product("Traveler Pant", catalog)
    assert r.status == RESOLVED
    assert r.match_method == "alias_list"
    assert any("not found in the product catalog" in c for c in r.caveats)


# ---------------------------------------------------------------------------
# Style versus family
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query", ["Jetsetter", "jetsetter family", "the Jetsetter collection"])
def test_family_question_resolves_to_family(catalog, query):
    r = resolve_product(query, catalog)
    assert r.status == RESOLVED
    assert r.level == FAMILY
    assert r.canonical_name == "Jetsetter"
    assert set(r.styles) == {"Jetsetter Tech Pant", "Jetsetter Travel Pant", "Jetsetter Short"}
    assert "jetsetter" in r.match_phrases
    assert any("Family-level answer" in c for c in r.caveats)


def test_family_ambiguity_asks_instead_of_mixing(catalog):
    r = resolve_product("Jetsetter Pant", catalog)
    assert r.status == AMBIGUOUS
    assert set(r.candidates) == {"Jetsetter Tech Pant", "Jetsetter Travel Pant"}
    assert r.family == "Jetsetter"
    assert r.match_phrases == ()


def test_generic_garment_is_ambiguous(catalog):
    r = resolve_product("pants", catalog)
    assert r.status == AMBIGUOUS
    assert "Atlas Pant" in r.candidates
    assert "Traveler Pant" not in r.candidates  # alias-list entry not in the catalog


def test_style_name_wins_over_family(catalog):
    r = resolve_product("Jetsetter Short", catalog)
    assert r.level == STYLE
    assert r.canonical_name == "Jetsetter Short"


# ---------------------------------------------------------------------------
# Unknown products and catalog outages
# ---------------------------------------------------------------------------

def test_unknown_product_returns_suggestions_not_broader_scope(catalog):
    r = resolve_product("Atlas Jogger", catalog)
    assert r.status == UNKNOWN
    assert r.match_phrases == ()
    assert set(r.candidates) >= {"Atlas Short", "Atlas Pant"}


def test_unrelated_unknown_product(catalog):
    r = resolve_product("Mystery Parka", catalog)
    assert r.status == UNKNOWN
    assert not r.is_resolved


def test_catalog_unavailable_matches_exact_phrase_only():
    r = resolve_product("Atlas Short", alias_only_catalog(error="KeyError"))
    assert r.status == RESOLVED
    assert r.level == PHRASE
    assert r.match_method == "exact_phrase_unverified"
    assert "atlas" not in r.match_phrases
    assert "atlas short" in r.match_phrases
    assert any("could not be loaded" in c for c in r.caveats)
    assert r.catalog_available is False


def test_catalog_unavailable_does_not_guess_from_one_alias_entry():
    r = resolve_product("clubhouse", alias_only_catalog())
    assert r.level == PHRASE
    assert r.canonical_name == "Clubhouse"


def test_style_in_two_families_keeps_no_family():
    rows = CATALOG_ROWS + [{"STYLE_NAME": "Atlas Short", "FRANCHISE": "Weekend"}]
    r = resolve_product("Atlas Short", catalog_from_rows(rows, FIXTURE_SOURCE))
    assert r.level == STYLE
    assert r.family is None


def test_sql_phrases_cover_plural_and_hyphen():
    phrases = sql_phrases("Atlas Shorts")
    assert "atlas shorts" in phrases
    assert "atlas short" in phrases
    assert "atlas-shorts" in phrases
    assert sql_phrases("Atlas") == ["atlas"]  # not singularised to "atla"


# ---------------------------------------------------------------------------
# Mentions in free text
# ---------------------------------------------------------------------------

def _mentions(text, catalog):
    return [(m.name, m.level) for m in find_product_mentions(text, catalog)]


def test_mentions_find_catalog_style(catalog):
    assert _mentions("What are customers saying about the Atlas Shorts?", catalog) == [("Atlas Short", STYLE)]
    assert _mentions("fit on the charter twill pants last 180 days", catalog) == [("Charter Twill Pant", STYLE)]


def test_mentions_keep_style_followed_by_synonym(catalog):
    assert _mentions("thoughts on the clubhouse polo shirt", catalog) == [("Clubhouse Polo", STYLE)]


def test_mentions_do_not_shrink_a_longer_product_name(catalog):
    assert _mentions("Atlas Short sleeve shirt feedback", catalog) == [("atlas short sleeve shirt", PHRASE)]


def test_mentions_family_and_family_refinement(catalog):
    assert _mentions("what about jetsetter overall", catalog) == [("Jetsetter", FAMILY)]
    assert _mentions("how is the jetsetter tech doing", catalog) == [("Jetsetter Tech Pant", STYLE)]
    assert _mentions("how is the jetsetter pant doing", catalog) == [("jetsetter pant", PHRASE)]
    assert _mentions("how is the atlas jogger doing", catalog) == [("atlas jogger", PHRASE)]


def test_mentions_product_like_phrase_outside_catalog(catalog):
    assert _mentions("What do people think of the Mystery Pant?", catalog) == [("mystery pant", PHRASE)]


def test_mentions_ignore_adjective_uses_of_garment_words(catalog):
    assert _mentions("customers say the sleeves are too short", catalog) == []
    assert _mentions("what are customers saying about our new shorts", catalog) == []


def test_mentions_multiple_products_in_order(catalog):
    assert _mentions("compare atlas short and charter twill pant", catalog) == [
        ("Atlas Short", STYLE), ("Charter Twill Pant", STYLE),
    ]
