"""Tests for post translation and the per-post view count.

Translation itself is an external service, so it is stubbed here: the tests
assert the *decisions* this repository makes — when a translate prompt is
offered, how a long post is split, and that a failure degrades to "no toggle"
rather than an error. The transport is the service's job.
"""

import pytest

from app import translate as translate_module
from app.config import settings
from app.translate import (
    _chunks,
    detect_language,
    language_name,
    translate_text,
    translate_with_detection,
)


# --------------------------------------------------------------------------
# Language detection
# --------------------------------------------------------------------------


def test_detects_a_long_foreign_post():
    code, confidence = detect_language("Bonjour tout le monde, comment allez-vous aujourd'hui ?")
    assert code == "fr"
    assert confidence >= 0.75


def test_english_is_detected_but_not_offered_a_translation(client, make_user):
    """A post already in the UI's language gets no language, so no prompt."""
    headers, _ = make_user("ada")
    post = client.post(
        "/api/posts",
        headers=headers,
        json={"body": "This is an English post about the weather and the weekend."},
    ).json()

    assert post["language"] is None
    assert post["language_name"] is None


def test_a_foreign_post_reports_its_language(client, make_user):
    headers, _ = make_user("ada")
    post = client.post(
        "/api/posts",
        headers=headers,
        json={"body": "Bonjour tout le monde, comment allez-vous aujourd'hui ?"},
    ).json()

    assert post["language"] == "fr"
    assert post["language_name"] == "French"


def test_a_short_latin_post_is_not_guessed_at():
    """`ok` and `nice one` are confidently misdetected, so short text is skipped."""
    assert detect_language("ok")[0] is None
    assert detect_language("nice one")[0] is None
    assert detect_language("new post")[0] is None


def test_a_short_post_in_a_distinctive_script_is_still_detected():
    """Script alone is unambiguous, so the length guard does not apply."""
    assert detect_language("こんにちは")[0] == "ja"
    assert detect_language("안녕하세요")[0] == "ko"


def test_a_short_cyrillic_post_is_left_alone():
    """Cyrillic languages are confidently confused for each other when short."""
    assert detect_language("Привет мир как дела")[0] is None


def test_detection_is_stable_across_calls():
    """The library is randomized unless seeded; the same post must not flip."""
    body = "Hola mundo, como estas hoy? Espero que todo este muy bien."
    assert detect_language(body) == detect_language(body)


# The statistical detector has no model for Igbo, Yoruba or Hausa, and does not
# report them as unknown — it returns a confident wrong answer. Igbo in
# particular is read as Vietnamese, which is what made an Igbo post both mislabel
# and translate to nothing. These are recognised by their distinctive letters.


def test_igbo_is_detected_by_its_dotted_vowels():
    body = "ACHỌRỌ M EGO NA-ABATA KWA ỤBỌCHỊ NKE GA-EGBO MKPA M, KWADO EBUMNUCHE M"
    assert detect_language(body) == ("ig", 1.0)
    assert language_name("ig") == "Igbo"


def test_igbo_with_a_nasal_is_still_igbo():
    assert detect_language("Ọ dị mma ịhụ gị ebe a, gịnị ka ị na-eme ugbua?")[0] == "ig"


def test_a_vietnamese_post_is_not_read_as_igbo():
    """Vietnamese shares ị/ọ/ụ, so its own letters have to win."""
    body = "Xin chào bạn, hôm nay bạn có khỏe không? Tôi rất vui được gặp bạn ở đây."
    assert detect_language(body)[0] == "vi"


def test_yoruba_is_detected_by_its_s():
    assert detect_language("Báwo ni, ṣé dáadáa ni? Orúkọ mi ni Adéyẹmí")[0] == "yo"
    assert language_name("yo") == "Yoruba"


def test_hausa_is_detected_by_its_hooked_letters():
    assert detect_language("Sannu, yaya kake? Sunana Musa, ina zaune a Kano, ɓ ɗ ƙ")[0] == "ha"
    assert language_name("ha") == "Hausa"


def test_english_is_untouched_by_the_script_check():
    """The script layer must not claim English; the serializer drops `en` later."""
    assert detect_language("This is a normal English post about the weather and the weekend.")[0] == "en"


def test_language_name_maps_known_codes_and_ignores_unknown():
    assert language_name("fr") == "French"
    assert language_name("zh-cn") == "Chinese"
    assert language_name("xx") is None
    assert language_name(None) is None


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------


def test_a_short_post_is_one_chunk():
    assert _chunks("Bonjour.", 500) == ["Bonjour."]


