"""Public link-preview pages and images for shared profiles.

Crawlers (WhatsApp, Facebook, Slack, iMessage, …) do not run JavaScript, so a
shared ``/u/<username>`` link would unfurl as the generic SPA shell — no title,
no picture, which is what the share button was meant to avoid. These endpoints
render a small server-side page carrying Open Graph tags and a generated
1200x630 card, which is what those crawlers actually read.

They are the only endpoints in the app that answer without a bearer token,
because a crawler has no account. They expose nothing a public profile does not
already show, and the card is composed here rather than serving the raw upload,
so no arbitrary media id can be reached through them.
"""

import html
import io
import json
import os

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import func, select

from .. import storage
from ..deps import DbSession, public_base
from ..models import Media, Post, User

router = APIRouter(prefix="/api/preview", tags=["preview"])

# The size every major crawler expects. WhatsApp is the strictest consumer: it
# silently drops a card larger than roughly 300KB, so the JPEG is written at a
# quality that keeps a photographic avatar well clear of that ceiling.
CARD_WIDTH = 1200
CARD_HEIGHT = 630
CARD_QUALITY = 82

BACKGROUND = (8, 9, 13)
FOREGROUND = (245, 246, 250)
MUTED = (150, 155, 170)

DESCRIPTION_LIMIT = 160

# How many posts the server-rendered profile quotes. Enough to give a search
# engine real text to index, small enough that the page stays a preview rather
# than a full mirror of the app.
RECENT_POSTS = 20


def _load_font(size: int):
    """A font at ``size``, preferring a real face and always returning one.

    The container that builds this app does not guarantee a font package, so the
    scalable default is the fallback rather than the first choice: DejaVu gives
    better metrics when it is present, and `load_default(size=...)` (Pillow
    >= 10.1) keeps the card legible when it is not.
    """
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


def _shade(rgb: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, channel + amount)) for channel in rgb)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb``, falling back to the app's accent rather than raising."""
    text = (value or "").lstrip("#")
    if len(text) != 6:
        return (99, 102, 241)
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (99, 102, 241)


def _initials(name: str) -> str:
    parts = name.split()[:2]
    letters = "".join(part[0].upper() for part in parts if part)
    return letters or "?"


def _break_long_word(draw, word: str, font, max_width: int) -> list[str]:
    """Split a word that is wider than the column into fitting pieces.

    A display name or bio can be one long unbroken token, which word wrapping
    alone cannot lay out — the token would be drawn straight off the card.
    """
    pieces: list[str] = []
    current = ""
    for character in word:
        candidate = current + character
        if current and draw.textlength(candidate, font=font) > max_width:
            pieces.append(current)
            current = character
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _wrap(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    """Greedy word wrap that stops after ``max_lines``, ellipsising the last."""
    words: list[str] = []
    for word in text.split():
        if draw.textlength(word, font=font) > max_width:
            words.extend(_break_long_word(draw, word, font, max_width))
        else:
            words.append(word)

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)

    if not lines:
        return []

    # Anything that did not fit is replaced by an ellipsis on the final line, so
    # a long bio is visibly truncated rather than silently cut mid-word.
    consumed = len(" ".join(lines).replace(" ", ""))
    total = len("".join(words))
    if consumed < total:
        last = lines[-1]
        while last and draw.textlength(f"{last}…", font=font) > max_width:
            last = last[:-1]
        lines[-1] = f"{last}…"
    return lines


def _fit_font(draw, text: str, max_width: int, sizes: tuple[int, ...]):
    """The largest font in ``sizes`` that renders ``text`` within ``max_width``.

    Display names run to 64 characters, which at the intended size is several
    times the available width and would be drawn straight off the card. Shrinking
    to fit keeps a long name inside the layout; the smallest size is returned
    when even that overflows, and the caller then wraps.
    """
    for size in sizes:
        font = _load_font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font, size
    return _load_font(sizes[-1]), sizes[-1]


