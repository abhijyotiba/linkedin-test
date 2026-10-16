# LinkedIn Studio

An autonomous 6-agent LinkedIn growth system. Claude runs it on a schedule, writes the content, renders the carousel, emails you a daily kit, and publishes the post for you once LinkedIn is connected.

```
EVENING 18:45 IST (Sun–Thu)   MORNING 09:25 IST (Mon–Fri)      SUNDAY 10:00 IST
┌─────────────────────────┐   ┌──────────────────────────┐    ┌──────────────────────┐
│ 1 Content Intelligence  │   │ publish post (+PDF) via  │    │ 6 Analytics & Leads  │
│ 2 Ghostwriter           │──▶│   LinkedIn API           │    │   data/metrics.csv   │
│ 3 Design (carousel PDF) │   │ post 1st self-comment    │    │   kill <1k ×2        │
│ 4 Network Builder       │   │ email "LIVE: reply now"  │    │   double >5k         │
│ 5 Engagement Engine     │   │ (or "POST NOW" + PDF if  │    │   email report       │
│ → content/<date>.json   │   │  LinkedIn not connected) │    └──────────────────────┘
│ → content/<date>.pdf    │   └──────────────────────────┘
│ → email the kit         │
└─────────────────────────┘
```

Every run is a Claude Code scheduled task that follows `agents/PLAYBOOK.md`. The repo is the database: one JSON + PDF per day in `content/`, post URNs in `data/posted.jsonl`, your weekly numbers in `data/metrics.csv`.

## Your daily routine (60 min)

| Time (IST) | Agent | You do |
|---|---|---|
| 09:00 | Engagement Engine | Paste the 10 creator comments from last night's kit (3 lines, add insight). |
| 09:30 | Ghostwriter + Design | Post goes live automatically (or paste text + upload the PDF from the "POST NOW" email). Post self-comment #1. |
| 09:35–10:00 | Engagement Engine | Reply to every comment with a question. Send the 5 value DMs. |
| 19:00 | Network Builder | Send the 10 personalised connection requests from the kit. Unfollow inactive accounts on Fridays. |
| Sunday | Analytics | Append last week's numbers to `data/metrics.csv`; read the Sunday report. |

Weekly mix: Mon story · Tue carousel · Wed contrarian · Thu build-in-public · Fri debate (`config.yaml → weekly_mix`).

## Connect LinkedIn (one time, 5 minutes) — turns on auto-publish

LinkedIn's official API lets an app post to **your own profile** with no partner review (product "Share on LinkedIn"). Comments on other people's posts, connection requests and DMs are not available via API, and bots that do them get accounts restricted, so those stay human-clicked with AI-written text.

1. https://www.linkedin.com/developers/apps → **Create app** (needs any LinkedIn Page you admin; create a free one if needed).
2. App → **Products** → add **Share on LinkedIn** and **Sign In with LinkedIn using OpenID Connect**.
3. App → **Auth** → add redirect URL `http://localhost:8765/callback`. Copy Client ID and Client Secret.
4. On your laptop:
   ```bash
   git clone <this repo> && cd linkedin-test && pip install -r requirements.txt
   printf 'LINKEDIN_CLIENT_ID=...\nLINKEDIN_CLIENT_SECRET=...\n' > .env
   python studio.py auth          # browser opens, approve, .env now has the token + your person URN
   python studio.py publish content/<date>.json --dry   # sanity check, nothing is posted
   ```
5. Put `LINKEDIN_ACCESS_TOKEN` and `LINKEDIN_PERSON_URN` (from `.env`) into the Claude Code cloud environment: environment menu → Edit → **Network secrets / environment variables**. Also add `api.linkedin.com` and `www.linkedin.com` under **Allowed domains** (keep "Allow package managers" ticked).
6. Tokens last 60 days. `python studio.py auth` again before `LINKEDIN_TOKEN_EXPIRES` in `.env` and update the secret.

Optional: drop a square `assets/face.png` (it is git-ignored) and the cover + CTA slides get your face. Posts with a face get noticeably more reach.

## Run it by hand

```bash
python studio.py render  content/2026-10-20.json   # JSON -> 1080x1350 PDF carousel
python studio.py brief   content/2026-10-20.json   # the HTML kit
python studio.py publish content/2026-10-20.json   # post it (needs .env)
python studio.py comment urn:li:share:123 "text"   # comment as you
python studio.py metrics                           # weekly report
python test_studio.py                              # self-check
```

## Change the voice, niche, schedule

Edit `config.yaml` (persona, audience, proof points, real stories, target creators, brand colours, banned phrases, timezone). The playbook reads it on every run. To move the schedule, edit the scheduled tasks in Claude Code (they are named `LinkedIn Studio · evening/morning/sunday`).