def test_a_long_post_splits_on_sentence_boundaries():
    text = " ".join(["Ceci est une phrase assez longue."] * 40)
    pieces = _chunks(text, 120)

    assert len(pieces) > 1
    assert all(len(piece) <= 120 for piece in pieces)
    # Nothing is lost in the split.
    assert "".join(piece.replace(" ", "") for piece in pieces) == text.replace(" ", "")


def test_a_single_oversized_sentence_is_still_split():
    pieces = _chunks("x" * 1300, 500)
    assert len(pieces) >= 3
    assert all(len(piece) <= 500 for piece in pieces)


# --------------------------------------------------------------------------
# Translation, with the service stubbed
# --------------------------------------------------------------------------


@pytest.fixture
def stub_service(monkeypatch):
    """Replace the network call with a mapping, and record the chunks asked for.

    The stub returns the shape the real call does: ``(translation, detected)``.
    """
    seen: list[str] = []

    def fake_chunk(client, chunk, source, target):
        seen.append(chunk)
        return (f"[{target}]{chunk}", "fr")

    monkeypatch.setattr(translate_module, "_translate_chunk", fake_chunk)
    return seen


def test_translate_returns_the_service_result(stub_service):
    assert translate_text("Bonjour tout le monde", "fr") == "[en]Bonjour tout le monde"


def test_translate_joins_every_chunk(stub_service):
    text = " ".join(["Ceci est une phrase assez longue."] * 40)
    result = translate_text(text, "fr")

    assert result is not None
    assert result.count("[en]") == len(stub_service)
    assert len(stub_service) > 1


def test_translate_refuses_a_post_already_in_the_target_language(stub_service):
    assert translate_text("Hello there, friend", "en") is None
    assert stub_service == []


def test_translate_is_disabled_by_configuration(stub_service, monkeypatch):
    monkeypatch.setattr(settings, "translate_enabled", False)
    assert translate_text("Bonjour tout le monde", "fr") is None


def test_one_failed_chunk_discards_the_whole_translation(monkeypatch):
    """A half-translated post would be worse than none."""
    calls = {"n": 0}

    def flaky(client, chunk, source, target):
        calls["n"] += 1
        return (None, None) if calls["n"] == 2 else ("ok", "fr")

    monkeypatch.setattr(translate_module, "_translate_chunk", flaky)
    text = " ".join(["Ceci est une phrase assez longue."] * 40)

    assert translate_text(text, "fr") is None


def test_the_request_never_pins_the_local_guess(monkeypatch):
    """A wrong pinned source is what made Igbo come back untranslated.

    The service is always asked to autodetect, so its own answer governs and a
    bad local guess cannot make it echo the text back.
    """
    pairs: list[str] = []

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {
                "responseStatus": 200,
                "responseData": {"translatedText": "hello", "detectedLanguage": "ig"},
            }

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            pairs.append(params["langpair"])
            return FakeResponse()

    monkeypatch.setattr(translate_module.httpx, "Client", lambda **kw: FakeClient())

    translated, detected = translate_with_detection("Ndewo, kedu ka ị mere?", "vi")
    assert translated == "hello"
    assert detected == "ig"
    assert pairs == ["Autodetect|en"]


def test_google_is_preferred_and_its_answer_is_parsed(monkeypatch):
    """The Google endpoint answers first, and its ``[[text, detected]]`` shape parses."""
    calls: list[str] = []

    class FakeResponse:
        def __init__(self, body): self._body = body
        def raise_for_status(self): pass
        def json(self): return self._body

    class FakeClient:
        def get(self, url, params=None, headers=None):
            calls.append(url)
            return FakeResponse([["Hi how are you? My name is Chukwuemeka", "ig"]])

    monkeypatch.setattr(
        translate_module, "_request_translation",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("MyMemory should not be reached")),
    )

    translated, detected = translate_module._translate_chunk(
        FakeClient(), "Ndewo, kedu ka ị mere?", "ig", "en"
    )
    assert translated == "Hi how are you? My name is Chukwuemeka"
    assert detected == "ig"
    assert calls == ["https://clients5.google.com/translate_a/t"]


def test_google_failure_falls_through_to_the_other_provider(monkeypatch):
    """When Google will not answer, the other provider is tried."""
    monkeypatch.setattr(translate_module, "_translate_google", lambda *a, **k: None)
    monkeypatch.setattr(
        translate_module, "_request_translation",
        lambda client, chunk, pair: {
            "responseStatus": 200,
            "responseData": {"translatedText": "hello", "detectedLanguage": "ig"},
        },
    )
    translated, detected = translate_module._translate_chunk(object(), "Ndewo", "ig", "en")
    assert (translated, detected) == ("hello", "ig")


