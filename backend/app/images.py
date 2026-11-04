"""Shrink an uploaded image before it is stored.

Every byte an upload occupies is a byte in the ``media.blob`` column, and that
column lives in the database — which on the free tiers this app is deployed to
means a metered Neon allowance. A modern phone camera produces a 4-6MB JPEG that
is displayed at a fraction of that size, so storing it verbatim spends most of
the quota on pixels nobody sees.

The transform is deliberately conservative. It never upscales, never crops, and
never converts a format to one that would lose a feature (an animated GIF stays
an animated GIF, a transparent PNG keeps its alpha). If anything about the image
is unexpected, the original bytes are returned unchanged: a larger upload is a
far smaller problem than a corrupted one.

Pillow is imported lazily. It is already a dependency for the preview card, but
this module is on the upload path for every file, and a deployment that somehow
lacks the wheel should still accept uploads rather than 500.
"""

import io
import logging

logger = logging.getLogger("pulse.images")

# The longest edge kept, in pixels. A photo is shown in a feed or a message at
# roughly 800px wide on the largest phone, so 1600 leaves headroom for a 2x
# display and for someone opening the full image, while discarding the rest of a
# 4000px capture. Anything already within this is left at its own size.
MAX_EDGE = 1600

# JPEG quality. 82 is visually indistinguishable from the original at these
# dimensions on a phone screen and typically cuts the bytes by 70-85%.
JPEG_QUALITY = 82

# Formats that are passed through untouched, with the reason:
#   GIF  - re-encoding drops the animation, which is the whole point of the file
#   SVG  - vector, and not accepted as an upload in the first place
#   others - not image types this app accepts, so they never reach here
PASSTHROUGH = {"image/gif"}

# Encodings the transform understands. A type outside this set is returned as-is
# rather than guessed at.
SUPPORTED = {"image/jpeg", "image/png", "image/webp"}

# A profile picture or cover is drawn far smaller than a feed photo. The cover is
# the wider of the two and is shown as a banner, so it gets the larger budget;
# an avatar is never displayed above a couple of hundred pixels. Both are still
# generous next to the largest display size, so a retina screen stays sharp.
AVATAR_MAX_EDGE = 512
COVER_MAX_EDGE = 1280


def optimize_image(
    data: bytes, content_type: str, max_edge: int = MAX_EDGE
) -> tuple[bytes, str]:
    """Return possibly-smaller image bytes and the content type they are in.

    ``max_edge`` is the longest edge to keep, so a caller that knows the display
    size can ask for less. A profile picture is rendered at roughly a hundred
    pixels, and storing one at feed resolution wastes most of what it occupies.

    Returns the input unchanged when the image cannot be improved: an unsupported
    type, an animated GIF, or any decode failure. The content type is returned
    alongside because re-encoding can change it — a JPEG stays a JPEG, but a PNG
    is re-emitted as a PNG so transparency survives.
    """
    if content_type in PASSTHROUGH or content_type not in SUPPORTED:
        return data, content_type

    original_type = content_type

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a declared dependency
        logger.warning("Pillow is not installed; uploads are stored unoptimized")
        return data, content_type

    try:
        with Image.open(io.BytesIO(data)) as image:
            # `n_frames` is only present on some plugins; a GIF that slipped
            # through PASSTHROUGH, or any other animation, must not be flattened.
            if getattr(image, "n_frames", 1) > 1:
                return data, content_type

            # `draft` lets the JPEG decoder do the downscale for free while
            # reading, which avoids materialising a full-size bitmap in memory
            # for a large photo. A no-op for other formats.
            image.draft("RGB", (max_edge, max_edge))

            width, height = image.size
            if width > max_edge or height > max_edge:
                scale = max_edge / max(width, height)
                image = image.resize(
                    (max(1, round(width * scale)), max(1, round(height * scale))),
                    Image.LANCZOS,
                )

            buffer = io.BytesIO()
            if content_type == "image/jpeg":
                # A JPEG has no alpha channel. A CMYK or palette image must be
                # converted or the save fails, and `RGBA` would be rejected too.
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            elif content_type == "image/png":
                # Keep the alpha channel: a PNG with transparency flattened onto
                # black would look broken against the app's background.
                if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                    image = image.convert("RGBA")
                image.save(buffer, format="PNG", optimize=True)
            else:  # image/webp
                if image.mode not in ("RGB", "RGBA", "L", "LA"):
                    image = image.convert("RGBA")
                image.save(buffer, format="WEBP", quality=JPEG_QUALITY, method=4)

            optimized = buffer.getvalue()
    except Exception as exc:  # noqa: BLE001 - never fail an upload over this
        logger.warning("could not optimize a %s upload: %s", content_type, exc)
        return data, content_type

    # A re-encode can come out larger (an already-tight image, or one whose
    # dimensions were under the limit). Keeping the smaller of the two means the
    # optimization can never cost storage.
    if len(optimized) >= len(data):
        return data, original_type
    return optimized, content_type
