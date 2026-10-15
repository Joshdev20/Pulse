# Pulse

A small full-stack social platform: post short updates, follow people, like and reply,
get notified, tag topics with hashtags, and search across posts and users.

- **Backend** — FastAPI + SQLAlchemy 2 + SQLite, JWT auth, PBKDF2 password hashing.
- **Frontend** — React 18 + TypeScript + Vite, hand-written CSS design system, no UI library.

```
project/
├── backend/          FastAPI app, models, routers, seed script, tests
└── frontend/         React + Vite single-page app
```

## Quick start

Two processes: the API on `:8000` and the Vite dev server on `:5173`. Vite proxies
`/api/*` to the backend, so the browser only ever talks to one origin.

### 1. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt

python -m app.seed --reset      # create pulse.db and load demo data
python -m uvicorn app.main:app --reload --port 8000
```

API docs: <http://127.0.0.1:8000/docs>

### 2. Frontend

```bash
cd frontend
npm install
npm run dev                     # http://127.0.0.1:5173
```

### Demo accounts

Every seeded user has the password `password123`. Try `ada`, `grace`, `linus`,
`alan`, `donald`, `katherine`, `barbara`, or `margaret`.

## Tests

```bash
cd backend
python -m pytest -q             # 30 tests
```

```bash
cd frontend
npm run typecheck               # tsc --noEmit
npm run build                   # typecheck + production bundle
```

## Features

| Area | What works |
| --- | --- |
| Accounts | Register, sign in, JWT bearer sessions, edit profile (name, bio, location, website, avatar colour) |
| Posts | Create, edit, delete, hashtag extraction, `#tag` and `@mention` linking |
| Social | Follow / unfollow, followers and following lists, follow suggestions |
| Engagement | Likes (idempotent), threaded replies on a post detail page |
| Notifications | Generated on like, reply and follow; unread count, mark one or all read |
| Discovery | Home timeline (following or everyone), explore with latest / people / topics tabs, full-text search over posts and users, trending hashtags, popular people |
| UI | Responsive dark theme, mobile bottom navigation, optimistic likes, toasts, skeleton loaders, empty and error states |

## Architecture notes

**Backend layering.** `models.py` holds the SQLAlchemy tables, `schemas.py` the Pydantic
request/response shapes, `routers/` the endpoints, and `deps.py` the shared dependencies
(current user, pagination, database session). Counts such as `like_count` and
`follower_count` are computed with grouped queries rather than per-row lookups, so list
endpoints stay a constant number of queries regardless of page size.

**Tag normalisation.** `tagging.py` parses hashtags out of a post body and resolves them
to `Tag` rows, creating any that are missing and linking them through the
`post_tags` association table. Trending tags are ranked by usage.

**Frontend data flow.** `lib/api.ts` is the single typed wrapper around `fetch`. Auth state
lives in `AuthContext` and is restored from a stored token on boot. `usePostFeed` owns
paginated lists and applies edits, deletes and like changes locally so the UI updates
without refetching the page.

## Configuration

Backend settings are read from the environment or `backend/.env` (see `app/config.py`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./pulse.db` | SQLAlchemy connection string |
| `SECRET_KEY` | `dev-secret-key-change-me` | JWT signing key — **set this in production** |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `10080` (7 days) | Token lifetime |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated allowed origins |

Frontend build-time variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `5173` | Vite dev server port |
| `VITE_API_PROXY` | `http://127.0.0.1:8000` | Backend target for the `/api` proxy |

## API reference

All endpoints are under `/api`. Routes marked *auth* require an
`Authorization: Bearer <token>` header.

### Auth

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/auth/register` | Create an account, returns a token |
| `POST` | `/auth/login` | Exchange username/email + password for a token |
| `GET` | `/auth/me` | Current user — *auth* |

### Posts

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/feed?scope=following\|all` | Timeline, paginated |
| `GET` | `/posts` | Public post list, paginated |
| `POST` | `/posts` | Create a post — *auth* |
| `GET` | `/posts/{id}` | Single post |
| `PATCH` | `/posts/{id}` | Edit body — author only |
| `DELETE` | `/posts/{id}` | Delete — author only |
| `POST` / `DELETE` | `/posts/{id}/like` | Like / unlike — *auth*, idempotent |
| `GET` / `POST` | `/posts/{id}/comments` | List / add replies |
| `DELETE` | `/comments/{id}` | Delete a reply — author or post owner |

### Users and social

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/users/{username}` | Public profile |
| `PATCH` | `/users/{username}` | Update own profile — *auth* |
| `GET` | `/users/{username}/posts` | A user's posts |
| `GET` | `/users/{username}/likes` | Posts a user liked |
| `POST` / `DELETE` | `/users/{username}/follow` | Follow / unfollow — *auth* |
| `GET` | `/users/{username}/followers` | Follower list |
| `GET` | `/users/{username}/following` | Following list |
| `GET` | `/users/suggested` | Who to follow |
| `GET` | `/users/stats` | Platform-wide counts |

### Discovery

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/search?q=` | Combined posts + users |
| `GET` | `/search/posts?q=` | Posts only |
| `GET` | `/search/users?q=` | Users only |
| `GET` | `/trending/tags` | Hashtags ranked by usage |
| `GET` | `/trending/people` | Users ranked by followers |

### Notifications

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/notifications` | List for the current user — *auth* |
| `GET` | `/notifications/unread-count` | Badge count — *auth* |
| `POST` | `/notifications/{id}/read` | Mark one read — *auth* |
| `POST` | `/notifications/read-all` | Mark all read — *auth* |

## Production notes

The defaults are tuned for local development. Before deploying:

- Set `SECRET_KEY` to a long random value and serve over HTTPS.
- Point `DATABASE_URL` at PostgreSQL (or another server database) and manage schema
  changes with Alembic instead of `create_all`.
- Set `CORS_ORIGINS` to the real frontend origin.
- Build the frontend with `npm run build` and serve `frontend/dist` from a static host or CDN.