def test_an_igbo_post_translates_through_the_provider_chain(monkeypatch):
    """The end the user sees: Igbo in, English out, labelled Igbo."""
    monkeypatch.setattr(translate_module, "_translate_google", lambda *a, **k: None)
    monkeypatch.setattr(
        translate_module, "_request_translation",
        lambda client, chunk, pair: {
            "responseStatus": 200,
            "responseData": {"translatedText": "I want money that comes daily", "detectedLanguage": "ig"},
        },
    )
    translated, detected = translate_with_detection(
        "ACHỌRỌ M EGO NA-ABATA KWA ỤBỌCHỊ NKE GA-EGBO MKPA M", "ig"
    )
    assert translated == "I want money that comes daily"
    assert detected == "ig"


def test_the_request_falls_back_to_the_named_source_when_autodetect_is_refused(monkeypatch):
    """The free service turns away some callers; the pinned path still works."""
    pairs: list[str] = []

    class FakeResponse:
        def __init__(self, body): self._body = body
        def raise_for_status(self): pass
        def json(self): return self._body

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            pairs.append(params["langpair"])
            if params["langpair"] == "Autodetect|en":
                return FakeResponse({"responseStatus": 403, "responseDetails": "REFUSED"})
            return FakeResponse({
                "responseStatus": 200,
                "responseData": {"translatedText": "I want money that comes daily"},
            })

    monkeypatch.setattr(translate_module.httpx, "Client", lambda **kw: FakeClient())

    translated, detected = translate_with_detection("Ndewo, kedu ka ị mere?", "ig")
    assert pairs == ["Autodetect|en", "ig|en"]
    assert translated == "I want money that comes daily"
    assert detected == "ig"


def test_the_service_detection_is_reported_when_it_knows_the_language(monkeypatch):
    """The label follows the service, which has actually seen the text."""
    monkeypatch.setattr(
        translate_module, "_translate_chunk",
        lambda client, chunk, source, target: ("hello", "ig"),
    )
    translated, detected = translate_with_detection("Ndewo, kedu ka ị mere?", "ig")
    assert translated == "hello"
    assert detected == "ig"


# --------------------------------------------------------------------------
# The translate endpoint
# --------------------------------------------------------------------------


def test_translation_endpoint_returns_the_translation(client, make_user, stub_service):
    headers, _ = make_user("ada")
    post = client.post(
        "/api/posts",
        headers=headers,
        json={"body": "Bonjour tout le monde, comment allez-vous aujourd'hui ?"},
    ).json()

    body = client.get(f"/api/posts/{post['id']}/translation", headers=headers).json()
    assert body["source"] == "fr"
    assert body["source_name"] == "French"
    assert body["translated"]


def test_translation_endpoint_reports_no_translation_for_english(client, make_user, stub_service):
    headers, _ = make_user("ada")
    post = client.post(
        "/api/posts",
        headers=headers,
        json={"body": "This is an English post about the weather and the weekend."},
    ).json()

    body = client.get(f"/api/posts/{post['id']}/translation", headers=headers).json()
    assert body["translated"] is None


def test_translation_endpoint_labels_an_igbo_post_as_igbo(client, make_user, monkeypatch):
    """Igbo was labelled Vietnamese and translated to nothing; it is Igbo now."""
    monkeypatch.setattr(
        translate_module, "_translate_chunk",
        lambda client, chunk, source, target: ("I want money that comes in daily", "ig"),
    )
    headers, _ = make_user("ada")
    post = client.post(
        "/api/posts",
        headers=headers,
        json={"body": "ACHỌRỌ M EGO NA-ABATA KWA ỤBỌCHỊ NKE GA-EGBO MKPA M"},
    ).json()

    assert post["language"] == "ig"
    assert post["language_name"] == "Igbo"

    body = client.get(f"/api/posts/{post['id']}/translation", headers=headers).json()
    assert body["source"] == "ig"
    assert body["source_name"] == "Igbo"
    assert body["translated"] == "I want money that comes in daily"


def test_translation_endpoint_requires_authentication(client, make_user):
    headers, _ = make_user("ada")
    post = client.post("/api/posts", headers=headers, json={"body": "Bonjour tout le monde ici"}).json()
    assert client.get(f"/api/posts/{post['id']}/translation").status_code == 401


def test_translation_endpoint_404s_for_a_missing_post(client, make_user):
    headers, _ = make_user("ada")
    assert client.get("/api/posts/9999/translation", headers=headers).status_code == 404


# --------------------------------------------------------------------------
# View counts
# --------------------------------------------------------------------------