def _avatar_bytes(db: DbSession, user: User) -> bytes | None:
    """The user's uploaded picture, from the row or the file it was written to."""
    if user.avatar_media_id is None:
        return None
    media = db.get(Media, user.avatar_media_id)
    if media is None:
        return None
    if media.blob is not None:
        return media.blob
    try:
        path = storage.resolve(media.stored_name)
    except ValueError:
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _circular_avatar(db: DbSession, user: User, size: int):
    """A round avatar: the uploaded picture, or the same initials tile the app shows."""
    from PIL import Image, ImageDraw

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)

    raw = _avatar_bytes(db, user)
    tile = None
    if raw:
        try:
            tile = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:  # noqa: BLE001 - a corrupt upload must not break the card
            tile = None

    if tile is None:
        # Reproduce the initials tile rather than leaving a blank disc, so a user
        # with no upload still gets a recognisable preview.
        base = _hex_to_rgb(user.avatar_color)
        tile = Image.new("RGB", (size, size), base)
        draw = ImageDraw.Draw(tile)
        for offset in range(size):
            ratio = offset / max(size - 1, 1)
            draw.line(
                [(0, offset), (size, offset)],
                fill=_shade(base, int(-40 * ratio)),
            )
        font = _load_font(int(size * 0.42))
        label = _initials(user.display_name)
        box = draw.textbbox((0, 0), label, font=font)
        draw.text(
            ((size - (box[2] - box[0])) / 2 - box[0], (size - (box[3] - box[1])) / 2 - box[1]),
            label,
            font=font,
            fill=(255, 255, 255),
        )
    else:
        # Cover-crop to a square so a landscape upload is not stretched.
        width, height = tile.size
        edge = min(width, height)
        tile = tile.crop(
            ((width - edge) // 2, (height - edge) // 2, (width + edge) // 2, (height + edge) // 2)
        ).resize((size, size), Image.LANCZOS)

    canvas.paste(tile, (0, 0), mask)
    return canvas


def _render_card(db: DbSession, user: User) -> bytes:
    """Compose the 1200x630 preview image as a JPEG."""
    from PIL import Image, ImageDraw

    accent = _hex_to_rgb(user.avatar_color)
    card = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(card)

    # A vertical wash from the account's own colour, matching how the app tints
    # a profile, so the card reads as that person rather than as a generic box.
    for offset in range(CARD_HEIGHT):
        ratio = offset / CARD_HEIGHT
        draw.line(
            [(0, offset), (CARD_WIDTH, offset)],
            fill=_shade(BACKGROUND, int(26 * (1 - ratio))),
        )
    glow = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BACKGROUND)
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (-260, -300, 620, 360),
        fill=tuple(min(255, channel + 70) for channel in accent),
    )
    card = Image.blend(card, glow, 0.22)
    draw = ImageDraw.Draw(card)
    draw.rectangle(
        [(0, CARD_HEIGHT - 10), (CARD_WIDTH, CARD_HEIGHT)],
        fill=accent,
    )

    avatar = _circular_avatar(db, user, 230)
    card.paste(avatar, (90, 200), avatar)

    text_left = 380
    text_width = CARD_WIDTH - text_left - 90

    name_font, name_size = _fit_font(draw, user.display_name, text_width, (72, 64, 56, 48, 42))
    handle_font = _load_font(40)
    bio_font = _load_font(34)
    meta_font = _load_font(30)

    # A name that even the smallest size cannot fit is wrapped to two lines
    # rather than drawn past the edge of the card.
    name_lines = (
        [user.display_name]
        if draw.textlength(user.display_name, font=name_font) <= text_width
        else _wrap(draw, user.display_name, name_font, text_width, 2)
    )
    line_height = int(name_size * 1.2)
    for index, line in enumerate(name_lines):
        draw.text((text_left, 196 + index * line_height), line, font=name_font, fill=FOREGROUND)

    handle_top = 196 + len(name_lines) * line_height + 14
    draw.text((text_left, handle_top), f"@{user.username}", font=handle_font, fill=MUTED)

    bio = (user.bio or "").strip()
    if bio:
        bio_top = handle_top + 68
        for index, line in enumerate(_wrap(draw, bio, bio_font, text_width, 3)):
            draw.text((text_left, bio_top + index * 46), line, font=bio_font, fill=FOREGROUND)

    meta = f"{user.follower_count} followers"
    if user.is_verified:
        meta = f"Verified · {meta}"
    draw.text((90, CARD_HEIGHT - 96), meta, font=meta_font, fill=MUTED)
    draw.text((CARD_WIDTH - 260, CARD_HEIGHT - 96), "Pulse", font=meta_font, fill=FOREGROUND)

    buffer = io.BytesIO()
    card.save(buffer, format="JPEG", quality=CARD_QUALITY, optimize=True, progressive=True)
    return buffer.getvalue()


