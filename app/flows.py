"""Flows: editable pipeline definitions (seeded here, stored in the DB, edited in the UI) and the engine that runs them."""
import asyncio
import copy
import datetime as dt
import html
import json
import string
import traceback
from zoneinfo import ZoneInfo

from sqlalchemy import select

from . import kits as K
from . import linkedin, mailer, render
from .db import Flow, Kit, Metric, Run, SessionLocal, get_settings, save_settings, utcnow
from .llm import LLM, LLMError, Usage

# ------------------------------------------------------------- schemas ----
S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}


def obj(props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def arr(items, n=None):
    d = {"type": "array", "items": items}
    if n:
        d["minItems"] = n  # honoured by the offline fake; stripped before the API call, counts are enforced in code
    return d


INTEL = obj({"bullets": arr(obj({"fact": S, "source": S}), 5), "questions": arr(S, 3), "hooks": arr(S, 3)})
CAROUSEL = obj({"title": S, "subtitle": S, "cta": S, "slides": arr(obj({"title": S, "body": S}), 5)})
CANDIDATE = obj({"angle": S, "hook": S, "text": S, "self_comments": arr(S, 2), "carousel": CAROUSEL})
SCORE = obj({"hook": N, "dwell": N, "specificity": N, "fit": N, "total": N, "notes": S})
VERIFY = obj({"claims": arr(obj({"claim": S, "verified": B, "fix": S}), 1)})
ENGAGEMENT = obj({"engagement_comments": arr(obj({"creator": S, "topic": S, "comment": S}), 10)})
NETWORK = obj({"connection_requests": arr(obj({"who": S, "why": S, "message": S}), 10), "dms": arr(obj({"to": S, "message": S}), 5), "ideas_bank": arr(S, 5)})
FIXED = obj({"post": obj({"hook": S, "text": S}), "self_comments": arr(S, 2), "changes": arr(S, 1)})


# ------------------------------------------------------------- defaults ----
SYSTEM = """You are the ghostwriting and growth team for $name ($headline).
Niche: $niche
Audience: $audience
Market: $market
What we offer: $offer
Proof points (the only facts you may claim as ours):
$proof_points
Real stories you may draw on:
$stories
Voice: $voice
Never use these phrases: $banned
Post date: $date ($weekday, $day_type day).
Already posted or banked (never repeat a hook or idea; do build on what performed):
$memory
Return only the structured output."""

RULES = """Structure that beats LinkedIn's 2026 ranking (profile-topic match, dwell time, saves, substantive comments):
- Line 1: hook, at most 7 words, a pain or a result, never a question.
- Lines 2 and 3: a personal story or observation. Use only the proof points and stories as facts about us. If nothing fits, write it as an observation ("A founder told me last week...") and never invent a client name or a metric.
- Body: 3 lessons or steps, numbered, one concrete detail each (a number, a tool, a situation).
- End: one question that invites argument or a story, plus one soft CTA (comment a word, DM, save).
- 600 to 1300 characters. One sentence per line. Blank line between blocks. No em-dashes. No external links. At most 3 hashtags at the end, or none. First person. Numbers over adjectives.
- self_comments: exactly 2, posted in the first 30 minutes: one adds a surprising detail, one asks the reader a question.
- carousel: $carousel_rule When included: title is a bold claim of at most 10 words, subtitle at most 12 words, cta at most 8 words, 5 to 8 slides, each slide title at most 8 words and body at most 40 words. When not included, return a carousel with an empty slides list."""

KIT_FLOW = {
    "name": "Daily kit", "kind": "kit",
    "data": {
        "description": "Research the niche, draft four candidate posts on different angles, judge them with three lenses, fact-check the winner, then write the creator comments, connection requests, DMs and idea hooks. Produces one kit for the next weekday.",
        "schedule": {"days": ["sun", "mon", "tue", "wed", "thu"], "time": "18:45"},
        "system": SYSTEM,
        "research": {
            "web_search": True, "max_uses": 5, "min_facts": 3,
            "angles": [
                {"key": "news", "prompt": "You are the Content Intelligence agent. Search the web (3 to 5 queries) for the newest news in our niche from the last 7 days: launches, numbers, reversals, funding, incidents. Return 5 bullets, each with a source URL, 3 real questions people are asking about this (forums, Reddit, Quora, LinkedIn), and 3 hook patterns you saw working."},
                {"key": "platform", "prompt": "You are the Content Intelligence agent. Search the web (3 queries) for what is changing on LinkedIn this month: algorithm updates, formats that get reach, hook styles that win, penalties. Return 5 bullets with source URLs, 3 questions creators ask, and 5 hook patterns currently working."},
                {"key": "market", "prompt": "You are the Content Intelligence agent. Search the web (3 to 4 queries) for the market angle on our niche for our audience and market this month: hiring, budgets, adoption numbers, regulation, customer complaints. Return 5 bullets with source URLs, 3 questions professionals are asking, and 3 hook patterns."},
                {"key": "creators", "prompt": "You are the Content Intelligence agent. For each of these creators, search the web for what they have posted or argued about recently:\n$creators\nReturn one bullet per creator formatted \"Creator name: current theme or take\" with the best source URL, 3 questions their audiences ask, and 3 hook patterns they use. If the list is empty, find 5 creators our audience follows in our niche and do the same for them."},
            ],
        },
        "draft": {
            "effort": "high", "rules": RULES,
            "prompt": "You are the Ghostwriter. Angle: $brief\n\nIntel (sourced facts, audience questions, hook patterns):\n$intel\n\n$rules\n\nWrite one post. Set angle=\"$angle\".",
            "angles": [
                {"key": "story", "brief": "Story-first: open on a specific moment, make the lesson earned, keep the data to one number."},
                {"key": "data", "brief": "Data-led: build the post around the single most surprising sourced number in the intel and what it means for the reader tomorrow morning."},
                {"key": "contrarian", "brief": "Contrarian: take the popular take in the intel and argue the opposite with evidence, respectfully, without strawmen."},
                {"key": "operator", "brief": "Operator checklist: the exact steps a practitioner would run this week, with the trap at each step."},
            ],
        },
        "judge": {
            "effort": "medium",
            "prompt": "You are a judge ($lens lens). $ask total = the sum of the scores you set. notes: two sentences naming the single biggest weakness.\n\nIntel for reference:\n$intel\n\nCandidate (angle $angle):\n$candidate",
            "lenses": [
                {"key": "hook-dwell", "ask": "Would a scrolling reader in our audience stop at line 1 and keep reading past line 3? Score hook (0-5) and dwell (0-5) on structure and rhythm. Set specificity and fit to 0."},
                {"key": "credibility", "ask": "Is every claim concrete, sourced in the intel or the proof points, and free of invented clients or numbers? Is the voice an operator, not a marketer? Score specificity (0-10). Set hook, dwell and fit to 0."},
                {"key": "audience-fit", "ask": "Does it fit our audience and market, match our niche so the ranking model trusts the author, and end with something people will actually comment on or save? Score fit (0-10). Set hook, dwell and specificity to 0."},
            ],
        },
        "verify": {
            "enabled": True, "effort": "medium",
            "prompt": "You are a fact-checker ($lens). Default to verified=false when uncertain. $ask\n\nIntel:\n$intel\n\nPost:\n$candidate",
            "refuters": [
                {"key": "numbers", "ask": "Attack every number, percentage, date and count in the post and carousel. For each, say whether the intel or the proof points support it exactly; if not, give the corrected wording or \"remove\"."},
                {"key": "claims", "ask": "Attack every claim about a company, person, product or event. For each, say whether the intel supports it; if not, give the corrected wording or \"remove\"."},
            ],
            "rewrite_prompt": "You are the Ghostwriter doing a correction pass. Apply these fixes exactly, keep the angle, structure, hook length and character budget, and change nothing else:\n$claims\n\n$rules\n\nPost to correct:\n$candidate",
        },
        "assemble": {
            "effort": "medium",
            "engagement_prompt": "You are the Engagement Engine. Write exactly 10 comments, one per creator below (if fewer are listed, add creators our audience follows, named), to be posted on their newest post 15 minutes before ours goes live. Each: 2 to 4 lines, adds an insight, a number from the intel, or a respectful counterpoint tied to that creator's current theme. Never open with praise. End with a question when natural.\nCreators:\n$creators\n\nIntel:\n$intel\n\nOur post today (for coherence):\n$post",
            "network_prompt": "You are the Network Builder. Return exactly 10 connection_requests (5 hiring managers or founders, 5 peers; describe who as \"title @ company type\", why they matter, and a message of at most 300 characters on the template \"Hey [Name], saw your post on [Topic]. The point about [Specific] hit me. Building in the same space, would love to connect.\" with [Specific] personalised each time), exactly 5 dms for people who engage with today's post (pure value, a checklist or a number, zero pitch), and exactly 5 ideas_bank hooks for future posts (at most 7 words each, contrarian or story-based, none already used).\n\nIntel:\n$intel\n\nOur post today:\n$post",
        },
        "fix_prompt": "The post below failed these quality gates:\n$violations\n\nFix ONLY formatting: whitespace, line breaks, syncing the hook with the first line, trimming over-length wording of your own, removing a stray link or hashtag. Never add, remove or change a number, name, claim, quote or source. Return the corrected post, the two self_comments, and one line per change you made.\n\n$rules\n\nPost:\n$candidate",
    },
}

PUBLISH_FLOW = {
    "name": "Go live", "kind": "publish",
    "data": {
        "description": "Publishes today's kit through the LinkedIn API (text plus carousel PDF), posts the first self-comment, and emails you the LIVE note. If the kit still needs approval or LinkedIn is not connected, it emails you the post to publish by hand instead.",
        "schedule": {"days": ["mon", "tue", "wed", "thu", "fri"], "time": "09:30"},
        "comment_after_publish": True,
    },
}

WEEKLY_FLOW = {
    "name": "Weekly analytics", "kind": "weekly",
    "data": {
        "description": "Reads the numbers you entered per post, kills post types that get under 1,000 impressions twice, doubles types averaging over 5,000, proposes next week's mix and emails the report.",
        "schedule": {"days": ["sun"], "time": "09:50"},
    },
}

DEFAULT_FLOWS = {"daily_kit": KIT_FLOW, "publish": PUBLISH_FLOW, "weekly": WEEKLY_FLOW}


# --------------------------------------------------------------- engine ----
class SafeDict(dict):
    def __missing__(self, k):
        return "$" + k


def tpl(template: str, ctx: dict) -> str:
    return string.Template(template or "").safe_substitute(SafeDict(ctx))


class Logger:
    def __init__(self, s, run: Run):
        self.s, self.run, self.entries = s, run, run.log

    def __call__(self, step: str, message: str, **data):
        self.entries.append({"t": utcnow().isoformat(timespec="seconds"), "step": step, "message": message, **({"data": data} if data else {})})
        self.run.log_json = json.dumps(self.entries, ensure_ascii=False)
        self.s.commit()


def bullets(items) -> str:
    return "\n".join(f"- {x}" for x in items if x) or "- (none given)"


def base_context(settings: dict, date: dt.date, day_type: str, memory: str) -> dict:
    p = settings["profile"]
    return {
        "name": p.get("name") or "the author", "headline": p.get("headline") or "", "niche": p.get("niche") or "(niche not set)",
        "audience": p.get("audience") or "(audience not set)", "market": p.get("market") or "global", "offer": p.get("offer") or "",
        "proof_points": bullets(p.get("proof_points") or []), "stories": bullets(p.get("stories") or []), "voice": p.get("voice") or "",
        "banned": ", ".join(p.get("banned_phrases") or []) or "(none)",
        "creators": bullets(f"{c.get('name')}: {c.get('topic', '')}" for c in p.get("target_creators") or []),
        "date": date.isoformat(), "weekday": date.strftime("%A"), "day_type": day_type, "memory": memory,
        "carousel_rule": "REQUIRED today." if day_type == "carousel" else "include only if the body is naturally a list.",
    }


async def gather_ok(coros, log, step):
    """Run coroutines concurrently; log failures; return the successes in order (failed ones as None)."""
    results = await asyncio.gather(*coros, return_exceptions=True)
    out = []
    for r in results:
        if isinstance(r, Exception):
            log(step, f"call failed: {r}")
            out.append(None)
        else:
            out.append(r)
    return out


def normalize_candidate(c: dict) -> dict:
    car = c.get("carousel") or {}
    slides = car.get("slides") or []
    c["carousel"] = car if 5 <= len(slides) <= 8 else None
    c["self_comments"] = (c.get("self_comments") or [])[:2]
    return c


async def run_kit(s, run: Run, fd: dict, settings: dict, log: Logger) -> None:
    p, content = settings["profile"], settings["content"]
    tz = ZoneInfo(p.get("timezone") or "UTC")
    today = dt.datetime.now(tz).date()
    params = run.params
    date = dt.date.fromisoformat(params["date"]) if params.get("date") else K.next_weekday(today)
    day_type = params.get("day_type") or K.day_type_for(content["weekly_mix"], date)
    run.kit_date = date.isoformat()
    existing = s.get(Kit, date.isoformat())
    if existing and not params.get("overwrite"):
        log("skip", f"a kit for {date} already exists (status {existing.status}); run again with overwrite to replace it")
        return
    llm = LLM(settings["llm"]["model"], settings["llm"]["effort"], settings["llm"].get("fallbacks", True))
    usage = Usage()

    def track(res):
        data, u = res
        usage.add(u)
        run.tokens_in, run.tokens_out, run.cost_usd = usage.tokens_in, usage.tokens_out, round(usage.cost_usd, 4)
        return data

    all_kits = [k.data for k in s.scalars(select(Kit))]
    metrics = {m.date: {"impressions": m.impressions, "comments": m.comments} for m in s.scalars(select(Metric))}
    ctx = base_context(settings, date, day_type, K.memory_text(all_kits, metrics))
    system = tpl(fd.get("system", SYSTEM), ctx)
    log("plan", f"kit for {date} ({ctx['weekday']}, {day_type}); model {llm.model}")

    # 1. research
    r = fd["research"]
    coros = [llm.structured(system=system, user=tpl(a["prompt"], ctx), schema=INTEL, web_search=r.get("web_search", True), max_uses=r.get("max_uses", 5)) for a in r["angles"]]
    found = await gather_ok(coros, log, "research")
    facts, questions, hooks = [], [], []
    for a, res in zip(r["angles"], found):
        if not res:
            continue
        data = track(res)
        facts += [b for b in data.get("bullets", []) if str(b.get("source", "")).startswith("http")]
        questions += data.get("questions", [])
        hooks += data.get("hooks", [])
        log("research", f"{a['key']}: {len(data.get('bullets', []))} bullets")
    if len(facts) < r.get("min_facts", 3):
        raise RuntimeError(f"research produced {len(facts)} sourced facts (need {r.get('min_facts', 3)}); not drafting on thin air")
    ctx["intel"] = json.dumps({"facts": facts, "questions": questions, "hook_patterns": hooks}, ensure_ascii=False, indent=1)
    log("research", f"{len(facts)} sourced facts, {len(questions)} questions, {len(hooks)} hook patterns")

    # 2. draft
    d = fd["draft"]
    ctx["rules"] = tpl(d["rules"], ctx)
    coros = [llm.structured(system=system, user=tpl(d["prompt"], {**ctx, "brief": a["brief"], "angle": a["key"]}), schema=CANDIDATE, effort=d.get("effort")) for a in d["angles"]]
    drafted = await gather_ok(coros, log, "draft")
    candidates = []
    for a, res in zip(d["angles"], drafted):
        if res:
            c = normalize_candidate(track(res))
            c["angle"] = a["key"]
            candidates.append(c)
            log("draft", f"{a['key']}: \"{c.get('hook', '')}\" ({len(c.get('text', ''))} chars{', carousel' if c.get('carousel') else ''})")
    if not candidates:
        raise RuntimeError("no candidate was drafted")

    # 3. judge
    j = fd["judge"]
    scored = []
    for c in candidates:
        coros = [llm.structured(system=system, user=tpl(j["prompt"], {**ctx, "lens": l["key"], "ask": l["ask"], "angle": c["angle"], "candidate": json.dumps(c, ensure_ascii=False, indent=1)}), schema=SCORE, effort=j.get("effort")) for l in j["lenses"]]
        votes = await gather_ok(coros, log, "judge")
        if any(v is None for v in votes):
            log("judge", f"{c['angle']}: dropped, a judge failed")
            continue
        votes = [track(v) for v in votes]
        total = sum(float(v.get("total") or 0) for v in votes)
        scored.append({"angle": c["angle"], "total": total, "notes": [v.get("notes", "") for v in votes], "candidate": c})
        log("judge", f"{c['angle']}: {total:g}", notes=[v.get("notes", "") for v in votes])
    if not scored:
        raise RuntimeError("no candidate survived judging")
    scored.sort(key=lambda x: -x["total"])
    winner = scored[0]["candidate"]
    log("judge", f"winner: {winner['angle']} ({scored[0]['total']:g}) over " + ", ".join(f"{x['angle']} {x['total']:g}" for x in scored[1:]))

    # 4. verify
    corrections = 0
    v = fd.get("verify") or {}
    if v.get("enabled", True):
        coros = [llm.structured(system=system, user=tpl(v["prompt"], {**ctx, "lens": rf["key"], "ask": rf["ask"], "candidate": json.dumps(winner, ensure_ascii=False, indent=1)}), schema=VERIFY, effort=v.get("effort")) for rf in v["refuters"]]
        verdicts = [track(x) for x in await gather_ok(coros, log, "verify") if x]
        bad = [c for vd in verdicts for c in vd.get("claims", []) if not c.get("verified")]
        corrections = len(bad)
        log("verify", f"{corrections} unsupported claim(s)", claims=bad[:20])
        if bad:
            res = await llm.structured(system=system, user=tpl(v["rewrite_prompt"], {**ctx, "claims": json.dumps(bad, ensure_ascii=False, indent=1), "candidate": json.dumps(winner, ensure_ascii=False, indent=1)}), schema=CANDIDATE, effort=d.get("effort"))
            fixed = normalize_candidate(track(res))
            fixed["angle"] = winner["angle"]
            winner = fixed
            log("verify", "correction pass applied")
    if day_type == "carousel" and not winner.get("carousel"):
        raise RuntimeError("carousel day but the winning post has no 5-8 slide carousel")

    # 5. assemble
    a = fd["assemble"]
    ctx["post"] = winner["text"]

    async def need(label, prompt, schema, counts):
        for attempt in (1, 2):
            res = await llm.structured(system=system, user=tpl(prompt, ctx) + ("" if attempt == 1 else "\n\nYour previous attempt returned too few items. Return exactly the counts asked for."), schema=schema, effort=a.get("effort"))
            data = track(res)
            short = [f"{k} {len(data.get(k) or [])}/{n}" for k, n in counts if len(data.get(k) or []) < n]
            if not short:
                return data
            log("assemble", f"{label}: short ({', '.join(short)}){'; retrying' if attempt == 1 else ''}")
        raise RuntimeError(f"{label} returned too few items after a retry")

    eng, net = await asyncio.gather(need("engagement", a["engagement_prompt"], ENGAGEMENT, [("engagement_comments", 10)]),
                                    need("network", a["network_prompt"], NETWORK, [("connection_requests", 10), ("dms", 5), ("ideas_bank", 5)]))
    kit = {"date": date.isoformat(), "day_type": day_type, "post": {"hook": winner["hook"], "text": winner["text"]},
           "self_comments": winner["self_comments"], "carousel": winner.get("carousel"),
           "engagement_comments": eng["engagement_comments"][:10], "connection_requests": net["connection_requests"][:10],
           "dms": net["dms"][:5], "ideas_bank": net["ideas_bank"][:5],
           "intel": [f"{b['fact']} — {b['source']}" for b in facts[:8]],
           "pipeline": {"winner": winner["angle"], "scores": [{"angle": x["angle"], "total": x["total"]} for x in scored], "corrections": corrections}}

    # 6. gate
    violations = K.lint(kit, p.get("banned_phrases") or [])
    if violations and fd.get("fix_prompt"):
        log("gate", f"{len(violations)} violation(s); formatting-only fix pass", violations=violations)
        res = await llm.structured(system=system, user=tpl(fd["fix_prompt"], {**ctx, "violations": "\n".join(violations), "candidate": json.dumps({"post": kit["post"], "self_comments": kit["self_comments"]}, ensure_ascii=False, indent=1)}), schema=FIXED, effort=a.get("effort"))
        fx = track(res)
        kit["post"], kit["self_comments"] = fx["post"], fx["self_comments"][:2]
        log("gate", "edits: " + "; ".join(fx.get("changes", [])))
        violations = K.lint(kit, p.get("banned_phrases") or [])
    kit["lint"] = violations
    log("gate", "lint ok" if not violations else f"still {len(violations)} violation(s); kit saved as draft for you to fix", violations=violations)

    row = existing or Kit(date=date.isoformat())
    row.day_type, row.data, row.run_id = day_type, kit, run.id
    row.status = "draft" if (violations or content.get("require_approval", True)) else "approved"
    s.add(row)
    s.commit()
    log("done", f"kit saved ({row.status}); cost ${usage.cost_usd:.3f}, {usage.tokens_in}/{usage.tokens_out} tokens")
    notify(settings, f"LinkedIn kit · {date} · {kit['post']['hook']}",
           f"<p>{'Approve it on the dashboard before the go-live run.' if row.status == 'draft' else 'Approved automatically; the go-live run will publish it.'}</p>" + K.brief_html(kit), log)


async def run_publish(s, run: Run, fd: dict, settings: dict, log: Logger) -> None:
    p, li = settings["profile"], settings.get("linkedin") or {}
    tz = ZoneInfo(p.get("timezone") or "UTC")
    date = run.params.get("date") or dt.datetime.now(tz).date().isoformat()
    run.kit_date = date
    kit = s.get(Kit, date)
    if not kit:
        log("skip", f"no kit for {date}; run the Daily kit flow first")
        return
    if kit.status == "posted":
        log("skip", f"{date} is already posted: {kit.post_urn}")
        return
    data = kit.data
    brief = K.brief_html(data)
    if settings["content"].get("require_approval", True) and kit.status != "approved":
        log("wait", f"kit {date} is {kit.status}; approve it on the dashboard to publish")
        notify(settings, f"APPROVE · {data['post']['hook']}", "<p>This kit is waiting for your approval on the dashboard. Approve it, then run Go live, or post it by hand:</p>" + brief, log)
        return
    connected = li.get("access_token") and li.get("person_urn") and (li.get("expires_at") or 0) > dt.datetime.now().timestamp()
    if not connected:
        log("manual", "LinkedIn not connected (or token expired); emailed the post to publish by hand")
        notify(settings, f"POST NOW · {data['post']['hook']}", "<p>Auto-publish is off: connect LinkedIn in Settings. Post this by hand:</p>" + brief, log)
        return
    pdf = render.carousel_pdf(data, settings["brand"]) if data.get("carousel") else None
    urn = await asyncio.to_thread(linkedin.publish, li["access_token"], li["person_urn"], data["post"]["text"], pdf, (data.get("carousel") or {}).get("title", ""))
    kit.post_urn, kit.status, kit.posted_at = urn, "posted", utcnow()
    s.commit()
    log("publish", f"posted {urn}" + (" with carousel PDF" if pdf else ""))
    if fd.get("comment_after_publish", True) and data.get("self_comments"):
        try:
            await asyncio.to_thread(linkedin.comment, li["access_token"], li["person_urn"], urn, data["self_comments"][0])
            log("publish", "first self-comment posted")
        except Exception as e:  # some apps lack comment permission; the email carries it
            log("publish", f"self-comment not posted ({e}); post it by hand")
    link = f"https://www.linkedin.com/feed/update/{urn}/"
    notify(settings, f"LIVE · {data['post']['hook']}",
           f"<p>Live: <a href='{html.escape(link)}'>{html.escape(link)}</a>. Reply to every comment in the first 60 minutes with a question, post the second self-comment, then send the 5 DMs.</p>" + brief, log)


async def run_weekly(s, run: Run, fd: dict, settings: dict, log: Logger) -> None:
    kits = {k.date: k for k in s.scalars(select(Kit))}
    rows = [{"date": m.date, "post_type": kits[m.date].day_type if m.date in kits else "?", "impressions": m.impressions} for m in s.scalars(select(Metric))]
    mix = settings["content"]["weekly_mix"]
    table, verdicts, proposed = K.weekly_report(rows, mix)
    log("report", f"{len(rows)} post(s) with numbers; verdicts {verdicts or 'none'}; proposed mix {proposed}")
    if proposed != mix and settings["content"].get("auto_apply_mix"):
        settings["content"]["weekly_mix"] = proposed
        save_settings(s, settings)
        log("apply", f"weekly mix updated to {proposed}")
    e = html.escape
    body = ["<h1 style='font:700 22px system-ui'>Weekly LinkedIn report</h1>"]
    if table:
        body.append("<table border=1 cellpadding=6 style='border-collapse:collapse;font:14px system-ui'><tr><th>type</th><th>posts</th><th>avg impressions</th><th>verdict</th></tr>" +
                    "".join(f"<tr><td>{e(r['post_type'])}</td><td>{r['posts']}</td><td>{r['avg']:,}</td><td>{e(r['verdict'])}</td></tr>" for r in table) + "</table>")
    else:
        body.append("<p>No numbers entered yet. Open each posted kit on the dashboard and enter impressions, reactions and comments; next Sunday this report grades every post type.</p>")
    body.append(f"<p>Current mix: {e(json.dumps(mix))}<br>Proposed mix: {e(json.dumps(proposed))}{' (applied)' if proposed != mix and settings['content'].get('auto_apply_mix') else ''}</p>")
    notify(settings, "LinkedIn weekly report", "\n".join(body), log)


def notify(settings: dict, subject: str, html_body: str, log: Logger) -> None:
    to = (settings.get("delivery") or {}).get("email_to")
    if not to or not mailer.configured():
        log("email", "email not configured (set SMTP_* env vars and an address in Settings); skipped: " + subject)
        return
    try:
        mailer.send(to, subject, html_body)
        log("email", f"sent to {to}: {subject}")
    except Exception as e:
        log("email", f"failed: {e}")


RUNNERS = {"kit": run_kit, "publish": run_publish, "weekly": run_weekly}


async def execute_run(run_id: int) -> None:
    with SessionLocal() as s:
        run = s.get(Run, run_id)
        flow = s.get(Flow, run.flow_slug)
        settings = get_settings(s)
        run.status, run.started_at = "running", utcnow()
        s.commit()
        log = Logger(s, run)
        try:
            if not flow:
                raise RuntimeError(f"flow {run.flow_slug} not found")
            await RUNNERS[flow.kind](s, run, flow.data, settings, log)
            run.status = "done"
        except (LLMError, linkedin.LinkedInError, RuntimeError, KeyError, ValueError) as e:
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"
            log("error", run.error)
            notify(settings, f"STUDIO FAILED · {flow.name if flow else run.flow_slug}", f"<pre>{html.escape(run.error)}</pre><p>Open the run on the dashboard for the full log.</p>", log)
        except Exception as e:  # programming errors: keep the trace in the log
            run.status, run.error = "failed", f"{type(e).__name__}: {e}"
            log("error", run.error, trace=traceback.format_exc()[-3000:])
        run.finished_at = utcnow()
        s.commit()
