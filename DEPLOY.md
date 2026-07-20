# Deploying MacroAgent (paper / educational)

MacroAgent is a NiceGUI app: a **single long-lived Python process** that needs
**websockets** and **sticky, in-memory per-session state**. That rules out
serverless (Vercel/Lambda) and rules out running multiple instances. A managed
container host like **Render** (primary, below) or **Railway** is the right fit.

Everything here keeps the app **paper-only and educational** — no real money.

---

## What's already wired for deploy

- `macroagent_app.py` reads **`$PORT`** and binds **`0.0.0.0`** (managed hosts require this),
  runs with `reload=False`, and won't try to open a browser server-side.
- `requirements.txt` — Python deps (Render/Railway install these).
- `runtime.txt` — pins Python 3.12.
- `render.yaml` — Render Blueprint (one-click).
- `Procfile` — start command for Railway/Heroku-style hosts.
- Secrets stay out of git (`.env` is gitignored); you set them in the host dashboard.

---

## Deploy on Render (recommended)

1. **Push to GitHub** (see "First push" below if the repo isn't on GitHub yet).
2. In Render: **New + → Blueprint**, select the repo. Render reads `render.yaml`.
3. When prompted, fill in the four secret env vars (their values never touch git):
   - `FRED_API_KEY`, `GOOGLE_API_KEY`, `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`
   - `NICEGUI_STORAGE_SECRET` is auto-generated — leave it.
4. Click **Apply**. First build takes a few minutes; then you get a
   `https://macroagent-XXXX.onrender.com` URL.

Prefer clicking through the dashboard instead of the Blueprint? Create a **Web
Service**, set Build = `pip install -r requirements.txt`, Start =
`python macroagent_app.py`, and add the same env vars by hand.

## Deploy on Railway (alternative)

1. Push to GitHub. In Railway: **New Project → Deploy from GitHub repo**.
2. Railway auto-detects Python and uses the `Procfile` start command.
3. Add the same env vars under **Variables**. Railway injects `$PORT` automatically.

---

## First push (if the repo isn't on GitHub yet)

```bash
# from the project folder
git add -A
git commit -m "Prepare MacroAgent for deployment"
# create an empty GitHub repo first, then:
git remote add origin https://github.com/<you>/macroagent.git
git push -u origin main
```
Double-check `.env` is **not** in the commit (it's gitignored) before pushing.

---

## Know before you ship (real limitations, not blockers)

1. **Metrics don't persist.** `metrics.jsonl` lives on an ephemeral disk — it's
   wiped on every redeploy/restart. For durable metrics, move `log_event()` to a
   Supabase `events` table (already the roadmap plan; `log_event` is the only
   function that changes). Or attach a Render persistent disk.

2. **Everyone shares your one Alpaca paper account.** The app uses *your* server-side
   paper keys, so any visitor who places a paper trade is trading in your paper
   account, and their orders co-mingle there. It's fake money so the stakes are low,
   but on a public URL consider gating the EXECUTION tab (or per-user Alpaca keys via
   the Supabase auth step) before sharing widely.

3. **One instance only.** Per-session state is in memory, so horizontal scaling
   would split users across processes and break sessions. Keep it at 1 instance
   (`numInstances: 1`). Vertical scaling (bigger instance) is fine.

4. **Free tier sleeps.** Render/Railway free instances spin down after idle; the
   first visit after a nap is slow to wake. Upgrade the plan for always-on.

5. **Rotate keys if they ever touched git.** Set every secret in the dashboard, not
   in a committed file.

---

## Enabling login (Supabase magic link)

Login is **optional** — the app runs anonymous-only until these are set, then a
**LOG IN** button appears in the header.

1. Create a project at [supabase.com](https://supabase.com).
2. **Settings → API**: copy the **Project URL** and the **anon / public** key.
3. Set them as env vars (local `.env` *and* the Render dashboard):
   `SUPABASE_URL`, `SUPABASE_ANON_KEY`. (The anon key is public by design.)
4. **Auth → URL Configuration** — this is required, magic links only redirect to
   allow-listed URLs:
   - **Site URL**: your primary app URL (the `onrender.com` one).
   - **Redirect URLs**: add BOTH
     `http://localhost:8090/auth/callback` (dev) and
     `https://<your-app>.onrender.com/auth/callback` (prod).
5. **Email provider** is on by default. Supabase's built-in email sender is
   **rate-limited (~2–4/hour, for testing only)** — if links stop arriving, that's
   why. For real use, configure custom SMTP under **Auth → Emails**.

The app uses the **implicit** auth flow on purpose (tokens arrive in the URL
fragment at `/auth/callback`), which suits this stateless server; don't switch the
client to PKCE without also handling the code-exchange.

> Login currently establishes **identity** (email shown, LOG OUT, metrics tied to
> the user id). Persisting each user's **profile/trades across sessions** is the
> next step — it needs a Postgres `profiles` table + row-level security.
