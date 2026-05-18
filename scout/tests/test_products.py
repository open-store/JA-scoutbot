"""Tests for product alias resolution."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scout.taxonomy.products import resolve_aliases, canonical_name, normalise


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


def test_unknown_product():
    aliases = resolve_aliases("Mystery Product XYZ")
    # Should return at least the normalised input + first-word broadened
    assert "mystery product xyz" in aliases
    assert "mystery" in aliases


def test_canonical_name():
    assert canonical_name("anytime crewneck") == "Anytime Crewneck"
    assert canonical_name("clubhouse polo") == "Clubhouse Polo"
    # Unknown product returns title-cased input
    name = canonical_name("some random thing")
    assert name == "Some Random Thing"


def test_normalise():
    assert normalise("  Anytime  Crewneck  ") == "anytime crewneck"
    assert normalise("CLUBHOUSE POLO") == "clubhouse polo"


if __name__ == "__main__":
    test_known_product()
    test_case_insensitive()
    test_clubhouse_polo()
    test_unknown_product()
    test_canonical_name()
    test_normalise()
    print("All product alias tests passed.")
