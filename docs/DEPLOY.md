# Deploying Pulse

Pulse is split across two hosts on purpose:

| Part | Host | Why |
|---|---|---|
| `frontend/` | **Vercel** | Static Vite build; a CDN and preview deploys are exactly what it is good at |
| `backend/` | **Render** (or Railway / Fly.io) | Needs a long-lived process, a writable disk and WebSockets |

## Why the backend is not on Vercel

Vercel Functions are stateless and short-lived. Pulse depends on four things that
serverless breaks, so the split is a requirement rather than a preference:

1. **SQLite on local disk** — wiped on every deploy and not shared between
   instances. Writes would silently disappear.
2. **Media on a local disk** — uploaded bytes are stored in the database
   row (`media.blob`) as well as the file, so an ephemeral disk no longer
   loses them. Only the database has to persist.
3. **The rate limiter counts in process memory** — limits would multiply by the
   number of instances.
4. **WebSockets** — capped at 300s per connection, and there is no way to
   broadcast to sockets held by another instance. The Redis pub/sub bus
   coordinates worker *processes*; it cannot coordinate unrelated function
   instances, so DMs and notifications would drop silently.

Vercel also caps a request body at **4.5 MB**, while Pulse accepts videos up to
32 MB. Vercel WebSocket support is in public beta (Python since July 2026) and
requires Fluid Compute.

---

## 1. Deploy the backend

### Option A — Render (recommended, has a free tier)

The repo contains `render.yaml`, so this is a blueprint deploy.

1. Push this repo to GitHub.
2. Render Dashboard → **New** → **Blueprint** → pick the repo.
3. Render creates the `pulse-api` web service from `backend/`.
4. Fill in the environment variables marked `sync: false` (Render prompts for
   them). See the table below.
5. Deploy, then confirm `https://<your-service>.onrender.com/api/health`
   returns `{"status":"ok",...}`.

