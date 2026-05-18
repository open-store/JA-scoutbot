"""
Tag scope service.

Resolves a tag group name → list of tag UUIDs in that scope.
Thin wrapper around taxonomy + tag_mapping for convenience.
"""
from __future__ import annotations

import logging
from typing import List, Set

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from tag_mapping import TAG_ID_TO_NAME
from scout.taxonomy.tags import tag_names_for_group, all_group_names

logger = logging.getLogger("scout.services.tag_scope")

# Reverse mapping: tag name → list of UUIDs
_NAME_TO_UUIDS = {}
for _uuid, _name in TAG_ID_TO_NAME.items():
    _NAME_TO_UUIDS.setdefault(_name, []).append(_uuid)


def resolve_tag_group_to_uuids(group: str) -> List[str]:
    """Return all tag UUIDs belonging to a named group.

    Raises ``KeyError`` if the group is unknown.
    """
    tag_names = tag_names_for_group(group)
    uuids: List[str] = []
    for name in tag_names:
        uuids.extend(_NAME_TO_UUIDS.get(name, []))
    logger.debug("Resolved group '%s' → %d tag names → %d UUIDs", group, len(tag_names), len(uuids))
    return uuids


def resolve_tag_groups_to_uuids(groups: List[str]) -> List[str]:
    """Resolve multiple groups, returning a deduplicated list of UUIDs."""
    seen: Set[str] = set()
    result: List[str] = []
    for g in groups:
        for uuid in resolve_tag_group_to_uuids(g):
            if uuid not in seen:
                seen.add(uuid)
                result.append(uuid)
    return result


def list_available_groups() -> List[str]:
    """Return all known tag group names."""
    return all_group_names()
