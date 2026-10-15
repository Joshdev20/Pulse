"""Populate the database with demo users, follows, posts, likes and comments.

Usage:
    python -m app.seed          # add demo data if the database is empty
    python -m app.seed --reset  # drop everything first
"""

import argparse
import random
from datetime import timedelta

from sqlalchemy import func, select

from .database import Base, SessionLocal, engine
from .models import Comment, Follow, Like, Notification, Post, User, utcnow
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
        for username, display_name, email, bio, location, website, color in USERS:
            user = User(
                username=username,
                email=email,
                display_name=display_name,
                bio=bio,
                location=location,
                website=website,
                avatar_color=color,
                password_hash=hash_password(DEMO_PASSWORD),
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

        db.commit()
        print(f"Seeded {len(users)} users, {len(posts)} posts, {len(FOLLOWS)} follows.")
        print(f"Sign in with any username above and the password '{DEMO_PASSWORD}'.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the Pulse database with demo content.")
    parser.add_argument("--reset", action="store_true", help="drop all tables before seeding")
    seed(reset=parser.parse_args().reset)
