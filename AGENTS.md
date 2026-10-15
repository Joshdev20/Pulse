# AGENTS.md

Repository-specific context for AI agents working on Pulse.

## What this is

A full-stack social platform. `backend/` is a FastAPI + SQLAlchemy 2 API backed by SQLite.
`frontend/` is a React 18 + TypeScript single-page app built with Vite. There is no
component library — all styling is hand-written CSS in `frontend/src/styles.css`.

## Commands

### First-time setup in a fresh sandbox

The sandbox is rebuilt from scratch between sessions, so `redis-server`, the venv and
`node_modules` may be missing. `redis-py` lives in the venv, but the *server* is a system
package and has to be reinstalled:

```bash
sudo apt-get update -qq && sudo apt-get install -y redis-server
redis-server --daemonize yes --save '' --appendonly no   # no persistence needed
redis-cli PING                                           # expect PONG
```

Without a running server the app still works: it falls back to an in-process cache and
skips the realtime bus. `/api/health` reports which backend is live, so check it before
assuming Redis is in use.

### Backend (run from `backend/`)

The venv has **no pip**; use `uv`:

```bash
uv pip install -r requirements.txt        # or: uv pip install redis
.venv/bin/python -m app.seed --reset      # rebuild pulse.db with demo data
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
.venv/bin/python -m pytest -q             # 88 tests
```

### Frontend (run from `frontend/`)

```bash
npm install
PORT=12000 npm run dev   # must bind 12000: that is the port the public URL maps to
npm run typecheck        # tsc --noEmit
npm run build            # typecheck + vite build
```

`PORT` defaults to 5173 in `vite.config.ts`, which the sandbox proxy does **not** expose —
the public URL returns 502 until the dev server is bound to 12000.

`npm run build` runs `tsc --noEmit` first, so a type error fails the build. Always run
`npm run typecheck` after touching frontend code.

## Conventions

### Backend

- Layer strictly: `models.py` (tables) → `schemas.py` (Pydantic) → `routers/` (endpoints).
  Shared dependencies such as the current user and pagination live in `deps.py`.
- Response shapes are explicit Pydantic models. Never return ORM objects directly.
- Avoid N+1 queries. Counts on list endpoints (`like_count`, `follower_count`,
  `comment_count`) are computed with grouped aggregate queries and `is_following` /
  `liked_by_me` with a single membership query per page — see `routers/posts.py` and
  `routers/users.py` for the pattern to follow.
- Password hashing and token helpers live in `security.py`. Do not inline crypto.
- Post bodies are limited to 2000 characters (see `schemas.py`).

### Frontend

- `lib/api.ts` is the only place that calls `fetch`. Add new endpoints there as typed
  methods, and add the matching shape to `lib/types.ts`.
- Auth state comes from `AuthContext` (`useAuth()`). Do not read the token from
  `localStorage` directly in components.
- Paginated post lists use `usePostFeed`, and single async values use `useAsync`.
  Both ignore stale responses, so prefer them over hand-rolled `useEffect` fetches.
- Optimistic updates are the norm for likes and follows: update local state first, then
  reconcile with the server response and roll back on error.
- Every list view needs a loading state (`FeedSkeleton`), an empty state (`EmptyState`),
  and an error state (`ErrorState`) from `components/UI.tsx`.
- Style with existing classes in `styles.css` and the CSS custom properties at the top of
  that file. Do not introduce a styling library or inline hex colours.

### Realtime

- One socket per session, owned by `RealtimeContext`. Components never open their own
  WebSocket; they call `useRealtimeEvent(listener)` or `useRealtime()` and receive events.
- The auth token travels in the first frame after `open`, never the query string, so it
  stays out of access logs. Keep it that way.
- Message fan-out is per recipient: each participant gets their own serialized copy
  because `Message.mine` is relative to the reader. Do not broadcast one shared payload.
- `usePageTitle(pathname, suffix?)` keeps `document.title` in step with the route. Login and
  register set their own titles while mounted; signed-in pages go through the hook.
- The realtime socket must **not** hold a database session for its lifetime. `/api/ws` takes
  `Depends(get_db)`, so `routers/realtime.py` closes that session as soon as the handshake
  finishes and closes it again after each typing lookup. A socket that keeps a session open
  pins a pooled connection for as long as the browser tab lives; ~14 idle sockets exhaust the
  default pool (5 + 10 overflow) and every HTTP request then blocks on `pool_timeout` (~30s).
  That is what made the whole app hang on its loading spinner, so the page title stayed at
  the base `Pulse`. `tests/test_realtime_pool.py` guards this with a deliberately tiny pool.

