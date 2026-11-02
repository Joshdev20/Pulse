"""Tests for the upload optimizer.

These assert the property that matters to the database bill -- the stored bytes
are smaller than the uploaded ones -- rather than a particular byte count, which
would change with the Pillow version.
"""

import io

import pytest
from PIL import Image

from app import images


def photo(width: int = 4000, height: int = 3000, fmt: str = "JPEG") -> bytes:
    """A noisy image, so the encoder cannot cheat by compressing flat colour.

    A solid-colour test image would compress to almost nothing at any quality and
    would make the quality setting look effective when it is not.
    """
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = ((x * 7 + y * 13) % 256, (x * 3) % 256, (y * 5) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def test_a_large_photo_is_shrunk_substantially():
    """The whole point: a camera-sized photo must not be stored verbatim."""
    original = photo()
    optimized, content_type = images.optimize_image(original, "image/jpeg")
    assert content_type == "image/jpeg"
    assert len(optimized) < len(original)
    # The quota only improves if the saving is real, not a rounding error.
    assert len(optimized) < len(original) / 2


def test_the_longest_edge_is_capped():
    """Dimensions, not just bytes, are bounded so the decode cost is too."""
    optimized, _ = images.optimize_image(photo(), "image/jpeg")
    with Image.open(io.BytesIO(optimized)) as result:
        assert max(result.size) <= images.MAX_EDGE


def test_the_aspect_ratio_is_preserved():
    """A resized photo must not be distorted or cropped."""
    optimized, _ = images.optimize_image(photo(4000, 1000), "image/jpeg")
    with Image.open(io.BytesIO(optimized)) as result:
        # 4:1 in, 4:1 out, within a pixel of rounding.
        assert abs(result.size[0] / result.size[1] - 4) < 0.02


def test_a_small_image_is_not_upscaled():
    """An image already within the limit keeps its own size."""
    original = photo(320, 240)
    optimized, _ = images.optimize_image(original, "image/jpeg")
    with Image.open(io.BytesIO(optimized)) as result:
        assert result.size == (320, 240)


def test_an_already_optimized_image_is_left_alone():
    """A re-encode must never cost storage, so the smaller of the two is kept."""
    small = photo(200, 150)
    optimized, _ = images.optimize_image(small, "image/jpeg")
    assert len(optimized) <= len(small)


def test_a_png_keeps_its_transparency():
    """Flattening alpha would put a black box behind a logo."""
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    optimized, content_type = images.optimize_image(buffer.getvalue(), "image/png")
    assert content_type == "image/png"
    with Image.open(io.BytesIO(optimized)) as result:
        assert result.mode in ("RGBA", "LA", "P")
        assert result.getpixel((50, 50))[3] == 0


def test_an_animated_gif_is_passed_through_untouched():
    """Re-encoding would drop the animation, which is the reason for the file."""
    frames = [Image.new("P", (10, 10), i) for i in range(3)]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:])
    original = buffer.getvalue()
    optimized, content_type = images.optimize_image(original, "image/gif")
    assert optimized == original
    assert content_type == "image/gif"


def test_a_non_image_type_is_passed_through():
    """A video or audio upload must not be handed to the image decoder."""
    payload = b"not an image at all"
    optimized, content_type = images.optimize_image(payload, "video/mp4")
    assert optimized == payload
    assert content_type == "video/mp4"


def test_corrupt_bytes_are_returned_unchanged():
    """A decode failure must not lose the upload.

    The bytes are sniffed as an image on the way in, so a failure here is a
    decoder limitation, not a bad upload: storing the original is the safe answer.
    """
    broken = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    optimized, content_type = images.optimize_image(broken, "image/jpeg")
    assert optimized == broken
    assert content_type == "image/jpeg"


def test_a_cmyk_jpeg_is_converted_rather_than_failing():
    """A CMYK JPEG cannot be saved as RGB without conversion, so it is converted."""
    image = Image.new("CMYK", (3000, 2000))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    optimized, content_type = images.optimize_image(buffer.getvalue(), "image/jpeg")
    assert content_type == "image/jpeg"
    with Image.open(io.BytesIO(optimized)) as result:
        assert result.mode == "RGB"


@pytest.mark.parametrize("fmt,content_type", [("JPEG", "image/jpeg"), ("PNG", "image/png")])
def test_the_result_is_still_a_readable_image_of_the_same_format(fmt, content_type):
    """The stored bytes must decode, or the file would be served broken."""
    optimized, returned_type = images.optimize_image(photo(3000, 2000, fmt), content_type)
    assert returned_type == content_type
    with Image.open(io.BytesIO(optimized)) as result:
        assert result.size[0] > 0
        result.load()
