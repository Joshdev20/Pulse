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
python -m pytest -q                         # 30 tests
```

Frontend (run from `frontend/`):

```bash
npm install
npm run dev          # port 5173, proxies /api to 127.0.0.1:8000
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

## Gotchas

- `allowedHosts: true` is set in `vite.config.ts` so the dev server accepts the generated
  sandbox hostnames. Removing it makes the app return "Blocked request" when accessed
  through a proxy hostname.
- `UserPublic` deliberately omits `email`, and every user-facing endpoint (including
  `/auth/me`) returns `UserPublic`. The email is only ever accepted on register/login, so
  no component may reference `user.email`.
- SQLite writes can hit `UNIQUE` constraint errors if a flush is skipped mid-transaction;
  `tagging.py` flushes new `Tag` rows before linking them for this reason. Keep that flush.
- The database file is `backend/pulse.db` and is gitignored. `python -m app.seed --reset`
  drops and rebuilds it.
- Pushing to GitHub fails with `403 ... denied to <user>` in this sandbox. `GITHUB_TOKEN` is a
  user-to-server token from the "OpenHands AI" GitHub App (client id `Iv23lizrrwLGm04hszl7`),
  which declares `contents: write` but has `total_count: 0` installations, so it is read-only
  for git and the API. Granting write requires installing the app at
  https://github.com/apps/openhands-ai/installations/new and selecting the target repo.
  Verify with `git push --dry-run` before assuming a push will work.
