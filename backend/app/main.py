"""Pulse — a small social media API built with FastAPI."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import cache, storage
from .config import settings
from .database import Base, engine
from .middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from .realtime import manager
from .routers import (
    admin,
    auth,
    media,
    messages,
    notifications,
    posts,
    preview,
    push,
    realtime,
    search,
    sitemap,
    users,
)
from .schema import ensure_schema

logger = logging.getLogger("pulse.main")


def _backfill_media_blobs() -> None:
    """Copy on-disk uploads written before the ``blob`` column into the database.

    Runs once per boot. A host that still has its files gets them preserved
    before the next redeploy; one that has already lost them — the free Render
    tier — repairs nothing and must not fail startup.
    """
    from .database import SessionLocal
    from .routers.media import backfill_blobs

    db = SessionLocal()
    try:
        repaired = backfill_blobs(db)
        if repaired:
            print(f"Backfilled {repaired} media file(s) into the database.")
    except Exception as exc:  # noqa: BLE001 - startup must survive a bad row
        print(f"Media blob backfill skipped: {exc}")
    finally:
        db.close()


def _backfill_audio() -> None:
    """Convert voice notes stored in a container some readers cannot decode.

    Runs once per boot. A note recorded on Chrome was stored as WebM/Opus, which
    iOS cannot play at all, so this is what makes the notes already sitting in
    conversations start working rather than only the ones sent from now on.
    Idempotent, so the boots after the first do no work.
    """
    from .database import SessionLocal
    from .routers.media import backfill_audio

    db = SessionLocal()
    try:
        repaired = backfill_audio(db)
        if repaired:
            print(f"Converted {repaired} voice note(s) to a playable format.")
    except Exception as exc:  # noqa: BLE001 - startup must survive a bad row
        print(f"Voice note backfill skipped: {exc}")
    finally:
        db.close()


def _backfill_official_follows() -> None:
    """Make every existing account follow the official account.

    Accounts created before the follow was compulsory would otherwise miss every
    announcement. Idempotent, so it adds nothing on the boots after the first.
    """
    from .database import SessionLocal
    from .official import backfill_official_follows

    db = SessionLocal()
    try:
        added = backfill_official_follows(db)
        if added:
            print(f"Added {added} official-account follow(s).")
    except Exception as exc:  # noqa: BLE001 - startup must survive a bad row
        print(f"Official follow backfill skipped: {exc}")
    finally:
        db.close()


def _backfill_like_views() -> None:
    """Treat a like as proof of a view.

    Liking a post means reading it, but views were not recorded on every path
    that served a post — profiles and search were missed — so a post could end up
    showing more likes than views. Fill in the missing rows once per boot. The
    unique constraint keeps this idempotent, and the NOT EXISTS filter skips a
    liker who already has a view row, so a restart adds nothing.
    """
    from sqlalchemy import select as sa_select

    from .database import SessionLocal
    from .models import Like, PostView

    seen = (
        sa_select(PostView.id)
        .where(PostView.post_id == Like.post_id, PostView.user_id == Like.user_id)
        .correlate(Like)
        .exists()
    )
    db = SessionLocal()
    try:
        pairs = db.execute(sa_select(Like.post_id, Like.user_id).where(~seen)).all()
        if not pairs:
            return
        for post_id, user_id in pairs:
            db.add(PostView(post_id=post_id, user_id=user_id))
        db.commit()
        print(f"Backfilled {len(pairs)} view(s) implied by existing likes.")
    except Exception as exc:  # noqa: BLE001 - startup must survive a bad row
        db.rollback()
        print(f"Like-view backfill skipped: {exc}")
    finally:
        db.close()


def _warn_on_push_misconfiguration() -> None:
    """Report a VAPID subject that Apple's push service will refuse.

    This failure is otherwise invisible: the server starts, Android alerts
    arrive, and only iPhones are silently dropped with a 403 that reads like an
    authentication problem. Saying so at boot turns a support report into a log
    line. Warns rather than raises, because push is a side channel and a
    deployment with the wrong subject is still better off serving the app.
    """
    if settings.push_configured and not settings.push_subject_valid:
        # `logging`, not `print`: stdout is block-buffered when it is not a TTY,
        # which is exactly the case under a process manager, so a printed warning
        # can sit in the buffer and never reach the deployment's logs.
        logger.warning(
            "VAPID_SUBJECT is not deliverable and Apple will reject it "
            "(403 BadJwtToken); iPhone push will not work. Got %r. Use a real "
            "address such as mailto:you@yourdomain.com, or an https:// URL.",
            settings.vapid_subject,
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # create_all makes missing tables but never alters an existing one, so a
    # deploy onto a database created by an earlier version needs the newer
    # columns added here.
    ensure_schema(engine)
    storage.ensure_upload_dir()
    _backfill_media_blobs()
    # After the blob backfill: it is what gives an older row the bytes this
    # conversion needs.
    _backfill_audio()
    _backfill_like_views()
    _warn_on_push_misconfiguration()
    if settings.auto_seed:
        # Demo deploys start with an empty database; populate it once. `seed`
        # is a no-op when users already exist, so restarts are safe.
        from .seed import ensure_admin, seed

        seed()
        # A database seeded before moderators existed keeps is_admin at its
        # default and would have no route into the admin console.
        ensure_admin()
    # After seeding, so demo accounts get the compulsory follow too. Ordering
    # matters: the backfill only covers accounts that already exist.
    _backfill_official_follows()
    # Resolve the cache backend once at startup so the first request is not the
    # one that discovers Redis is unavailable.
    cache.get_store()
    # Join the cross-process realtime bus so a socket held by one worker still
    # receives events published by another.
    await manager.start()
    # Finalise calls whose end frame never arrived, so an abandoned call cannot
    # sit as "Calling…" in a thread that is already open.
    sweeper = asyncio.create_task(_sweep_calls_forever())
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        await manager.stop()


async def _sweep_calls_forever() -> None:
    """Run the stale-call sweeper on a fixed cadence for the life of the app."""
    while True:
        try:
            await realtime.sweep_stale_calls()
        except Exception:  # noqa: BLE001 - the loop must survive a bad sweep
            logger.debug("call sweep raised", exc_info=True)
        await asyncio.sleep(realtime.CALL_SWEEP_INTERVAL_SECONDS)


app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description=(
        "REST and WebSocket API for the Pulse social platform: posts, media, "
        "follows, likes, comments, notifications, search and direct messages."
    ),
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_origin_regex=settings.cors_origin_pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(posts.router)
app.include_router(users.router)
# Unauthenticated by design: a link-preview crawler has no account. See the
# module docstring in routers/preview.py.
app.include_router(preview.router)
app.include_router(sitemap.router)
app.include_router(notifications.router)
app.include_router(search.router)
app.include_router(media.router)
app.include_router(messages.router)
app.include_router(realtime.router)
app.include_router(push.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "cache": cache.backend_name()}