> **Free tier caveats.** A free web service **cannot attach a persistent disk**
> (Render rejects `plan: free` together with a `disk:` block), so:
>
> - **Uploads are kept in the database** (`media.blob`), so they survive a
>   redeploy even without a disk. A mounted volume is still worth setting for
>   large files.
> - **It sleeps after ~15 minutes idle** and takes ~30–60s to wake, so the first
>   request after a pause is slow. The workflow in
>   `.github/workflows/keep-alive.yml` pings `/api/health` every 5 minutes to
>   keep it warm — see [Keeping the backend warm](#keeping-the-backend-warm).
> - **Render's own free Postgres expires 30 days after creation.** Use a Neon or
>   Supabase URL for anything you want to keep.
>
> To offload large files to a volume as well, upgrade to a paid instance type,
> change `plan` in `render.yaml`, uncomment the `disk:` block, and add
> `UPLOAD_DIR=/data/uploads`.

### Option B — Railway / Fly.io

Both run the provided `backend/Dockerfile` unchanged.

- **Railway:** New Project → Deploy from GitHub → set **Root Directory** to
  `backend`. Railway detects the Dockerfile. Add a volume mounted at `/data`.
- **Fly.io:** `cd backend && fly launch` (accept the Dockerfile), then
  `fly volumes create pulse_data` and mount it at `/data`.

Either way, set the same environment variables as below.

### Option C — Docker anywhere

```bash
docker build -t pulse-api ./backend
docker run -p 8000:8000 \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e DATABASE_URL="postgresql://user:pass@host/db" \
  -e CORS_ORIGINS="https://your-app.vercel.app" \
  -e AUTO_SEED=true \
  -v pulse_data:/data \
  -e UPLOAD_DIR=/data/uploads \
  pulse-api
```

### Backend environment variables

| Variable | Required | Value |
|---|---|---|
| `SECRET_KEY` | **Yes** | `openssl rand -hex 32`. Tokens signed with the default key are forgeable. |
| `DATABASE_URL` | **Yes** | A hosted Postgres, e.g. a Neon URL. Render/Railway/Heroku `postgres://` URLs are normalized automatically. Without this you get SQLite on an ephemeral disk. |
| `CORS_ORIGINS` | **Yes** | Your exact Vercel URL, e.g. `https://pulse-hazel-nine.vercel.app`. Comma-separate several. |
| `CORS_ORIGIN_REGEX` | No | Defaults to `https://[a-zA-Z0-9-]+(\.vercel\.app)$`, which matches the production alias and every preview deployment. |
| `AUTO_SEED` | No | `true` seeds the demo users/posts when the database is empty. Demo only — it is a no-op once users exist. |
| `UPLOAD_DIR` | No | Point at the mounted volume, e.g. `/data/uploads`. |
| `REDIS_URL` | No | Leave **unset** for a single instance. Set it only when running more than one worker, so realtime fan-out reaches sockets on other workers. |

### Neon specifics

Neon hands out a URL shaped like
`postgresql://user:password@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require`.

- `sslmode=require` is already in the URL Neon gives you. Keep it.
- Prefer the **pooled** host (the one containing `-pooler`). Pulse sets
  `pool_pre_ping` and a 280s `pool_recycle`, so it tolerates a pooler that drops
  idle connections.
- `AUTO_SEED=true` creates the tables on first boot, so you do not need to run
  any migration by hand.

### Notes on the database

`AUTO_SEED=true` creates tables and inserts the demo content on first boot, so a
fresh deploy is immediately usable. Sign in as any seeded user (e.g. `ada`) with
password `password123`.

The engine is configured with `pool_pre_ping` and a 280s `pool_recycle` for
Postgres, because hosted Postgres (and poolers in front of it) closes idle
connections.

---

## 2. Deploy the frontend to Vercel

1. Vercel Dashboard → **Add New** → **Project** → import the GitHub repo.
2. Set **Root Directory** to `frontend`. `frontend/vercel.json` supplies the
   framework, build command, output directory and the SPA fallback rewrite.
3. Add a build-time environment variable:

   | Key | Value |
   |---|---|
   | `VITE_API_BASE` | `https://<your-backend-host>` — **no trailing slash** |

4. **Deploy.**

`VITE_API_BASE` is inlined into the bundle by Vite at build time. It is *not* a
runtime variable, so changing it requires a redeploy, not a restart.

**This variable is not optional.** If it is missing, the bundle calls its own
origin for `/api/*`, Vercel answers those paths with `index.html`, and the site
renders perfectly while every login and registration fails. The build now fails
loudly when `VERCEL` is set and `VITE_API_BASE` is empty, so this cannot ship
silently. See the troubleshooting table.

### How the two connect

- **HTTP** — every request is built as `VITE_API_BASE + /api/...`. With the
  variable set, requests go straight to the backend host, so no Vercel rewrite
  or proxy is involved.
- **WebSocket** — `RealtimeContext` derives `wss://<backend-host>/api/ws` from
  the same variable, honouring `https → wss`.
- **CORS** — the backend must allow the Vercel origin. Either set `CORS_ORIGINS`
  to the production URL or rely on the default `CORS_ORIGIN_REGEX`, which also
  covers preview deployments.

With `VITE_API_BASE` unset, the frontend stays same-origin and relies on the Vite
dev proxy — which is exactly what local development and the single-container
deploy want.

### Deployment protection

If preview URLs redirect to a Vercel login, protection is enabled. To let tools
reach them, generate a bypass secret under **Settings → Deployment Protection →
Protection Bypass for Automation** and pass it as
`?x-vercel-protection-bypass=<secret>` or the `x-vercel-protection-bypass`
header. Alternatively set Vercel Authentication to *Only Production Deployments*.

---

## 3. Verify a deploy

```bash
# Backend is up and which cache it chose
curl https://<backend>/api/health
# → {"status":"ok","service":"Pulse API","cache":"memory"}

# Login works (proves the DB is seeded and reachable)
curl -X POST https://<backend>/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"ada","password":"password123"}'

# CORS allows the Vercel origin
curl -i -X OPTIONS https://<backend>/api/auth/login \
  -H 'Origin: https://<your-app>.vercel.app' \
  -H 'Access-Control-Request-Method: POST' | grep -i access-control-allow-origin
```

Then open the Vercel URL and sign in as `ada` / `password123`. Open the browser
console: the WebSocket should connect to `wss://<backend>/api/ws`, and the
`/notifications` page should render with a live socket.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Site renders but **no account can be created or logged in** | `VITE_API_BASE` was not set at build time, so the bundle called its own origin and Vercel answered `/api/*` with `index.html`. Set it in the Vercel project and redeploy. The build now fails when this is missing. |
| Everything works **except uploads** (404, or "the upload was answered by the web server") | The upload XHR was built from a relative path instead of `apiUrl()`, so it hit the frontend host rather than the API. Uploads use `XMLHttpRequest` for progress reporting and so do not inherit the `fetch` wrapper's URL handling. Fixed in `uploadWithProgress()`; keep new upload paths going through `apiUrl()`. |
| Render: "unable to read the render.yaml file" | The Blueprint is invalid or unprovisionable. Validate with `render blueprints validate` or against https://render.com/schema/render.yaml.json. The most common trap is a `disk:` block on `plan: free` — Render's JSON schema accepts it, but free web services cannot have disks. |
| Browser console: CORS error | `CORS_ORIGINS` does not include the Vercel origin and the regex was overridden |
| Login returns 401 on a fresh deploy | `AUTO_SEED` was not `true`, so there are no users — seed manually with `python -m app.seed` |
| Media vanish after a redeploy | Should not happen now: bytes are stored in `media.blob` and served from the database. If it does, the row predates that column *and* the file is gone — re-upload the file. |
| WebSocket fails to connect | Backend host is unreachable, or a proxy in front of it does not forward `Upgrade` |
| First request after idle takes ~60s | Render free tier cold start — the keep-alive workflow should prevent it; check its recent runs if it does not |
| `FUNCTION_PAYLOAD_TOO_LARGE` | A Vercel Function received >4.5 MB — the backend is on the wrong host |
| `ModuleNotFoundError: psycopg` in the backend logs | `psycopg[binary]` is in `requirements-prod.txt` but the service is installing `requirements.txt`, or is building from a stale commit |

### Keeping the backend warm

A Render free instance sleeps after ~15 minutes idle and takes 30–60s to wake.
Every request made during that wake-up stalls, and a media download that stalls
surfaces in the UI as a **broken image** or a video that never plays — so the
symptom looks like lost media rather than a sleeping host. Pinging the health
endpoint on a schedule keeps the instance awake and removes that failure mode.

`.github/workflows/keep-alive.yml` runs `scripts/keep_alive.py` every 5 minutes,
which is comfortably inside the idle window. It is scheduled from GitHub rather
than from Render because a Render-side cron job would have to live on the same
sleeping service. The repository is public, so Actions minutes are not billed.

> **Verify the schedule before relying on it.** The workflow is registered and
> active, and a manual `workflow_dispatch` run succeeds, but the `schedule`
> trigger was **not** observed firing within ~48 minutes of being added. GitHub
> documents that scheduled runs can be delayed and are sometimes skipped under
> load, and newly added schedules can take a while to begin. Check the Actions
> tab for runs with the `schedule` event before trusting this as your only
> keep-alive. A purpose-built external monitor is the more dependable option.

The script touches only `/api/health`: unauthenticated, no database work, no
writes. It needs nothing but the standard library. Point it elsewhere with a
repository variable:

```bash
# Local run against any deploy
PULSE_API_URL=https://pulse-api-y16m.onrender.com python scripts/keep_alive.py
```

Its output includes the cache backend (`ok (cache=memory)`), which is the
quickest way to confirm from outside whether a `REDIS_URL` deploy actually
connected.

Two honest limits:

- **GitHub may delay scheduled runs** under load, and the first runs after
  adding a schedule can lag. If the Actions tab shows no `schedule` runs, use an
  external monitor instead — UptimeRobot or cron-job.org both offer a 5-minute
  interval on their free tier and are built for exactly this.
- **This only helps while it runs.** A scheduled workflow is disabled
  automatically after 60 days without repository activity, so a dormant repo
  loses its keep-alive. Check the Actions tab if a cold start returns.

Warming the host does not make the *first* request fast if the instance already
slept. The durable fix is a paid instance type, which never sleeps.

### Diagnosing "the site loads but nothing works"

`/api/*` is served by Vercel, not the backend, in this architecture. So you can
ask Vercel directly what it is doing:

```bash
# A working split deploy returns JSON. index.html means the API base is wrong.
curl -s https://<your-app>.vercel.app/api/health
```

- **JSON** → the frontend is configured correctly.
- **HTML** → `VITE_API_BASE` is unset or wrong, so requests never leave Vercel.

Then confirm the backend itself is reachable and permissive enough:

```bash
curl -s https://<your-backend>/api/health
curl -i -X OPTIONS https://<your-backend>/api/auth/register \
  -H 'Origin: https://<your-app>.vercel.app' \
  -H 'Access-Control-Request-Method: POST' | grep -i access-control-allow-origin
```

If the backend answers but the browser still fails, the origin is missing from
`CORS_ORIGINS` and `CORS_ORIGIN_REGEX` was overridden.
