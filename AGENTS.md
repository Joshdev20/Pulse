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
.venv/bin/python -m pytest -q             # 234 tests
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
- The theme lives in a module-level store in `lib/useTheme.ts` read through
  `useSyncExternalStore`, not in component state. The switch is rendered in more than one
  place (sidebar and Settings), and a `useState` per component let the two drift: flipping
  one left the other showing the old icon. Keep writes going through `setTheme`.
- Every list view needs a loading state (`FeedSkeleton`), an empty state (`EmptyState`),
  and an error state (`ErrorState`) from `components/UI.tsx`.
- Style with existing classes in `styles.css` and the CSS custom properties at the top of
  that file. Do not introduce a styling library or inline hex colours.
- Every token in the theme blocks must resolve to a literal, never to itself. A typo like
  `--accent-soft: var(--accent-soft)` is a cycle, so the custom property is invalid at
  computed-value time and anything using it silently loses its background. Both themes are
  covered because the light block sits above the dark one.
- `/messages` is an app pane, not a document. `.main:has(> .messages)` is a flex column of
  `100dvh` so the page head and the inbox/thread fill the viewport exactly and the page
  itself never scrolls; the inbox and thread scroll inside their own boxes. Do not give the
  pane a `calc(100vh - Npx)` height — that magic constant drifts out of step with the
  header and bottom padding and leaves a stray page scroll.

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
- Presence is a *transition*, not a per-socket fact. `routers/realtime.py` announces it only
  when the first socket for a user connects and the last one disconnects, so a second tab
  does not flicker the status. It fans out to conversation partners only (`_announce_presence`
  resolves the peer ids), because presence is rendered in a thread header and broadcasting it
  to every socket would leak who is online to strangers.
- A socket's `onclose` must ignore a socket that is no longer the current one
  (`if (socketRef.current !== socket) return`). React StrictMode mounts the effect twice in
  development, so the first socket is orphaned; letting its late close clear the ref and
  schedule a reconnect leaves two live authenticated sockets, and every event arrives twice.
- Presence on a *user* payload is opt-in (`with_presence=True` on `serialize_user` /
  `serialize_users`), and only the conversation partner payload passes it. Leaving it on by
  default would publish `is_online`/`last_seen_at` through search, follower lists and post
  authors, which is a different disclosure from the thread header that needs it.
- The notification frame carries a small `actor` summary (`notification_event` in
  `routers/notifications.py`) so a backgrounded tab can name who acted without a fetch. Both
  `posts._notify` and `users.follow_user` publish through it — keep the two in step rather
  than re-inlining the payload.

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
- Uploaded files are written to `backend/uploads/` (gitignored) under random names, and the
  bytes are *also* stored in `media.blob`. `storage.resolve` re-checks containment, so never
  build an upload path by hand. The database copy is what makes media durable: a host with an
  ephemeral filesystem loses the directory on every redeploy while the database row survives.
  `GET /api/media/{id}` serves `media.blob` when present and falls back to the file on disk,
  which is how a row written before the column existed still resolves. Any new upload path
  (`routers/media.py` upload, `_set_profile_image` in `routers/users.py`, `seed._seed_media`)
  must set `blob` or the file is the only copy and will be lost with the disk.
- The body-size ceiling in `middleware.py` has two tiers, and the upload routes must stay on
  the larger one. `max_body_bytes` is a JSON ceiling (~1 MB) while `max_upload_bytes` is the
  real cap for images (~8 MB), so a route that accepts a file has to be listed in
  `UPLOAD_PATH_PREFIXES` or a picture well under its own limit is rejected at the door with
  413 before the handler runs. `tests/test_middleware.py` pins both tiers.
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
- `mediaCache.ts` releases an entry from a `queueMicrotask`, not synchronously. StrictMode
  mounts, unmounts and remounts every effect in one tick; releasing inline cancelled a
  download that the remount immediately re-requested, so each image was fetched twice on
  every mount. Keep the deferral when touching that file.
