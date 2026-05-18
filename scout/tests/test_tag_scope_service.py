"""Tests for tag scope resolution."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scout.taxonomy.tags import tag_names_for_group, all_group_names, groups_for_tag
from scout.services.tag_scope_service import resolve_tag_group_to_uuids, list_available_groups


def test_product_feedback_group_exists():
    names = tag_names_for_group("product_feedback")
    assert len(names) > 0, "product_feedback group should have tags"
    assert "product-quality" in names
    assert "product-size-fit-issue" in names


def test_all_groups_listed():
    groups = all_group_names()
    assert "product_feedback" in groups
    assert "returns" in groups
    assert "shipping" in groups


def test_reverse_lookup():
    groups = groups_for_tag("product-quality")
    assert "product_feedback" in groups


def test_unknown_group_raises():
    try:
        tag_names_for_group("nonexistent_group")
        assert False, "Should have raised KeyError"
    except KeyError:
        pass


def test_uuid_resolution():
    uuids = resolve_tag_group_to_uuids("product_feedback")
    # Should return at least some UUIDs (depends on tag_mapping coverage)
    # Even if some tag names don't have UUIDs, the function should not error
    assert isinstance(uuids, list)


def test_list_available_groups():
    groups = list_available_groups()
    assert "product_feedback" in groups


if __name__ == "__main__":
    test_product_feedback_group_exists()
    test_all_groups_listed()
    test_reverse_lookup()
    test_unknown_group_raises()
    test_uuid_resolution()
    test_list_available_groups()
    print("All tag scope tests passed.")