## Gotchas

- `AuthProvider` is mounted once, in `main.tsx`, above the router. Do not mount it again in
  `App.tsx`: a second provider runs a second, unused `api.me()` on every load and doubles
  every auth request.
- A failed session check must not end the session. `AuthContext.refresh` only clears the
  token on a `401`; rate limits (429), dropped connections and 5xx are retried with backoff.
  Treating every error as "signed out" logged users out whenever they tripped the rate
  limiter.
- `RateLimitMiddleware` keeps counters in process-wide state, so tests disable it in
  `conftest.py`. If you add a test that asserts a 429, re-enable it for that test only.
- Uploaded files land in `backend/uploads/` (gitignored) under random names; the database
  stores only the generated filename. `storage.resolve` re-checks containment, so never
  build an upload path by hand.
- Vite HMR replaces a changed module but does not re-run effects whose dependencies are
  unchanged, so a new `useEffect` may not fire until a full page reload. Hard-reload when
  verifying hook changes in the browser.
- `allowedHosts: true` is set in `vite.config.ts` so the dev server accepts the generated
  sandbox hostnames. Removing it makes the app return "Blocked request" when accessed
  through a proxy hostname.
- `UserPublic` deliberately omits `email`, and every user-facing endpoint (including
  `/auth/me`) returns `UserPublic`. The email is only ever accepted on register/login, so
  no component may reference `user.email`.
- Every read endpoint requires a bearer token, including `/api/media/{id}`. A bare
  `<img src>`/`<video src>` cannot send an `Authorization` header, so media must go through
  `useMediaUrl` (`src/lib/useMediaUrl.ts`), which fetches the bytes with auth and returns a
  blob URL. It reference-counts per path, so several elements showing one attachment share a
  single download. Never point an element at `media.url` directly.
- SQLite writes can hit `UNIQUE` constraint errors if a flush is skipped mid-transaction;
  `tagging.py` flushes new `Tag` rows before linking them for this reason. Keep that flush.
- The database file is `backend/pulse.db` and is gitignored. `python -m app.seed --reset`
  drops and rebuilds it.
- `app/cache.py` caches JSON responses for hot read endpoints. Invalidation is by
  *namespace version*, not key deletion: `cache.bump(...)` increments `pulse:v:<ns>`, which
  is folded into every cache key, so old entries become unreachable and expire on their own.
  A write must bump every namespace whose reads it can change — a new post touches
  `posts`, `feed`, `trending`, `search`, `comments` and `users` (post counts), which is why
  `POST_WRITE_NAMESPACES` in `routers/posts.py` is a list rather than a single name.
- The cache store is process-global and resolved once. `conftest.py` clears `redis_url` and
  calls `reset_store()` around every test, so a Redis server running locally cannot leak a
  cached response from one test into the next. Keep that fixture autouse.
- `redis_url` defaults to `redis://localhost:6379/0`, so the app uses Redis when a server is
  present and silently falls back to an in-process store when it is not. `/api/health`
  reports which backend is live — check it before assuming Redis is actually in use.
- `app/realtime.py` fans out over local sockets *and* a Redis pub/sub channel (`pulse:ws`)
  so a socket held by one worker receives events published by another. Bus envelopes carry
  `PROCESS_ID`; a process ignores its own announcement because `publish` already delivered
  locally. Do not deliver bus messages without that origin check, or every event is sent twice.
- Timestamps are serialized through `UTCDatetime` in `schemas.py`, which forces an explicit
  UTC offset. SQLite drops `tzinfo`, so a naive datetime would otherwise be read as local
  time by the browser and make a fresh post look an hour old. Any new timestamp field must
  use `UTCDatetime`, not bare `datetime`.
- The frontend renders relative times with `RelativeTime` (`components/UI.tsx`), driven by
  the shared `useNow` clock. Use it instead of calling `timeAgo` directly so timestamps keep
  ticking and stay consistent with each other.
- Pushing to GitHub requires the "OpenHands AI" GitHub App (client id `Iv23lizrrwLGm04hszl7`)
  to be installed on the account. The `GITHUB_TOKEN` is a user-to-server token from that app;
  authorizing the app is not enough, it must be *installed* with `Contents: Read and write`.
  Without the installation every write fails with `403 ... denied to <user>` — including repo
  creation — while reads (`git ls-remote`, the API) still succeed. Install or adjust it at
  https://github.com/apps/openhands-ai/installations/new, and verify with `git push --dry-run`
  before assuming a push will work.
