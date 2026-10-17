# LinkedIn Studio

A self-hosted LinkedIn growth system. It runs a multi-agent pipeline on a schedule, writes one post kit per weekday for *your* profile and niche, renders the carousel, publishes through LinkedIn's official API once you connect it, and grades post types from the numbers you enter. One Python web service, one SQLite file, your own Anthropic API key. No subscription, no Claude Code, no external scheduler.

```
Settings (who you are, niche, audience, proof points, voice, creators, weekly mix)
        │
        ▼
Daily kit flow ── 4 researchers (server-side web search) ─▶ 4 drafts (story / data / contrarian / operator)
   18:45 Sun–Thu  ─▶ 3 judges per draft ─▶ 2 fact-checkers + correction pass ─▶ comments / connections / DMs / ideas
                  ─▶ quality gates ─▶ kit saved (draft) ─▶ email
        │
        ▼
You approve on the dashboard (or switch approval off)
        │
        ▼
Go live flow ── LinkedIn Posts API (text + carousel PDF) ─▶ first self-comment ─▶ LIVE email
   09:30 Mon–Fri   (or a POST NOW email with everything, until LinkedIn is connected)
        │
        ▼
You enter impressions / reactions / comments per post
        │
        ▼
Weekly flow ── kill types under 1,000 twice, double types over 5,000 ─▶ proposed mix ─▶ report
   09:50 Sun
```

## Run it locally (2 minutes)

```bash
pip install -r requirements.txt
cp .env.example .env            # put your ANTHROPIC_API_KEY and a DASHBOARD_PASSWORD in it
set -a; source .env; set +a
uvicorn app.main:app --reload
```
Open http://localhost:8000, sign in, fill **Settings** (the agents know nothing until you do), then open **Flows → Daily kit → Run**. The run page streams the log: research, drafts with hooks, judge scores, corrections, gates. The kit appears under **Kits** with the post, both self-comments, the carousel PDF, 10 creator comments, 10 connection requests, 5 DMs and 5 idea hooks.

Try it with no API key and no cost: `FAKE_LLM=1` runs the whole pipeline with canned output. `python tests/test_app.py` does exactly that end to end.

## Deploy on Render (10 minutes)

1. Push this repo to GitHub. In Render: **New → Blueprint**, pick the repo. `render.yaml` creates one always-on web service (Starter) with a 1 GB disk for the database.
2. Fill the secrets Render asks for: `DASHBOARD_PASSWORD`, `ANTHROPIC_API_KEY`. The rest are optional (below).
3. Open the service URL, sign in, fill Settings, run the Daily kit flow once.

The scheduler runs inside the web service, which is why the instance must not sleep (free instances do). If you prefer Render Cron Jobs or any other scheduler, set `SCHEDULER=0` and call `POST /api/run/daily_kit` (and `publish`, `weekly`) with `Authorization: Bearer $CRON_TOKEN`.

Postgres instead of SQLite: set `DATABASE_URL` to the Postgres URL; the driver is included.

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | everything (billed per token; a daily kit costs roughly $1 to $3 on Claude Opus 5.5 with web search) |
| `DASHBOARD_PASSWORD`, `SECRET_KEY` | the login wall |
| `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET` | auto-publish. Create an app at linkedin.com/developers/apps, add the products **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**, add `https://<your-service>/auth/linkedin/callback` as a redirect URL, then press **Connect LinkedIn** in Settings. Tokens last 60 days; reconnect when Settings says so. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | kit, LIVE and weekly emails (Gmail: smtp.gmail.com, 587, an app password). Without them everything is still on the dashboard. |
| `CRON_TOKEN` | the `/api/run/<flow>` endpoint |
| `SCHEDULER` | `0` disables the in-process scheduler |

## What you control

- **Settings**: name, headline, niche, audience, market, offer, proof points (the only facts the writers may claim as yours), real stories, voice rules, banned phrases, target creators, timezone, weekly mix, approval gate, model and effort, email, carousel colours and handle.
- **Flows**: every prompt, angle, judge lens, refuter and count of the pipeline is editable in the UI (form fields for scalars, raw JSON for lists). Schedules are per flow. Duplicate a flow to experiment; disable one to pause it; **Run now** with a chosen date and day type.
- **Kits**: edit the post and self-comments (gates re-run on save), approve, publish now, download the carousel PDF, mark as posted by hand, enter the numbers, delete.
- **Runs**: live log per run with every step, judge notes, unsupported claims, edits made by the gate fixer, tokens and cost.

## How the pipeline keeps quality

- Four candidates on different angles compete; three judges with different lenses score each; the winner is the sum.
- Two fact-checkers attack every number and company claim in the winner against the research; a correction pass applies their fixes before anything reaches you.
- Deterministic gates reject weak hooks, wrong lengths, links, em-dashes, banned phrases, short lists and unsourced research. A formatting-only fixer may touch whitespace and length; it is forbidden from changing facts. Anything still failing is saved as a draft with the violations shown, never auto-published.
- Memory: every past hook and banked idea is in the prompt, so posts never repeat and later posts build on what performed.
- Publishing is idempotent (a kit is never posted twice) and the create call is never auto-retried.

## Limits by design

Comments on other people's posts, connection requests and DMs are not available through LinkedIn's API, and bots that do them get accounts restricted. The pipeline writes all of it; you paste. Post analytics for personal profiles are not exposed by the API either, so you enter impressions, reactions and comments per post (30 seconds a day) and the weekly flow does the rest.

## Layout

```
app/main.py        routes, auth, run launcher, LinkedIn OAuth, scheduler startup
app/flows.py       default flow definitions (prompts, angles, lenses) and the run engine
app/llm.py         Claude client: structured JSON output, server-side web search, cost, offline fake
app/kits.py        quality gates, brief HTML, memory text, weekly verdicts
app/render.py      carousel PDF (1080x1350)
app/linkedin.py    OAuth, Posts API, Documents API, comments
app/mailer.py      SMTP
app/scheduler.py   in-process scheduler with a 10-minute catch-up window
app/db.py          SQLAlchemy models (settings, flows, runs, kits, metrics)
app/templates/     dashboard, kits, kit, flows, flow, runs, run, settings, login
tests/test_app.py  end-to-end check with the fake model
render.yaml        Render blueprint
```
