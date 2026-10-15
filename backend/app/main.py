"""Pulse — a small social media API built with FastAPI."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import cache, storage
from .config import settings
from .database import Base, engine
from .middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from .realtime import manager
from .routers import admin, auth, media, messages, notifications, posts, realtime, search, users
from .schema import ensure_schema


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # create_all makes missing tables but never alters an existing one, so a
    # deploy onto a database created by an earlier version needs the newer
    # columns added here.
    ensure_schema(engine)
    storage.ensure_upload_dir()
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
app.include_router(notifications.router)
app.include_router(search.router)
app.include_router(media.router)
app.include_router(messages.router)
app.include_router(realtime.router)
app.include_router(admin.router)


@app.get("/api/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "cache": cache.backend_name()}
