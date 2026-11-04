"""Pillow composition of Pulse's brand images.

Two features need to draw the same things from a user's stored uploads: the
public link-preview card (``routers/preview.py``) and the notification icon a
push carries. Both want a round avatar that falls back to the app's initials
tile, the same brand gradient, and the same waveform mark, so the drawing lives
here once and neither route can drift from the other.

Pillow is imported lazily inside each function. It is a declared dependency, but
keeping the import local means a deployment missing the wheel still boots and
only the image routes degrade, which is the behaviour the preview card already
had.
"""

import io
import os

from . import storage
from .models import Media, User

# The brand gradient, as the three stops the logo tile is drawn from. Shared by
# the favicon and every generated mark so they cannot drift apart.
BRAND_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (184, 242, 74)),
    (0.55, (140, 224, 143)),
    (1.0, (96, 165, 250)),
)

# The waveform drawn over the gradient, as points in the favicon's 24x24 space:
# a flat lead-in, a spike, a dip, a second spike and a flat tail. Kept as
# fractions so it scales to any tile.
WAVEFORM_POINTS: tuple[tuple[float, float], ...] = (
    (3.6, 12.0),
    (8.0, 12.0),
    (9.6, 7.2),
    (11.8, 16.8),
    (13.4, 12.0),
    (20.4, 12.0),
)
WAVEFORM_VIEWBOX = 24.0
# The near-black the waveform is stroked in, on top of the light gradient.
WAVEFORM_INK = (16, 22, 10)

BACKGROUND = (8, 9, 13)
FOREGROUND = (245, 246, 250)


def load_font(size: int):
    """A font at ``size``, preferring a real face and always returning one.

    The container that builds this app does not guarantee a font package, so the
    scalable default is the fallback rather than the first choice: DejaVu gives
    better metrics when it is present, and `load_default(size=...)` (Pillow
    >= 10.1) keeps the image legible when it is not.
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


def shade(rgb: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(max(0, min(255, channel + amount)) for channel in rgb)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    """Parse ``#rrggbb``, falling back to the app's accent rather than raising."""
    text = (value or "").lstrip("#")
    if len(text) != 6:
        return (99, 102, 241)
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (99, 102, 241)


def initials(name: str) -> str:
    parts = name.split()[:2]
    letters = "".join(part[0].upper() for part in parts if part)
    return letters or "?"


def avatar_bytes(db, user: User) -> bytes | None:
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


def circular_avatar(db, user: User, size: int):
    """A round avatar: the uploaded picture, or the same initials tile the app shows."""
    from PIL import Image, ImageDraw

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)

    raw = avatar_bytes(db, user)
    tile = None
    if raw:
        try:
            tile = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:  # noqa: BLE001 - a corrupt upload must not break the image
            tile = None

    if tile is None:
        # Reproduce the initials tile rather than leaving a blank disc, so a user
        # with no upload still gets a recognisable image.
        base = hex_to_rgb(user.avatar_color)
        tile = Image.new("RGB", (size, size), base)
        draw = ImageDraw.Draw(tile)
        for offset in range(size):
            ratio = offset / max(size - 1, 1)
            draw.line(
                [(0, offset), (size, offset)],
                fill=shade(base, int(-40 * ratio)),
            )
        font = load_font(int(size * 0.42))
        label = initials(user.display_name)
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


def _gradient(size: int):
    """The brand gradient, top to bottom, as an RGBA tile."""
    from PIL import Image, ImageDraw

    gradient = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = y / max(size - 1, 1)
        # Find the two stops this row sits between and interpolate across them.
        lower, upper = BRAND_STOPS[0], BRAND_STOPS[-1]
        for index in range(len(BRAND_STOPS) - 1):
            if BRAND_STOPS[index][0] <= t <= BRAND_STOPS[index + 1][0]:
                lower, upper = BRAND_STOPS[index], BRAND_STOPS[index + 1]
                break
        span = max(upper[0] - lower[0], 1e-6)
        ratio = (t - lower[0]) / span
        rgb = tuple(
            round(lower[1][channel] + (upper[1][channel] - lower[1][channel]) * ratio)
            for channel in range(3)
        )
        draw.line([(0, y), (size, y)], fill=(*rgb, 255))
    return gradient


def brand_mark(size: int, *, monochrome: bool = False):
    """The Pulse logo tile: the gradient with the waveform stroked over it.

    ``monochrome`` draws the waveform alone in white on transparency, which is
    what Android wants for a notification badge: it tints the badge to a single
    system colour, so a full-colour tile would be flattened into an unreadable
    blob. A silhouette of the mark survives the tint.
    """
    from PIL import Image, ImageDraw

    if monochrome:
        mark = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        ink = (255, 255, 255, 255)
    else:
        mark = _gradient(size)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, size - 1, size - 1), radius=int(size * 0.275), fill=255
        )
        rounded = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        rounded.paste(mark, (0, 0), mask)
        mark = rounded
        ink = (*WAVEFORM_INK, 255)

    scale = size / WAVEFORM_VIEWBOX
    points = [(x * scale, y * scale) for x, y in WAVEFORM_POINTS]
    ImageDraw.Draw(mark).line(
        points,
        fill=ink,
        width=max(2, round(size * 0.12)),
        joint="curve",
    )
    return mark


# How large the brand badge is relative to the avatar it is set into, and how
# wide the dark ring around it is. The ring is what keeps the mark legible on a
# dark profile picture as well as a light one.
BADGE_RATIO = 0.40
BADGE_RING = 0.045


def notification_icon(db, user: User, size: int = 192) -> bytes:
    """A push notification icon: the sender's avatar with the Pulse mark on it.

    The icon is the sender, because that is who the alert is about, and the
    brand mark is set into the corner so the notification still reads as coming
    from Pulse. A bare avatar loses the product; a bare logo loses the person.
    Returns PNG bytes.
    """
    from PIL import Image, ImageDraw

    icon = circular_avatar(db, user, size)

    badge_size = max(1, round(size * BADGE_RATIO))
    ring = max(1, round(size * BADGE_RING))
    badge = brand_mark(badge_size)

    # A dark disc behind the badge, a hair wider than the badge itself, so a
    # light mark on a light photo does not disappear into it.
    backing_size = badge_size + ring * 2
    backing = Image.new("RGBA", (backing_size, backing_size), (0, 0, 0, 0))
    ImageDraw.Draw(backing).ellipse(
        (0, 0, backing_size - 1, backing_size - 1), fill=(*BACKGROUND, 255)
    )

    margin = max(0, round(size * 0.015))
    corner = size - backing_size - margin
    icon.alpha_composite(backing, (corner, corner))
    icon.alpha_composite(badge, (corner + ring, corner + ring))

    buffer = io.BytesIO()
    icon.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _main() -> None:  # pragma: no cover - developer utility
    """Write the static brand assets the frontend serves.

    Run from ``backend/`` with ``python -m app.branding``. The notification badge
    is a plain file in ``public/`` rather than a route because it never varies by
    user, and a static file is one less request the API has to answer.
    """
    import sys

    target = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "public")
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        print(f"not a frontend public dir: {target}", file=sys.stderr)
        raise SystemExit(1)

    badge = brand_mark(96, monochrome=True)
    badge_path = os.path.join(target, "badge-96.png")
    badge.save(badge_path, format="PNG", optimize=True)
    print(f"wrote {badge_path}")


if __name__ == "__main__":  # pragma: no cover - developer utility
    _main()
