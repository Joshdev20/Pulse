"""The relevance ordering behind the home feed.

The feed is a weighted random draw, so these tests exercise the weight itself
rather than one particular arrangement: the draw is only as good as the numbers
it is handed.
"""

from datetime import timedelta

import pytest

from app.models import Post, Tag, utcnow
from app.routers.posts import _feed_weight, _spread_authors


def _post(post_id: int, *, author_id: int = 1, age_hours: float = 1.0, tags: list[str] | None = None) -> Post:
    post = Post(id=post_id, author_id=author_id, body="body")
    post.created_at = utcnow() - timedelta(hours=age_hours)
    post.tags = [Tag(name=name) for name in (tags or [])]
    return post


def _weight(
    post: Post,
    *,
    likes: int = 0,
    comments: int = 0,
    reposts: int = 0,
    views: int = 0,
    followed: set[int] | None = None,
    liked_authors: set[int] | None = None,
    liked_tags: set[str] | None = None,
) -> float:
    return _feed_weight(
        post,
        like_counts={post.id: likes},
        comment_counts={post.id: comments},
        repost_counts={post.id: reposts},
        view_counts={post.id: views},
        followed=followed or set(),
        liked_authors=liked_authors or set(),
        liked_tags=liked_tags or set(),
        now=utcnow(),
    )


def test_freshness_outranks_a_settled_post():
    fresh = _weight(_post(1, age_hours=0.5), likes=2, views=40)
    settled = _weight(_post(2, age_hours=30), likes=2, views=40)
    assert fresh > settled


def test_more_engagement_scores_higher_at_the_same_age():
    quiet = _weight(_post(1), likes=1, views=100)
    busy = _weight(_post(2), likes=12, comments=6, reposts=3, views=100)
    assert busy > quiet


def test_conversion_beats_raw_reach():
    """Few views and many reactions is the post that is going around."""
    going_around = _weight(_post(1), likes=8, comments=4, views=14)
    merely_seen = _weight(_post(2), likes=8, comments=4, views=900)
    assert going_around > merely_seen


def test_a_repost_is_ranked_by_the_content_it_shares():
    original = _post(1, age_hours=2)
    wrapper = Post(id=9, author_id=2, body="")
    wrapper.created_at = utcnow() - timedelta(hours=0.2)
    wrapper.repost_of = original
    wrapper.tags = []

    # Counts are keyed by the original, which is how the feed counts them.
    wrapper_weight = _feed_weight(
        wrapper,
        like_counts={1: 20},
        comment_counts={1: 5},
        repost_counts={1: 2},
        view_counts={1: 60},
        followed=set(),
        liked_authors=set(),
        liked_tags=set(),
        now=utcnow(),
    )
    bare = _weight(_post(2, age_hours=0.2))
    assert wrapper_weight > bare


def test_a_followed_author_is_boosted():
    plain = _weight(_post(1, author_id=7))
    followed = _weight(_post(2, author_id=7), followed={7})
    assert followed > plain


def test_an_author_liked_before_is_boosted_less_than_a_followed_one():
    plain = _weight(_post(1, author_id=7))
    liked = _weight(_post(2, author_id=7), liked_authors={7})
    followed = _weight(_post(3, author_id=7), followed={7})
    assert plain < liked < followed


def test_a_matching_tag_is_boosted():
    plain = _weight(_post(1, tags=["design"]))
    matched = _weight(_post(2, tags=["design"]), liked_tags={"design"})
    other = _weight(_post(3, tags=["football"]), liked_tags={"design"})
    assert matched > plain
    # A tag the reader does not engage with changes nothing.
    assert other == pytest.approx(plain)


def test_affinity_does_not_rescue_a_stale_post():
    """A follow tips a close call; it must not resurrect last month's post."""
    old_followed = _weight(_post(1, author_id=7, age_hours=200), followed={7})
    new_stranger = _weight(_post(2, author_id=8, age_hours=1))
    assert new_stranger > old_followed


def test_one_author_cannot_own_a_stretch_of_the_feed():
    ordered = [_post(index, author_id=1 if index < 4 else index) for index in range(1, 8)]
    spread = _spread_authors(ordered)
    assert len(spread) == len(ordered)
    assert {post.id for post in spread} == {post.id for post in ordered}
    for first, second, third in zip(spread, spread[1:], spread[2:]):
        assert not (first.author_id == second.author_id == third.author_id)


def test_spreading_keeps_the_weighted_order_otherwise():
    ordered = [_post(1, author_id=1), _post(2, author_id=2), _post(3, author_id=3)]
    assert [post.id for post in _spread_authors(ordered)] == [1, 2, 3]


def test_feed_pages_do_not_repeat_a_post(client, make_user):
    headers, _ = make_user("ada")
    for index in range(25):
        client.post("/api/posts", headers=headers, json={"body": f"post {index}"})

    first = client.get("/api/feed?scope=all&page=1&page_size=10&seed=abc", headers=headers).json()
    second = client.get("/api/feed?scope=all&page=2&page_size=10&seed=abc", headers=headers).json()
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 20