def _get_user_or_404(db: DbSession, username: str) -> User:
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


def _public_base(request: Request) -> str:
    """The origin the visitor actually used, for the URLs in the preview page.

    Thin alias for the shared helper so the existing call sites keep reading
    naturally; the implementation lives in ``deps`` because the sitemap needs
    the same origin.
    """
    return public_base(request)


def _card_url(request: Request, username: str) -> str:
    return f"{_public_base(request)}/api/preview/card/{username}"


def _page_url(request: Request, username: str) -> str:
    return f"{_public_base(request)}/u/{username}"


def _description(user: User) -> str:
    bio = " ".join((user.bio or "").split())
    if not bio:
        bio = f"{user.display_name} (@{user.username}) on Pulse."
    if len(bio) > DESCRIPTION_LIMIT:
        bio = bio[: DESCRIPTION_LIMIT - 1].rstrip() + "…"
    return bio


def _escape(value: str) -> str:
    return html.escape(value, quote=True)


def _plain(text: str, limit: int = 280) -> str:
    """Collapse whitespace and clip, for post bodies quoted into the page."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def _json_ld(user: User, request: Request, posts: list[Post]) -> str:
    """A ProfilePage graph, so a crawler reads structure rather than guessing.

    The JSON is embedded in a ``<script>`` element, where the HTML parser looks
    for the literal ``</script>`` rather than parsing JSON. ``json.dumps`` does
    not escape ``<``, ``>`` or ``&``, so a display name of
    ``</script><script>…`` would close the element and run as script on the
    site's origin. Escaping those three to their ``\\uXXXX`` forms is the fix:
    the bytes stay valid JSON and cannot terminate the element.
    """
    page_url = _page_url(request, user.username)
    graph: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "mainEntity": {
            "@type": "Person",
            "name": user.display_name,
            "alternateName": f"@{user.username}",
            "description": _description(user),
            "url": page_url,
            "interactionStatistic": [
                {
                    "@type": "InteractionCounter",
                    "interactionType": "https://schema.org/FollowAction",
                    "userInteractionCount": user.follower_count,
                }
            ],
        },
    }
    person = graph["mainEntity"]
    assert isinstance(person, dict)
    if user.bio:
        person["description"] = _plain(user.bio, DESCRIPTION_LIMIT)
    if user.website:
        person["sameAs"] = [user.website]
    if user.location:
        person["homeLocation"] = {"@type": "Place", "name": user.location}
    if posts:
        graph["hasPart"] = [
            {
                "@type": "SocialMediaPosting",
                "headline": _plain(post.body, 110),
                "datePublished": post.created_at.isoformat(),
                # The app serves a single post at /p/<id>, not under the author's
                # profile, so this must not be built by appending to page_url.
                "url": f"{_public_base(request)}/p/{post.id}",
                "author": {"@type": "Person", "name": user.display_name},
            }
            for post in posts
        ]
    return _json_for_script(graph)


def _json_for_script(value: object) -> str:
    """``json.dumps`` with the characters that could end a script element escaped."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _render_posts(posts: list[Post]) -> str:
    """The recent posts as plain HTML.

    This is the page's indexable substance: a crawler that runs no JavaScript
    otherwise sees only the profile header, which is not enough for a search
    engine to judge what the profile is about.
    """
    if not posts:
        return "<p>No posts yet.</p>"
    items = "\n".join(
        f'        <li><article><p>{_escape(_plain(post.body))}</p>'
        f'<p><time datetime="{post.created_at.isoformat()}">'
        f"{post.created_at.date().isoformat()}</time></p></article></li>"
        for post in posts
        if post.body
    )
    if not items:
        return "<p>No posts yet.</p>"
    return f"      <ol>\n{items}\n      </ol>"