- A failed media download must not stay cached. The cache is keyed by path and an entry is
  created before its fetch settles, so a rejection left in place makes the element show a
  broken image for the rest of the session — even after the cause clears, which a
  rate-limited burst (429) does on its own within a minute. `refMedia` deletes the entry in
  its `catch` so the next consumer retries; `primeMedia` does the same.
- The media cache holds the *previous* account's bytes after a sign-in change, because a
  path carries no notion of who fetched it. `clearMediaCache()` revokes and forgets every
  entry; `AuthContext` calls it on login, register, logout and a 401, so a shared tab never
  renders one user's avatar or cover for the next. The backend needs no equivalent: every
  viewer-relative read keys its cache by `viewer.id`.
- A placeholder must distinguish "still loading" from "will never load": read `failed` from
  `useMediaStatus`, or a broken avatar or attachment shimmers forever. Use
  `useMediaReady` rather than a bare `onLoad` — blob bytes are already in memory, so the
  element can finish decoding before React attaches the handler and the event never fires.
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
  rather than hardcoding a host or path. This includes the XHR upload path in
  `uploadWithProgress()`, which cannot use `fetch` because it reports progress: passing a
  relative path there bypasses `apiUrl()` and the deploy's SPA fallback answers the upload
  with a 404, so uploads break in a split deploy while every other call keeps working.
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
- Uploads are written to `UPLOAD_DIR` *and* to `media.blob`. The database copy is the
  durable one and is what a free instance relies on: Render free web services cannot attach a
  disk at all, so `render.yaml` omits the `disk:` block and leaves `UPLOAD_DIR` unset
  (combining `plan: free` with a `disk:` block makes the Blueprint unprovisionable even though
  it passes Render's JSON schema). With the bytes in the row, that configuration keeps media
  across redeploys instead of losing it. A mounted volume is still worth setting for large
  files, but it is no longer the difference between media surviving a deploy and not.
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
  Cover photos work the same way through `cover_url_for`.
- Media visibility is decided by `is_public_profile_image` in `routers/media.py`, which
  checks `avatar_media_id` *and* `cover_media_id`. The check has to cover both columns: an
  image in only one of them loads for its owner and 404s for everyone else, which reads as a
  broken profile rather than a permissions problem. Anything not in those two columns stays
  private to its uploader until it is attached to a post or a message.
- The profile picture and cover photo share `_set_profile_image` / `_clear_profile_image` in
  `routers/users.py` and differ only in which column they write, so a fix to one applies to
  both. Replacing or clearing either only deletes the superseded upload when it is not also
  attached to a post or message.
- Reposts are a self-referential `Post.repost_of_id`: a share is a real row authored by the
  sharer, so it reaches their followers, while its content, author and every interaction come
  from the original. `serialize_posts` nests the original exactly one level deep, and
  `_original_of` in `routers/posts.py` redirects a share of a share at the original so a chain
  can never form. The repost endpoints return the *original*, like/unlike style, so the client
  updates the post it is displaying.
- `delete_post` removes rows pointing at the post before deleting it. The `ON DELETE CASCADE`
  on `repost_of_id` covers a database created by this version, but a pre-existing database gets
  the column from `ensure_schema()`, which cannot add a constraint — and the test suite builds
  its tables without the SQLite `foreign_keys` pragma, so the cascade does not fire there either.
- Reposting your own post is a 400, and `PostCard` omits the button in that case rather than
  offering an action the API will refuse.
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
- Media never renders with a bare `<img src>` pointing at `/api/media/{id}`: the route
  requires a bearer token, so only `useMediaUrl`/`useMediaStatus` can load it into a blob
  URL. Anything that renders an `<img>` from a media path directly will show its alt text for
  a signed-in viewer too.
- `useMediaStatus` retries a failed download on a backoff. A one-shot load leaves the
  element showing its alt text for the whole session, because the effect never runs again for
  the same path — a transient 429, a token mid-refresh or a briefly-down backend all become
  permanent. A 429 is retried on its own larger schedule (`RATE_LIMIT_DELAYS_MS`) and honours
  the server's `Retry-After`, since this API's window is a minute and a short backoff would
  exhaust itself inside it. `fetchAsset` throws `MediaError`, which carries the status and
  `Retry-After`.
- `PostDetailPage` renders `MediaGallery` for `post.media`, like `PostCard` does. The page
  grew a custom layout for the large-format post and missed the attachment block, so an
  attached image showed on the feed and profile but not on the post's own URL.
- iOS Safari zooms the page when it focuses a text field whose computed `font-size` is under
  16px, and then needs a pinch to recover. The base `.input`/`.textarea` are 15px and the
  admin search 14px, and the search page *autofocuses*, so it zoomed on open. A single
  `@media (max-width: 900px)` block raises every field to 16px; the specific selectors are
  listed there so they outrank the more specific rules they override. The composer textarea is
  excluded because at 17px it already clears the threshold.
- A failed media download is only retried when it can plausibly succeed: 429, 5xx and a
  transport failure. A 404 is not retried, because it means the bytes are not there and
  retrying cannot change that. That can still happen for a row that predates `media.blob`
  whose file is gone, so keep it: a retry would only delay the "unavailable" state.
- Media bytes live in `media.blob`, which is a `deferred` column so listing a feed never
  loads them. `serialize_media` never touches `blob`, so the deferral is what keeps a feed
  response from pulling megabytes of attachments into memory — do not remove `deferred`, and
  read `blob` only in the download handler (and the backfill). A row whose `blob` is null and
  whose file is gone is a genuine 404; `GET /api/media/{id}` must answer 404, never 500.
  Row-stored bytes are bounded by the same caps as the files (images 8MB, video 32MB), which
  keeps the largest row modest; a deployment serving much larger video should put those bytes
  in object storage instead.
- On a host without a persistent disk — `render.yaml` sets `plan: free`, which cannot attach
  one — a redeploy wipes `UPLOAD_DIR` while `DATABASE_URL` (an external Postgres) keeps the
  rows. Before media moved into `blob`, every attachment 404'd at once and the symptom looked
  account-scoped, because the browser that uploaded the file still had it in its HTTP cache
  (`Cache-Control: private, max-age=86400` on the download route) and kept rendering it. With
  the bytes in the row that failure mode is gone; a redeploy keeps serving the same media.
  `_backfill_media_blobs` runs at startup to copy any legacy on-disk file into its row, which
  preserves a local database's files before they are lost and is a no-op on a host that has
  already lost them.
- The inbox (`GET /api/conversations`) must stay a constant number of queries. Building each
  row with its own partner lookup, last-message query and unread count made an eight-thread
  inbox cost 98 SELECTs and grow with the conversation count. `routers/messages.py` now does
  it with one joined query plus batched serialization — 11 SELECTs for any inbox size. Guard
  it with the `_count_selects` SQLAlchemy event counter in `tests/test_messages.py`, which
  asserts the count does not rise when conversations are added. `GET /api/conversations/unread-count`
  had the same shape and is a single aggregate now.
- Deleting a comment must remove its whole subtree. A parent's replies are one level down, so
  deleting only the row you were handed orphans deeper replies; `delete_comment` in
  `routers/posts.py` walks descendants with a frontier loop. Do not rely on the ORM cascade
  for the bulk delete — a database migrated from before threading has no FK constraint for it
  to follow.
- A new nullable column on an existing table needs an entry in `schema.py` `_ADDITIONS`;
  `create_all` only creates missing tables, never columns. `comment_likes` is a new table so
  `create_all` handles it, but `comments.parent_id` and `media.blob` are `ALTER TABLE`s.
  The migration is additive and idempotent, so existing comments stay top-level rows and
  an existing media row loads with a null `blob` until the startup backfill or a re-upload
  fills it. `media.blob` is declared `BYTEA NULL`, which SQLite accepts as a column type
  name even though it stores it as ordinary BLOB affinity.
- A VAPID public key must be the 65-byte uncompressed EC point (`0x04 || x || y`), not the
  64-byte `x || y` form. `PushManager.subscribe` rejects the short form with
  `InvalidAccessError`, which reaches the user as a toggle that flips on and silently fails.
  `config.valid_vapid_public_key` checks the shape and `push_configured` reports push as
  unavailable when it is wrong, so a bad key is a configuration error rather than a runtime
  one. A key pair also cannot be rotated without invalidating every stored subscription,
  since each browser subscribed against the old public key.
- Web Push must not be sent inline in an `async` handler. `pywebpush` is synchronous
  `requests`, so a send blocks for a full push-service round trip and stalls the event loop
  for every other request (measured at 279 ms on a like). `push.schedule_notify` hands the
  send to a worker thread that opens its own `SessionLocal`; the request's session is closed
  as soon as the response returns. Push is best-effort and must never fail the request that
  triggered it.
- `frontend/public/` is served verbatim and is not bundled, so `sw.js` is plain JavaScript
  and cannot import from `src/`. Check the file mode when adding to it: it was committed
  `600` once, which would not be served.

## Feed ordering and rate-limit budgets

- The home feed is a *weighted random permutation*, not newest-first. `_ordered_feed`
  gives each of the most recent `FEED_POOL_SIZE` (300) posts an exponential draw key
  scaled by its relevance weight and sorts on that, so a fresh or engaging post is
  more likely to lead without the order being fixed. The pool is bounded on purpose:
  ordering every post in the table would make the feed cost grow with the site.
- The feed order is a pure function of `(seed, post_id)`, hashed per post rather than
  drawn from one shared random stream. That is what keeps page 2 of a session
  continuing page 1's order instead of reshuffling and repeating or skipping posts.
  The client sends `seed` (one per scope change, in `HomePage.tsx`); without it the
  server derives a per-viewer default. Do not replace the key function with
  `random.shuffle` — that reintroduces duplicate and skipped posts across pages.
- `_age_hours` exists because SQLite returns naive datetimes. Subtracting one from an
  aware `now` raises `TypeError`, which broke the whole feed. Read the naive value as
  UTC, which is how it was written.
- `RateLimitMiddleware` has three independent budgets: `auth` (credential minting,
  `SENSITIVE_LIMIT`), `media` (downloads only, `MEDIA_LIMIT_MULTIPLIER` x the read
  limit) and `api` (everything else). They used to share one bucket, which meant one
  media-heavy feed page exhausted the read budget and then every picture answered 429
  — the app looked broken rather than rate-limited. Only `GET`/`HEAD` on
  `/api/media...` gets the media budget; an upload must stay on the ordinary budget or
  a client can fill the instance's storage. `tests/test_middleware.py` pins both.
- Idle buckets are swept so a burst of distinct clients cannot grow the counter table
  without bound.
- `MemoryStore` is bounded (`MAX_ENTRIES`). Its keys include the viewer id, so without
  a ceiling it grows with the number of people browsing rather than with the content.
- Media downloads stream `media.blob` in slices (`_stream_blob`) rather than passing
  the whole buffer to `Response`, which copies it again while rendering. The response
  also carries an `ETag` and honours an exact `If-None-Match` with a bodyless 304;
  `Vary: Authorization` is required because the response is per-viewer, so a shared
  cache must never hand one account's bytes to another. Do not drop that `Vary`.
- A lost race on a double-tap like or follow is a success, not a 500: the unique
  constraint rejects the duplicate insert and the caller's intent is already met.
  `IntegrityError` is caught and rolled back around the flush.
- Videos get a poster from a frame decoded in the browser (`src/lib/videoPoster.ts`).
  The backend has no ffmpeg, so no thumbnail can be made at upload time; the bytes are
  already downloaded to render the tile, so a `<video>` decodes one frame, it is drawn
  to a canvas and kept as a data URL, shared per media path and reference counted.
  `captureFrame` returning null on a tainted canvas is a guard — media is same-origin.
- Uploads that predate storing bytes in `media.blob` were lost with the ephemeral
  disk and answer 404 forever; on this deployment that is a normal response, not a
  bug. The gallery says "Image unavailable"/"Video unavailable" for a failed download
  rather than leaving an empty frame.
