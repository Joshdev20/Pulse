"""Populate the database with demo users, follows, posts, likes and comments.

Usage:
    python -m app.seed          # add demo data if the database is empty
    python -m app.seed --reset  # drop everything first
"""

import argparse
import random
from datetime import timedelta

from sqlalchemy import func, select

from . import storage
from .database import Base, SessionLocal, engine
from .models import (
    Comment,
    Conversation,
    ConversationParticipant,
    Follow,
    Like,
    Media,
    Message,
    Notification,
    Post,
    User,
    utcnow,
)
from .security import hash_password
from .tagging import extract_tag_names, resolve_tags

DEMO_PASSWORD = "password123"

USERS = [
    ("ada", "Ada Lovelace", "ada@example.com", "First programmer. Analytical engines, poetry and punch cards.", "London", "https://ada.dev", "#6366f1"),
    ("grace", "Grace Hopper", "grace@example.com", "Compilers, COBOL and the occasional nanosecond.", "Arlington", "https://grace.navy", "#ec4899"),
    ("linus", "Linus Torvalds", "linus@example.com", "Talk is cheap. Show me the code.", "Portland", "", "#f59e0b"),
    ("margaret", "Margaret Hamilton", "margaret@example.com", "Led the flight software team for Apollo. Software engineering, coined.", "Cambridge", "", "#10b981"),
    ("alan", "Alan Turing", "alan@example.com", "Machines, morphogenesis and marathon times.", "Wilmslow", "", "#3b82f6"),
    ("katherine", "Katherine Johnson", "katherine@example.com", "Orbital mechanics by hand. Precision matters.", "Hampton", "", "#8b5cf6"),
    ("barbara", "Barbara Liskov", "barbara@example.com", "Substitution principle enthusiast. Building reliable systems.", "Cambridge", "", "#ef4444"),
    ("donald", "Donald Knuth", "donald@example.com", "Typesetting, algorithms and literate programming.", "Stanford", "https://cs.stanford.edu", "#14b8a6"),
]

POSTS = [
    ("ada", "Just finished sketching an algorithm for computing Bernoulli numbers on the Analytical Engine. Notes and diagrams in the thread. #math #history", 42),
    ("grace", "The most dangerous phrase in the language is, 'We've always done it this way.' #engineering #leadership", 55),
    ("linus", "Rewrote a hot path today. 12% faster, 40 lines shorter. Deleting code is the best kind of refactor. #code #performance", 38),
    ("margaret", "Shipping software for a spacecraft means every edge case matters. Priority displays saved the Apollo 11 landing. #apollo #software", 61),
    ("alan", "Working through a question about whether machines can think. It's subtler than it looks. #ai #philosophy", 47),
    ("katherine", "Checked the numbers twice before the launch. Verify your work, then verify it again. #space #math", 33),
    ("barbara", "A good abstraction lets you reason about the whole system without reading every line. That is the goal. #design #types", 29),
    ("donald", "Picked up a bit of TeX today. Good typography is invisible until it is wrong. #tex #writing", 24),
    ("ada", "Poetry and mathematics are both about describing structures that do not exist yet. #math #creativity", 51),
    ("grace", "Wrote a compiler for a language people said would never be readable. Readability is a choice. #compilers #cobol", 44),
    ("linus", "Code review is not about finding fault, it is about finding the bug before the user does. #code #review", 19),
    ("katherine", "Spacecraft trajectories are just very long arithmetic problems with high stakes. #space #engineering", 27),
]

FOLLOWS = [
    ("grace", "ada"), ("ada", "grace"), ("linus", "ada"), ("linus", "grace"),
    ("margaret", "ada"), ("margaret", "grace"), ("alan", "ada"), ("alan", "margaret"),
    ("katherine", "margaret"), ("katherine", "alan"), ("barbara", "grace"),
    ("barbara", "alan"), ("donald", "ada"), ("donald", "grace"), ("donald", "barbara"),
    ("ada", "margaret"), ("grace", "linus"), ("margaret", "barbara"),
]