def _render_page(user: User, request: Request, posts: list[Post]) -> str:
    title = f"{user.display_name} (@{user.username}) · Pulse"
    description = _description(user)
    page_url = _page_url(request, user.username)
    image_url = _card_url(request, user.username)
    followers = f"{user.follower_count} followers"
    json_ld = _json_ld(user, request, posts)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{_escape(title)}</title>
    <meta name="description" content="{_escape(description)}" />
    <!-- Indexable: this page exists so a search engine can read a profile, so
         an explicit directive is safer than relying on the default. -->
    <meta name="robots" content="index, follow" />
    <link rel="canonical" href="{_escape(page_url)}" />
    <meta property="og:type" content="profile" />
    <meta property="og:site_name" content="Pulse" />
    <meta property="og:title" content="{_escape(user.display_name)} (@{_escape(user.username)})" />
    <meta property="og:description" content="{_escape(description)}" />
    <meta property="og:url" content="{_escape(page_url)}" />
    <meta property="og:image" content="{_escape(image_url)}" />
    <meta property="og:image:secure_url" content="{_escape(image_url)}" />
    <meta property="og:image:type" content="image/jpeg" />
    <meta property="og:image:width" content="{CARD_WIDTH}" />
    <meta property="og:image:height" content="{CARD_HEIGHT}" />
    <meta property="og:image:alt" content="{_escape(f'{user.display_name} on Pulse')}" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="{_escape(title)}" />
    <meta name="twitter:description" content="{_escape(description)}" />
    <meta name="twitter:image" content="{_escape(image_url)}" />
    <script type="application/ld+json">{json_ld}</script>
  </head>
  <body>
    <main>
      <h1>{_escape(user.display_name)}</h1>
      <p>@{_escape(user.username)} · {_escape(followers)}</p>
      <p>{_escape(description)}</p>
      <p><a href="{_escape(page_url)}">Open {_escape(user.display_name)} on Pulse</a></p>
      <h2>Recent posts</h2>
{_render_posts(posts)}
    </main>
  </body>
</html>
"""


def _not_found_page() -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8" />'
        "<title>Profile not found · Pulse</title>"
        '<meta name="robots" content="noindex" /></head>'
        "<body><h1>Profile not found</h1>"
        '<p>This Pulse profile does not exist.</p></body></html>'
    )


@router.get("/u/{username}", response_class=Response, include_in_schema=False)
def preview_profile(username: str, request: Request, db: DbSession) -> Response:
    """The page a crawler reads for a profile link.

    Doubles as the indexable HTML for the profile: a crawler that runs no
    JavaScript would otherwise see an empty SPA shell. The recent posts are
    rendered inline for the same reason.
    """
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    headers = {"Cache-Control": "public, max-age=300, s-maxage=900"}
    if user is None:
        return Response(content=_not_found_page(), status_code=404, media_type="text/html")
    posts = list(
        db.scalars(
            select(Post)
            .where(Post.author_id == user.id, Post.body != "")
            .order_by(Post.created_at.desc(), Post.id.desc())
            .limit(RECENT_POSTS)
        ).all()
    )
    return Response(
        content=_render_page(user, request, posts), media_type="text/html", headers=headers
    )


@router.get("/card/{username}", include_in_schema=False)
def preview_card(username: str, db: DbSession) -> Response:
    """The generated preview image referenced by ``og:image``."""
    user = _get_user_or_404(db, username)
    try:
        image = _render_card(db, user)
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Preview images are unavailable: Pillow is not installed",
        ) from None
    return Response(
        content=image,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "public, max-age=3600, s-maxage=86400",
            "Content-Length": str(len(image)),
        },
    )
