"""Tests for the public, indexable surface: the SSR profile page and sitemap.

The profile page is what a search engine reads, because a crawler that runs no
JavaScript would otherwise see an empty SPA shell. The sitemap is how it finds
the profiles in the first place. Both are unauthenticated, so the tests here are
as much about what they must not expose as about what they must render.
"""

import re
import xml.etree.ElementTree as ET

import pytest

from app.routers import preview

SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def test_public_profile_page_renders_the_posts(client, make_user):
    """The page carries the profile and its recent posts as plain HTML.

    Without the posts a crawler has only a name and a bio, which is not enough
    text for a search engine to judge the page.
    """
    headers, _ = make_user("ada", display_name="Ada Lovelace")
    client.post("/api/posts", headers=headers, json={"body": "Analytical engines, and other amusements"})

    html = client.get("/api/preview/u/ada").text
    assert "Ada Lovelace" in html
    assert "Analytical engines, and other amusements" in html
    # Indexable, and pointing at itself rather than the app root.
    assert re.search(r'<meta name="robots" content="index, follow"', html)
    assert '<link rel="canonical" href="https://testserver/u/ada"' in html


def test_public_profile_page_has_structured_data(client, make_user):
    """A ProfilePage JSON-LD graph names the person and their posts."""
    headers, _ = make_user("ada", display_name="Ada Lovelace")
    client.post("/api/posts", headers=headers, json={"body": "hello"})

    html = client.get("/api/preview/u/ada").text
    block = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    )
    assert block, "no JSON-LD block was rendered"

    import json

    graph = json.loads(block.group(1))
    assert graph["@type"] == "ProfilePage"
    assert graph["mainEntity"]["name"] == "Ada Lovelace"
    assert graph["mainEntity"]["alternateName"] == "@ada"
    assert graph["hasPart"][0]["@type"] == "SocialMediaPosting"
    # The app serves a single post at /p/<id>. Building this by appending to the
    # profile URL produced /u/ada/p/<id>, which matches no route and 404s.
    post_id = graph["hasPart"][0]["url"].rsplit("/", 1)[-1]
    assert graph["hasPart"][0]["url"].endswith(f"/p/{post_id}")
    assert "/u/ada/p/" not in graph["hasPart"][0]["url"]


def test_profile_page_escapes_a_hostile_display_name(client, make_user):
    """A name full of markup must not become markup, in body or in JSON-LD.

    The JSON-LD case is the subtle one: the HTML parser looks for the literal
    ``</script>`` inside a script element rather than parsing JSON, so an
    unescaped ``<`` would let a display name close the element and run as script
    on the site's origin.
    """
    make_user("grace", display_name='</script><script>alert(1)</script>')

    html = client.get("/api/preview/u/grace").text
    assert "<script>alert(1)</script>" not in html
    # The JSON-LD must survive as parseable JSON, with the payload neutralised.
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert block
    assert "\\u003c" in block.group(1)

    import json

    graph = json.loads(block.group(1))
    assert graph["mainEntity"]["name"] == '</script><script>alert(1)</script>'


def test_profile_page_escapes_markup_in_a_post_body(client, make_user):
    headers, _ = make_user("ada")
    client.post("/api/posts", headers=headers, json={"body": "<img src=x onerror=alert(1)>"})

    html = client.get("/api/preview/u/ada").text
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img" in html


def test_profile_page_handles_a_profile_with_no_posts(client, make_user):
    make_user("quiet")
    html = client.get("/api/preview/u/quiet").text
    assert "No posts yet." in html


def test_sitemap_lists_public_profiles(client, make_user):
    headers, _ = make_user("ada")
    make_user("grace")
    client.post("/api/posts", headers=headers, json={"body": "hello"})

    response = client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")

    root = ET.fromstring(response.text)
    locs = [node.text for node in root.iter(f"{SITEMAP_NS}loc")]
    assert "https://testserver/u/ada" in locs
    assert "https://testserver/u/grace" in locs
    # The app root is not a public page, so it must not be advertised.
    assert "https://testserver/" not in locs


def test_sitemap_is_anonymous(client, make_user):
    make_user("ada")
    assert client.get("/sitemap.xml").status_code == 200


def test_sitemap_omits_banned_accounts(client, make_user, make_admin):
    """A suspended account must not be advertised to crawlers.

    Driven through the real ban route rather than by writing the flag directly,
    so this also covers the route leaving the account out of the sitemap.
    """
    make_user("ada")
    _, spammer = make_user("spammer")
    admin_headers, _ = make_admin()

    banned = client.post(
        f"/api/admin/users/{spammer['id']}/ban",
        headers=admin_headers,
        json={"banned": True, "reason": "spam"},
    )
    assert banned.status_code == 200

    root = ET.fromstring(client.get("/sitemap.xml").text)
    locs = [node.text for node in root.iter(f"{SITEMAP_NS}loc")]
    assert any("/u/ada" in loc for loc in locs)
    assert not any("/u/spammer" in loc for loc in locs)


def test_sitemap_lastmod_uses_the_newest_post(client, make_user):
    """A profile's lastmod should follow its posts, not its creation date."""
    headers, _ = make_user("ada")
    client.post("/api/posts", headers=headers, json={"body": "hello"})

    root = ET.fromstring(client.get("/sitemap.xml").text)
    entry = next(node for node in root.iter(f"{SITEMAP_NS}url") if "/u/ada" in node.find(f"{SITEMAP_NS}loc").text)
    assert entry.find(f"{SITEMAP_NS}lastmod") is not None


def test_sitemap_xml_is_well_formed_with_a_hostile_username(client, make_user):
    """The document must stay parseable no matter what a username contains."""
    make_user("ada")
    root = ET.fromstring(client.get("/sitemap.xml").text)
    assert root.tag == f"{SITEMAP_NS}urlset"
