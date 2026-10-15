"""Helpers for extracting and resolving hashtags."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Tag

TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]{1,50})")
MAX_TAGS_PER_POST = 10


def extract_tag_names(body: str) -> list[str]:
    """Return lowercased, de-duplicated hashtags in order of first appearance."""
    seen: dict[str, None] = {}
    for match in TAG_RE.findall(body):
        seen.setdefault(match.lower(), None)
    return list(seen)[:MAX_TAGS_PER_POST]


def resolve_tags(db: Session, names: list[str]) -> list[Tag]:
    if not names:
        return []
    existing = {tag.name: tag for tag in db.scalars(select(Tag).where(Tag.name.in_(names))).all()}
    tags: list[Tag] = []
    created = False
    for name in names:
        tag = existing.get(name)
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            existing[name] = tag
            created = True
        tags.append(tag)
    if created:
        # Sessions run with autoflush disabled, so flush now to make the new rows
        # visible to subsequent lookups within the same transaction.
        db.flush()
    return tags