COMMENTS = [
    ("grace", "This is brilliant.", 0),
    ("linus", "Nice. Any benchmarks to share?", 2),
    ("margaret", "The diagrams make it click.", 0),
    ("alan", "I keep coming back to this.", 4),
    ("barbara", "Good abstraction. I would ship it.", 6),
    ("katherine", "Double-checked and it holds up.", 5),
    ("donald", "Elegant. Worthy of a footnote.", 8),
    ("ada", "Exactly the framing I was looking for.", 1),
]

# Direct message threads: (user_a, user_b, [(sender, body), ...])
CONVERSATIONS = [
    (
        "ada",
        "grace",
        [
            ("ada", "Do you still have the notes from the compiler talk?"),
            ("grace", "I do. I'll send the slides over this evening."),
            ("ada", "Perfect, thank you."),
            ("grace", "Sent. Let me know if the appendix makes sense."),
        ],
    ),
    (
        "linus",
        "margaret",
        [
            ("margaret", "Did you get a chance to look at the priority display patch?"),
            ("linus", "Reading it now. The error path is much clearer."),
        ],
    ),
    (
        "alan",
        "katherine",
        [
            ("katherine", "Your morphogenesis paper keeps coming up in my reading group."),
            ("alan", "Glad it is still useful. Which section?"),
            ("katherine", "The reaction-diffusion part. We are trying to reproduce it."),
        ],
    ),
]


def _demo_png(color: tuple[int, int, int]) -> bytes:
    """A tiny valid PNG, built by hand so the seed has no image dependency."""
    import struct
    import zlib

    width = height = 8
    raw = b""
    for _ in range(height):
        raw += b"\x00"  # filter byte: none
        raw += bytes(color) * width

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _seed_media(db, users: dict[str, User], posts: list[Post]) -> int:
    """Attach a generated image to a couple of posts so media renders in the feed."""
    plan = [
        ("margaret", 3, "Apollo guidance computer, mission display mock-up", (16, 185, 129)),
        ("ada", 8, "Sketch of the Analytical Engine operation cards", (99, 102, 241)),
    ]
    created = 0
    for username, post_index, alt, color in plan:
        if post_index >= len(posts):
            continue
        data = _demo_png(color)
        stored_name = storage.store(data, "png")
        media = Media(
            owner_id=users[username].id,
            stored_name=stored_name,
            content_type="image/png",
            kind="image",
            size_bytes=len(data),
            width=8,
            height=8,
            alt_text=alt,
            post_id=posts[post_index].id,
        )
        db.add(media)
        created += 1
    return created


def _seed_conversations(db, users: dict[str, User], now) -> int:
    created = 0
    for first, second, messages in CONVERSATIONS:
        left, right = users[first], users[second]
        last_sender = users[messages[-1][0]]

        conversation = Conversation(created_at=now - timedelta(days=1), last_message_at=now)
        db.add(conversation)
        db.flush()

        # Whoever did not send the last message starts with the thread unread,
        # so the unread badge is visible on first sign-in.
        db.add_all(
            [
                ConversationParticipant(
                    conversation_id=conversation.id,
                    user_id=member.id,
                    last_read_at=now if member.id == last_sender.id else None,
                )
                for member in (left, right)
            ]
        )

        offset = len(messages)
        for index, (sender, body) in enumerate(messages):
            db.add(
                Message(
                    conversation_id=conversation.id,
                    sender_id=users[sender].id,
                    body=body,
                    created_at=now - timedelta(minutes=offset - index),
                )
            )
        created += 1
    return created


