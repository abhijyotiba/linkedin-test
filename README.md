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

Every run is a Claude Code scheduled task that follows `agents/PLAYBOOK.md`. The repo is the database: one JSON + PDF per day in `content/`, post URNs in `data/posted.jsonl`, your weekly numbers in `data/metrics.csv`, and `data/memory.md` (auto-built) so the writers never repeat a hook and build on what performed.

## How a post gets made (`.claude/workflows/linkedin-kit.js`)

The evening run is not one prompt. It is a 20+ agent pipeline, versioned in this repo:

| Stage | Agents | What happens |
|---|---|---|
| Research | 4 in parallel | niche news, LinkedIn platform changes, India+US market, what each target creator is posting now. Every fact carries a source URL. |
| Draft | 4 in parallel | one post per angle: story-first, data-led, contrarian, operator checklist. Same structure rules, same intel, different bets. |
| Judge | 3 per candidate | hook/dwell, credibility/specificity, audience fit. Scores summed, winner picked, scores stored in the kit JSON. |
| Verify | 2 refuters + 1 rewrite | every number and company claim in the winner is attacked against the sources; unsupported ones get corrected or removed. |
| Assemble | 2 + 1 | 10 creator comments keyed to what those creators are posting this week; 10 connection requests, 5 DMs, 5 idea hooks; then write, render, lint. |

Deterministic gates run on every kit (`python studio.py lint`): hook ≤ 7 words and not a question, 600 to 1300 characters, no em-dashes, no external links (reach penalty), ≤ 3 hashtags, no banned phrases, exactly 10/10/5/5 items, every intel line sourced, carousel limits. CI runs the gates on every push. `python studio.py doctor` preflights dependencies, fonts, the LinkedIn token and its expiry, and push access before every run. The LinkedIn client retries on 429/5xx and tells you plainly when the token has expired.

Analytics loop: reply to any kit or weekly email with your numbers in any format. The Sunday run reads the reply, files it into `data/metrics.csv`, applies the kill/double rules and rewrites next week's mix.

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
