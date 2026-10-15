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
.venv/bin/python -m pytest -q             # 142 tests
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
- The app is split for deployment: `frontend/` on Vercel, `backend/` on a long-lived
  container host (Render/Railway/Fly). See `docs/DEPLOY.md`. Do not put the backend on
  Vercel: SQLite and uploads live on local disk, the rate limiter counts in process memory,
  WebSockets are capped at 300s with no cross-instance fan-out, and the request body cap is
  4.5 MB while uploads allow 32 MB. The Redis bus coordinates worker *processes*, not
  unrelated serverless instances, so moving the backend there drops DMs and notifications.
- The frontend talks to the backend through `VITE_API_BASE` (see `frontend/.env.example`).
  Unset means same-origin, which is what the Vite dev proxy and a single-container deploy
  need; set means absolute URLs to the API host. It is inlined by Vite at **build** time, so
  changing it needs a rebuild. All HTTP goes through `apiUrl()` in `src/lib/api.ts` and the
  socket derives its URL from the same variable in `RealtimeContext.tsx` — use those helpers
  rather than hardcoding a host or path.
- `CORS_ORIGIN_REGEX` (default `https://[a-zA-Z0-9-]+(\.vercel\.app)$`) is matched in
  addition to `CORS_ORIGINS`, so preview deployments work without listing each hostname.
  It is anchored, so `evil-vercel.app.attacker.com` is rejected — keep it anchored.
- `AUTO_SEED=true` seeds demo content on first boot when the database is empty (`seed()`
  returns early if users exist, so restarts are safe). Demo deploys only.
- `database.py` normalizes `postgres://` and bare `postgresql://` URLs to
  `postgresql+psycopg://`. Hosts hand out the first, SQLAlchemy 2 dropped it, and the second
  selects psycopg2 rather than the psycopg 3 driver this project installs. It also sets
  `pool_pre_ping` and `pool_recycle=280` for Postgres, because a pooler closes idle
  connections and a stale one surfaces as a confusing error on the next request.
- `manager.start()` pings Redis before starting the bus listener and leaves `_bus_active`
  False when it is unreachable. Without that check a configured-but-absent Redis (the
  production default is `localhost:6379`) started a listener that failed once a second and
  logged a full traceback each time — 88 errors in 45 seconds in a container test. The
  listener also backs off exponentially to a 30s ceiling. Keep both: a single-process hub is
  the correct behaviour when the bus is optional and missing.
- Uploads are written to `UPLOAD_DIR`. On a host with an ephemeral filesystem this must
  point at a mounted volume (e.g. `/data/uploads`) or uploaded media is lost on redeploy.
  Note that Render free web services cannot attach a disk at all, so `render.yaml` omits the
  `disk:` block and leaves `UPLOAD_DIR` unset; combining `plan: free` with a `disk:` block
  makes the Blueprint unprovisionable even though it passes Render's JSON schema.
- `requirements-prod.txt` is for deployed images and excludes tests; `requirements-dev.txt`
  extends `requirements.txt` with pytest. pip does not parse docstrings, so comments in
  these files must start with `#` — a `"""..."""` header breaks the build.
- `is_verified` is granted only through `POST /admin/users/{id}/verify`. It is deliberately
  absent from the profile-update schema, so `PATCH /users/{username}` with `is_verified`
  silently ignores it rather than letting an account self-verify. `tests/test_profile_features.py`
  pins that.
- A display name carries the verified seal via `DisplayName` in `components/Avatar.tsx`.
  `.display-name` is never `inline-flex`: a container that truncates with an ellipsis clips
  an atomic `inline-flex` box whole — badge and all — instead of trimming the name. The seal
  is a sibling `.verified-badge` span, and `.page-head-title` opts into `block` + ellipsis
  only because it is not itself the truncating container.
- Avatars resolve through `avatar_url_for` in `deps.py` to `/api/media/{id}`, the same
  authenticated route as post media, so the URL is useless without a token and every
  serializer that carries a user must go through it rather than reading `avatar_media_id`.
- The password-reset link is only returned in the response body when `may_return_reset_token`
  is true, i.e. no SMTP host is configured *and* `EXPOSE_RESET_TOKEN` is false. That is what
  makes the demo flow walkable; set `EXPOSE_RESET_TOKEN=true` in production so a live reset
  link is never handed back to the caller. `POST /auth/forgot-password` answers identically
  for a known and an unknown address, so it must not branch on whether the account exists.
- `vercel.json`'s SPA rewrite must stay scoped to `/((?!api/).*)`. A plain `/(.*)`
  catch-all also swallows `/api/*`, and because that happens before the build's
  API base is consulted, the deployed site renders fine while every auth call
  receives `index.html`. `vite.config.ts` also fails a `VERCEL=1` build when
  `VITE_API_BASE` is empty, so a misconfigured bundle cannot ship silently;
  `VITE_SAME_ORIGIN=1` opts out for a genuine single-origin deploy. `api.ts`
  raises a descriptive error when a response comes back as `text/html`.
