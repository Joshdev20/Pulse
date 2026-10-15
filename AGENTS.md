# AGENTS.md

Repository-specific context for AI agents working on Pulse.

## What this is

A full-stack social platform. `backend/` is a FastAPI + SQLAlchemy 2 API backed by SQLite.
`frontend/` is a React 18 + TypeScript single-page app built with Vite. There is no
component library — all styling is hand-written CSS in `frontend/src/styles.css`.

## Commands

Backend (run from `backend/`):

```bash
pip install -r requirements.txt
python -m app.seed --reset                  # rebuild pulse.db with demo data
python -m uvicorn app.main:app --reload --port 8000
python -m pytest -q                         # 71 tests
```

Frontend (run from `frontend/`):

```bash
npm install
npm run dev          # port 12000, proxies /api and /api/ws to 127.0.0.1:8000
npm run typecheck    # tsc --noEmit
npm run build        # typecheck + vite build
```

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

## Gotchas

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
- Pushing to GitHub requires the "OpenHands AI" GitHub App (client id `Iv23lizrrwLGm04hszl7`)
  to be installed on the account. The `GITHUB_TOKEN` is a user-to-server token from that app;
  authorizing the app is not enough, it must be *installed* with `Contents: Read and write`.
  Without the installation every write fails with `403 ... denied to <user>` — including repo
  creation — while reads (`git ls-remote`, the API) still succeed. Install or adjust it at
  https://github.com/apps/openhands-ai/installations/new, and verify with `git push --dry-run`
  before assuming a push will work.
