"""Tests for the public link-preview page and generated card.

These endpoints are the only ones that answer without a bearer token, so the
tests here are as much about what they must not expose as about what they must
render.
"""

import io
import re

import pytest

from app.routers import preview

# A 1x1 PNG, enough to exercise the upload and blob paths.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)

WHATSAPP_UA = "WhatsApp/2.23.20.0"


def meta(html: str, prop: str) -> str | None:
    """The content of an ``og:``/``name`` meta tag, or None when absent."""
    pattern = (
        rf'<meta\s+(?:property|name)="{re.escape(prop)}"\s+content="([^"]*)"'
        rf'|<meta\s+content="([^"]*)"\s+(?:property|name)="{re.escape(prop)}"'
    )
    match = re.search(pattern, html)
    if not match:
        return None
    return match.group(1) if match.group(1) is not None else match.group(2)


def set_avatar(client, headers, data: bytes = PNG) -> None:
    """Upload a profile picture through the real endpoint."""
    response = client.post(
        "/api/users/me/avatar",
        headers=headers,
        files={"file": ("a.png", io.BytesIO(data), "image/png")},
    )
    assert response.status_code == 200, response.text


def test_preview_page_needs_no_authentication(client, make_user):
    """A crawler has no account, so the page must answer anonymously."""
    make_user("grace")
    response = client.get("/api/preview/u/grace", headers={"User-Agent": WHATSAPP_UA})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")


def test_preview_page_carries_the_tags_a_crawler_reads(client, make_user):
    make_user("grace", display_name="Grace Hopper", bio="Compilers and nanoseconds.")
    html = client.get("/api/preview/u/grace").text

    assert meta(html, "og:title") == "Grace Hopper (@grace)"
    assert meta(html, "og:description") == "Compilers and nanoseconds."
    assert meta(html, "og:type") == "profile"
    assert meta(html, "og:site_name") == "Pulse"
    assert meta(html, "twitter:card") == "summary_large_image"


def test_preview_image_url_is_absolute_https_and_points_at_the_card(client, make_user):
    make_user("grace")
    html = client.get("/api/preview/u/grace").text
    image = meta(html, "og:image")
    assert image is not None
    assert image.startswith("https://")
    assert image.endswith("/api/preview/card/grace")
    # The declared dimensions must match what the card actually renders, or
    # crawlers letterbox it.
    assert meta(html, "og:image:width") == str(preview.CARD_WIDTH)
    assert meta(html, "og:image:height") == str(preview.CARD_HEIGHT)


def test_preview_uses_the_forwarded_host_behind_a_proxy(client, make_user):
    """The public origin is the site, not the API host the proxy dialled."""
    make_user("grace")
    response = client.get(
        "/api/preview/u/grace",
        headers={
            "x-forwarded-host": "pulse.example.com",
            "x-forwarded-proto": "https",
        },
    )
    html = response.text
    assert meta(html, "og:url") == "https://pulse.example.com/u/grace"
    assert meta(html, "og:image") == "https://pulse.example.com/api/preview/card/grace"


def test_preview_does_not_leak_private_fields(client, make_user):
    make_user("grace")
    html = client.get("/api/preview/u/grace").text
    assert "grace@example.com" not in html
    assert "password" not in html.lower()


def test_preview_escapes_a_hostile_display_name(client, make_user):
    """The page is HTML, so a name is data and must never become markup."""
    make_user("grace", display_name='<script>alert(1)</script>')
    html = client.get("/api/preview/u/grace").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_preview_truncates_a_long_bio(client, make_user):
    # 280 is the schema's ceiling, which is still longer than the 160 a preview
    # card shows, so this is the longest description the endpoint can receive.
    make_user("grace", bio="word " * 55)
    html = client.get("/api/preview/u/grace").text
    description = meta(html, "og:description")
    assert description is not None
    assert len(description) <= preview.DESCRIPTION_LIMIT
    assert description.endswith("…")


def test_preview_unknown_user_is_a_404_page_not_an_api_error(client):
    response = client.get("/api/preview/u/nobody")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "not found" in response.text.lower()


def test_preview_is_case_insensitive_like_the_profile_route(client, make_user):
    make_user("grace")
    assert client.get("/api/preview/u/Grace").status_code == 200


def test_preview_page_is_cacheable(client, make_user):
    make_user("grace")
    response = client.get("/api/preview/u/grace")
    assert "max-age" in response.headers["cache-control"]


def test_card_is_a_jpeg_of_the_expected_size(client, make_user):
    from PIL import Image

    make_user("grace")
    response = client.get("/api/preview/card/grace")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    image = Image.open(io.BytesIO(response.content))
    assert image.size == (preview.CARD_WIDTH, preview.CARD_HEIGHT)


def test_card_stays_within_the_whatsapp_size_limit(client, make_user):
    """WhatsApp silently drops an og:image over roughly 300KB.

    A noisy photo is the worst case — it barely compresses — so this is the
    image most likely to break a preview, and the one worth asserting on.
    """
    from PIL import Image

    headers, me = make_user("grace")
    noise = Image.effect_noise((900, 900), 100).convert("RGB")
    buffer = io.BytesIO()
    noise.save(buffer, format="PNG")
    set_avatar(client, headers, buffer.getvalue())

    response = client.get("/api/preview/card/grace")
    assert response.status_code == 200
    assert len(response.content) < 300 * 1024, f"card is {len(response.content)} bytes"


def test_card_renders_without_an_uploaded_picture(client, make_user):
    """Most accounts have no upload, so the initials tile is the common case."""
    make_user("grace", display_name="Grace Hopper")
    response = client.get("/api/preview/card/grace")
    assert response.status_code == 200
    assert len(response.content) > 0


def test_card_unknown_user_is_404(client):
    assert client.get("/api/preview/card/nobody").status_code == 404


def test_card_is_anonymous(client, make_user):
    make_user("grace")
    assert client.get("/api/preview/card/grace", headers={}).status_code == 200


def test_card_uses_the_uploaded_avatar(client, make_user):
    """A picture upload must change the card, or the preview is wrong for real accounts."""
    from PIL import Image

    headers, _ = make_user("grace")
    plain = client.get("/api/preview/card/grace").content

    # A saturated red square is unmistakable once it is pasted onto the card.
    solid = Image.new("RGB", (400, 400), (220, 20, 20))
    buffer = io.BytesIO()
    solid.save(buffer, format="PNG")
    set_avatar(client, headers, buffer.getvalue())

    with_photo = client.get("/api/preview/card/grace").content
    assert with_photo != plain
    image = Image.open(io.BytesIO(with_photo)).convert("RGB")
    # Sample the centre of the avatar disc, where the upload should now be.
    centre = image.getpixel((205, 315))
    assert centre[0] > 150 and centre[1] < 120, f"expected the red upload, got {centre}"


@pytest.mark.parametrize("username", ["Grace", "GRACE", "grace"])
def test_card_lookup_is_case_insensitive(client, make_user, username):
    make_user("grace")
    assert client.get(f"/api/preview/card/{username}").status_code == 200