def seed(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.scalar(select(func.count()).select_from(User)):
            print("Database already contains data. Use --reset to rebuild it.")
            return

        random.seed(7)
        users: dict[str, User] = {}
        for index, (username, display_name, email, bio, location, website, color) in enumerate(USERS):
            user = User(
                username=username,
                email=email,
                display_name=display_name,
                bio=bio,
                location=location,
                website=website,
                avatar_color=color,
                password_hash=hash_password(DEMO_PASSWORD),
                # Without one administrator the moderation endpoints are
                # unreachable, so the first demo account can moderate.
                is_admin=index == 0,
            )
            db.add(user)
            users[username] = user
        db.flush()

        for follower, following in FOLLOWS:
            db.add(Follow(follower_id=users[follower].id, following_id=users[following].id))

        now = utcnow()
        posts: list[Post] = []
        for index, (username, body, hours_ago) in enumerate(POSTS):
            post = Post(
                author_id=users[username].id,
                body=body,
                created_at=now - timedelta(hours=hours_ago),
            )
            post.tags = resolve_tags(db, extract_tag_names(body))
            db.add(post)
            posts.append(post)
        db.flush()

        for index, post in enumerate(posts):
            others = [u for name, u in users.items() if u.id != post.author_id]
            for liker in random.sample(others, random.randint(1, len(others))):
                db.add(Like(post_id=post.id, user_id=liker.id))
                if post.author_id != liker.id:
                    db.add(
                        Notification(
                            user_id=post.author_id,
                            actor_id=liker.id,
                            kind="like",
                            post_id=post.id,
                            created_at=post.created_at + timedelta(minutes=random.randint(1, 120)),
                        )
                    )

        for username, body, post_index in COMMENTS:
            post = posts[post_index]
            db.add(
                Comment(
                    post_id=post.id,
                    author_id=users[username].id,
                    body=body,
                    created_at=post.created_at + timedelta(minutes=random.randint(1, 240)),
                )
            )
            if post.author_id != users[username].id:
                db.add(
                    Notification(
                        user_id=post.author_id,
                        actor_id=users[username].id,
                        kind="comment",
                        post_id=post.id,
                    )
                )

        for follower, following in FOLLOWS:
            db.add(
                Notification(
                    user_id=users[following].id,
                    actor_id=users[follower].id,
                    kind="follow",
                    read=True,
                )
            )

        media_count = _seed_media(db, users, posts)
        conversation_count = _seed_conversations(db, users, now)

        db.commit()
        print(f"Seeded {len(users)} users, {len(posts)} posts, {len(FOLLOWS)} follows.")
        print(f"Seeded {media_count} attachments and {conversation_count} conversations.")
        print(f"Sign in with any username above and the password '{DEMO_PASSWORD}'.")
    finally:
        db.close()


def ensure_admin() -> str | None:
    """Promote the oldest account when the platform has no moderator at all.

    The demo seed only runs against an empty database, so a deployment whose
    users predate the ``is_admin`` column keeps the column's ``false`` default
    and cannot reach the admin endpoints. The API refuses to remove the last
    administrator, so reaching zero this way means the column was just added;
    restoring one keeps the console reachable. Returns the promoted username.
    """
    db = SessionLocal()
    try:
        has_admin = db.scalar(
            select(func.count()).select_from(User).where(User.is_admin.is_(True))
        )
        if has_admin:
            return None
        first = db.scalar(select(User).order_by(User.id).limit(1))
        if first is None:
            return None
        first.is_admin = True
        db.commit()
        print(f"No administrator found; '{first.username}' can now moderate.")
        return first.username
    finally:
        db.close()


def grant_admin(username: str) -> bool:
    """Give an existing account moderator rights.

    Needed because the demo seeding path only runs on an empty database, and a
    deployment that already has users has no other way to reach the admin
    endpoints.
    """
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if user is None:
            print(f"No user named '{username}'.")
            return False
        user.is_admin = True
        db.commit()
        print(f"'{user.username}' can now moderate.")
        return True
    finally:
        db.close()


def revoke_admin(username: str) -> bool:
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
        if user is None:
            print(f"No user named '{username}'.")
            return False
        remaining = db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.is_admin.is_(True), User.id != user.id)
        )
        if not remaining:
            print("Refusing: this is the only administrator.")
            return False
        user.is_admin = False
        db.commit()
        print(f"'{user.username}' is no longer an administrator.")
        return True
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the Pulse database with demo content.")
    parser.add_argument("--reset", action="store_true", help="drop all tables before seeding")
    parser.add_argument("--grant-admin", metavar="USERNAME", help="give an account moderator rights")
    parser.add_argument("--revoke-admin", metavar="USERNAME", help="remove moderator rights")
    args = parser.parse_args()

    if args.grant_admin:
        raise SystemExit(0 if grant_admin(args.grant_admin) else 1)
    if args.revoke_admin:
        raise SystemExit(0 if revoke_admin(args.revoke_admin) else 1)
    seed(reset=args.reset)
