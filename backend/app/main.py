"""Pulse — a small social media API built with FastAPI."""

import logging
from contextlib import asynccontextmanager

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
    _warn_on_push_misconfiguration()
    if settings.auto_seed:
        # Demo deploys start with an empty database; populate it once. `seed`
        # is a no-op when users already exist, so restarts are safe.
        from .seed import ensure_admin, seed

        seed()
        # A database seeded before moderators existed keeps is_admin at its
        # default and would have no route into the admin console.
        ensure_admin()
    # Resolve the cache backend once at startup so the first request is not the
    # one that discovers Redis is unavailable.
    cache.get_store()
    # Join the cross-process realtime bus so a socket held by one worker still
    # receives events published by another.
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


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
