"""
Tag scope service.

Resolves a tag group name → list of tag UUIDs in that scope.
Thin wrapper around taxonomy + tag_ids for convenience.
"""
from __future__ import annotations

import logging
from typing import List

from scout.taxonomy.tag_ids import TAG_NAME_TO_UUIDS
from scout.taxonomy.tags import tag_names_for_group, all_group_names

logger = logging.getLogger("scout.services.tag_scope")


def resolve_tag_group_to_uuids(group: str) -> List[str]:
    """Return all tag UUIDs belonging to a named group.

    Raises ``KeyError`` if the group is unknown.
    """
    tag_names = tag_names_for_group(group)
    uuids: List[str] = []
    for name in tag_names:
        uuids.extend(TAG_NAME_TO_UUIDS.get(name, []))
    logger.debug("Resolved group '%s' → %d tag names → %d UUIDs", group, len(tag_names), len(uuids))
    return uuids


def list_available_groups() -> List[str]:
    """Return all known tag group names."""
    return all_group_names()
