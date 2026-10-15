# Pulse

A small full-stack social platform: post short updates with photos, follow people, like
and reply, get notified, tag topics with hashtags, direct-message other accounts, and
search across posts and users.

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
python -m pytest -q             # 178 tests
```

```bash
cd frontend
npm run typecheck               # tsc --noEmit
npm run build                   # typecheck + production bundle
```

## Features

| Area | What works |
| --- | --- |
| Accounts | Register, sign in, JWT bearer sessions, edit profile (name, bio, location, website, avatar colour), upload or clear a profile picture and a profile cover photo |
| Password reset | Request a link by email, choose a new password, single-use token with a 30-minute expiry |
| Posts | Create, edit, delete, hashtag extraction, `#tag` and `@mention` linking |
| Social | Follow / unfollow, followers and following lists, follow suggestions |
| Engagement | Likes (idempotent), threaded replies on a post detail page, repost / unrepost with a repost envelope in the timeline |
| Notifications | Generated on like, reply, follow and repost; unread count, mark one or all read |
| Media | Image and video attachments on posts, authenticated delivery through `/api/media/{id}` |
| Messages | One-to-one conversations with read receipts and typing indicators over a websocket |
| Moderation | Admin console: analytics, user search and status filters, ban/unban, promote/demote, verified badges, content queues with delete |
| Discovery | Home timeline (following or everyone), explore with latest / people / topics tabs, full-text search over posts and users, trending hashtags, popular people |
| UI | Responsive light/dark theme, mobile bottom navigation, optimistic likes, toasts, skeleton loaders, empty and error states, verified seals |

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
| `FRONTEND_BASE_URL` | `http://localhost:12000` | Origin used to build the password-reset link |
| `PASSWORD_RESET_TTL_MINUTES` | `30` | How long a reset token stays valid |
| `SMTP_HOST` | *(unset)* | Mail relay. Unset means reset links are logged, not sent |
| `SMTP_PORT` | `587` | Mail relay port |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(unset)* | Mail relay credentials |
| `SMTP_FROM` | `no-reply@pulse.local` | Envelope sender |
| `SMTP_STARTTLS` | `true` | Upgrade the SMTP connection with STARTTLS |
| `EXPOSE_RESET_TOKEN` | `false` | Return the reset link in the response. Set in production so a link is never handed back |

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
| `POST` | `/auth/forgot-password` | Email a reset link. Always answers the same way, so it cannot be used to probe for accounts |
| `POST` | `/auth/reset-password` | Exchange a reset token for a new password; returns a token so the user lands signed in |

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
| `POST` / `DELETE` | `/posts/{id}/repost` | Repost / undo a repost — *auth*. Returns the original post |
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
| `POST` / `DELETE` | `/users/me/avatar` | Upload or clear the caller's profile picture — *auth* |
| `POST` / `DELETE` | `/users/me/cover` | Upload or clear the caller's profile cover photo — *auth* |

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

### Media

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/media` | Upload an image or video — *auth* |
| `GET` | `/media/{id}` | Fetch the bytes — *auth* |

### Messages

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/conversations` | The caller's conversations — *auth* |
| `POST` | `/conversations` | Start or reopen a conversation — *auth* |
| `GET` | `/conversations/{id}/messages` | Messages in a conversation — *auth* |
| `POST` | `/conversations/{id}/messages` | Send a message — *auth* |
| `POST` | `/conversations/{id}/read` | Mark the conversation read — *auth* |
| `POST` | `/conversations/{id}/typing` | Publish a typing indicator — *auth* |

### Admin

Every route below requires an administrator — *auth*.

| Method | Path | Notes |
| --- | --- | --- |
| `GET` | `/admin/stats` | Dashboard analytics |
| `GET` | `/admin/users` | Paginated users, with search and status filters |
| `GET` | `/admin/users/{id}` | A single user |
| `POST` | `/admin/users/{id}/ban` | Ban or unban |
| `POST` | `/admin/users/{id}/admin` | Grant or revoke the admin flag |
| `POST` | `/admin/users/{id}/verify` | Grant or revoke the verified badge |
| `DELETE` | `/admin/users/{id}` | Delete an account |
| `GET` | `/admin/posts` | Moderation queue of posts |
| `DELETE` | `/admin/posts/{id}` | Delete a post |
| `GET` | `/admin/comments` | Moderation queue of replies |
| `DELETE` | `/admin/comments/{id}` | Delete a reply |

## Production notes

The defaults are tuned for local development. Before deploying:

- Set `SECRET_KEY` to a long random value and serve over HTTPS.
- Point `DATABASE_URL` at PostgreSQL (or another server database) and manage schema
  changes with Alembic instead of `create_all`.
- Set `CORS_ORIGINS` to the real frontend origin.
- Build the frontend with `npm run build` and serve `frontend/dist` from a static host or CDN.