def test_opening_a_post_counts_a_view(client, make_user):
    author, _ = make_user("author")
    reader, _ = make_user("reader")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    body = client.get(f"/api/posts/{post_id}", headers=reader).json()
    assert body["view_count"] == 1


def test_the_same_reader_is_counted_once(client, make_user):
    author, _ = make_user("author")
    reader, _ = make_user("reader")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    for _ in range(3):
        client.get(f"/api/posts/{post_id}", headers=reader)

    assert client.get(f"/api/posts/{post_id}", headers=author).json()["view_count"] == 1


def test_the_author_does_not_count_as_a_viewer(client, make_user):
    """The number is meant to say how many *other* people saw it."""
    author, _ = make_user("author")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    assert client.get(f"/api/posts/{post_id}", headers=author).json()["view_count"] == 0


def test_several_readers_accumulate(client, make_user):
    author, _ = make_user("author")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    for name in ("read1", "read2", "read3"):
        reader, _ = make_user(name)
        client.get(f"/api/posts/{post_id}", headers=reader)

    assert client.get(f"/api/posts/{post_id}", headers=author).json()["view_count"] == 3


def test_a_feed_view_counts_the_posts_it_shows(client, make_user):
    author, _ = make_user("author")
    reader, _ = make_user("reader")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]
    client.post(f"/api/users/author/follow", headers=reader)

    client.get("/api/feed", headers=reader)

    assert client.get(f"/api/posts/{post_id}", headers=author).json()["view_count"] == 1


def test_a_lost_view_race_does_not_fail_the_request(client, make_user):
    """Two tabs opening the same post at once must both get a normal response.

    The row is inserted out of band first, exactly as a second tab would, so the
    endpoint's insert loses the race at commit time. The unique constraint is the
    dedupe; the loser must still be served the post.
    """
    from app.database import get_db
    from app.models import PostView

    author, _ = make_user("author")
    reader, reader_user = make_user("reader")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    session = next(client.app.dependency_overrides[get_db]())
    try:
        session.add(PostView(post_id=post_id, user_id=reader_user["id"]))
        session.commit()
    finally:
        session.close()

    response = client.get(f"/api/posts/{post_id}", headers=reader)

    assert response.status_code == 200
    # Counted once, not twice.
    assert response.json()["view_count"] == 1


def test_reading_a_post_from_a_profile_counts_a_view(client, make_user):
    """The profile grid is where people browse; it must count views too.

    This path serves the same post as the detail endpoint but did not record a
    view, so a post read only from a profile could show fewer views than likes.
    """
    author, _ = make_user("author")
    reader, _ = make_user("reader")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    client.get("/api/users/author/posts", headers=reader)

    assert client.get(f"/api/posts/{post_id}", headers=author).json()["view_count"] == 1


def test_reading_a_post_from_search_counts_a_view(client, make_user):
    author, _ = make_user("author")
    reader, _ = make_user("reader")
    post_id = client.post(
        "/api/posts", headers=author, json={"body": "a uniquely searchable phrase"}
    ).json()["id"]

    client.get("/api/search/posts", headers=reader, params={"q": "uniquely searchable"})

    assert client.get(f"/api/posts/{post_id}", headers=author).json()["view_count"] == 1


def test_liking_a_post_records_a_view(client, make_user):
    """A like proves a read, so it must never leave likes above views.

    The post is served here by the like response itself, with no prior view, so
    the liker is counted as a viewer.
    """
    author, _ = make_user("author")
    liker, _ = make_user("liker")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    liked = client.post(f"/api/posts/{post_id}/like", headers=liker)
    assert liked.status_code == 200
    assert liked.json()["like_count"] == 1
    assert liked.json()["view_count"] == 1


def test_a_post_never_shows_more_likes_than_views(client, make_user):
    author, _ = make_user("author")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    for name in ("liker1", "liker2", "liker3"):
        liker, _ = make_user(name)
        client.post(f"/api/posts/{post_id}/like", headers=liker)

    body = client.get(f"/api/posts/{post_id}", headers=author).json()
    assert body["view_count"] >= body["like_count"]


def test_liking_your_own_post_counts_you_as_a_viewer(client, make_user):
    """Self-likes are allowed, so they must not push likes above views.

    An author can like their own post, and their own reads are normally not
    counted, so that like would otherwise sit above the view count forever.
    """
    author, _ = make_user("author")
    post_id = client.post("/api/posts", headers=author, json={"body": "hello there"}).json()["id"]

    liked = client.post(f"/api/posts/{post_id}/like", headers=author)
    assert liked.status_code == 200
    assert liked.json()["like_count"] == 1
    assert liked.json()["view_count"] == 1
