"""The XML sitemap, so a search engine can discover the public profiles.

Served from the site root rather than under ``/api`` because the sitemap
protocol expects it there and ``robots.txt`` points at it. It lists only the
pages that genuinely answer without a session, which today means the profiles.

The endpoints here are unauthenticated for the same reason the preview pages
are: a crawler has no account. They expose nothing a public profile does not
already show — a username and a last-modified date.
"""

from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from ..deps import DbSession, public_base
from ..models import Post, User

router = APIRouter(tags=["sitemap"])

# The sitemap protocol caps a single file at 50,000 URLs. A cap well below that
# keeps one query and one response bounded; past it the sitemap should become an
# index of several files, which is a job for when the site has that many
# profiles rather than now.
MAX_URLS = 5000


def _last_modified(db: DbSession, user_ids: list[int]) -> dict[int, object]:
    """The newest post time per author, in one grouped query.

    Used for ``lastmod``: a profile changes when its posts do, so the latest
    post is a better signal than the account creation date. One query for the
    whole page rather than one per profile.
    """
    if not user_ids:
        return {}
    rows = db.execute(
        select(Post.author_id, func.max(Post.created_at))
        .where(Post.author_id.in_(user_ids))
        .group_by(Post.author_id)
    ).all()
    return {author_id: stamp for author_id, stamp in rows}


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request, db: DbSession) -> Response:
    base = public_base(request)
    users = list(
        db.scalars(
            select(User)
            .where(User.is_active.is_(True), User.is_banned.is_(False))
            .order_by(User.id)
            .limit(MAX_URLS)
        ).all()
    )
    newest = _last_modified(db, [user.id for user in users])

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for user in users:
        loc = f"{base}/u/{user.username}"
        stamp = newest.get(user.id) or user.created_at
        parts.append("  <url>")
        parts.append(f"    <loc>{escape(loc)}</loc>")
        if stamp is not None:
            parts.append(f"    <lastmod>{stamp.date().isoformat()}</lastmod>")
        # Every profile is equally worth crawling, and none of them is a
        # time-sensitive page, so no changefreq or priority hints: search
        # engines ignore them and a wrong value is worse than none.
        parts.append("  </url>")
    parts.append("</urlset>")
    body = "\n".join(parts) + "\n"
    return Response(
        content=body,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600, s-maxage=86400"},
    )
