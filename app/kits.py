"""Kit quality gates, the daily brief HTML, memory text and weekly verdicts. Pure functions, no I/O."""
import datetime as dt
import html
import re

MIN_CHARS, MAX_CHARS = 600, 1300
REQUIRED = {"engagement_comments": 10, "connection_requests": 10, "dms": 5, "ideas_bank": 5, "intel": 3}
KILL_BELOW, DOUBLE_ABOVE = 1000, 5000
DAY_TYPES = ["story", "carousel", "contrarian", "build_in_public", "debate"]
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def lint(c: dict, banned: list[str]) -> list[str]:
    """What LinkedIn's 2026 ranking rewards plus what the kit needs. One violation per line."""
    v = []
    for k in ("date", "day_type"):
        if not c.get(k):
            v.append(f"{k} is required")
    post = c.get("post") or {}
    text, hook = post.get("text", ""), post.get("hook", "")
    if not text or not hook:
        v.append("post.text and post.hook are required")
    if len(hook.split()) > 7:
        v.append(f"hook has {len(hook.split())} words (max 7)")
    if hook and text and not text.startswith(hook):
        v.append("post.text must start with post.hook")
    if "?" in hook:
        v.append("hook must not be a question")
    if not MIN_CHARS <= len(text) <= MAX_CHARS:
        v.append(f"post.text is {len(text)} chars (want {MIN_CHARS}-{MAX_CHARS})")
    if "—" in text or "—" in hook:
        v.append("no em-dashes in the post")
    if "http://" in text or "https://" in text:
        v.append("no external links in the post (reach penalty); put links in a comment")
    if len(re.findall(r"(?<!\w)#[A-Za-z]\w*", text)) > 3:
        v.append("max 3 hashtags")
    low = text.lower()
    v += [f"banned phrase: {b!r}" for b in banned if b and b.lower() in low]
    if len(c.get("self_comments") or []) < 2:
        v.append("need 2 self_comments")
    for k, n in REQUIRED.items():
        if len(c.get(k) or []) < n:
            v.append(f"{k}: {len(c.get(k) or [])} (need {n})")
    for k, keys in {"engagement_comments": ("creator", "comment"), "connection_requests": ("who", "message"), "dms": ("to", "message")}.items():
        for i, x in enumerate(c.get(k) or []):
            for kk in keys:
                if not isinstance(x, dict) or not x.get(kk):
                    v.append(f"{k}[{i}] missing {kk}")
    for i, x in enumerate(c.get("engagement_comments") or []):
        lines = [l for l in (x.get("comment") or "").split("\n") if l.strip()]
        if not 2 <= len(lines) <= 4:
            v.append(f"engagement_comments[{i}] has {len(lines)} lines (want 2-4)")
        if (x.get("comment") or "").lower().startswith(("great post", "nice post", "love this")):
            v.append(f"engagement_comments[{i}] opens with filler")
    for i, x in enumerate(c.get("connection_requests") or []):
        if len(x.get("message") or "") > 300:
            v.append(f"connection_requests[{i}] message > 300 chars")
    car = c.get("carousel")
    if car:
        if not car.get("title") or not car.get("slides"):
            v.append("carousel needs title and slides")
        if len((car.get("subtitle") or "").split()) > 12:
            v.append("carousel subtitle > 12 words")
        if len((car.get("cta") or "").split()) > 8:
            v.append("carousel cta > 8 words")
        if not 5 <= len(car.get("slides") or []) <= 8:
            v.append(f"carousel has {len(car.get('slides') or [])} slides (want 5-8)")
        if len((car.get("title") or "").split()) > 10:
            v.append("carousel title > 10 words")
        for i, sl in enumerate(car.get("slides") or []):
            if not sl.get("title"):
                v.append(f"slide {i + 1} missing title")
            if len((sl.get("title") or "").split()) > 8:
                v.append(f"slide {i + 1} title > 8 words")
            if len((sl.get("body") or "").split()) > 40:
                v.append(f"slide {i + 1} body > 40 words")
    for i in c.get("intel") or []:
        if "http" not in str(i):
            v.append(f"intel without source url: {str(i)[:50]!r}")
    return v


def brief_html(c: dict, pdf_url: str | None = None) -> str:
    e = html.escape
    sec = lambda t: f"<h2 style='margin:28px 0 8px;font:600 18px system-ui'>{t}</h2>"
    pre = lambda t: f"<pre style='white-space:pre-wrap;font:15px/1.5 system-ui;background:#f6f8fa;padding:14px;border-radius:8px'>{e(str(t))}</pre>"
    n = iter(range(1, 20))
    out = [f"<h1 style='font:700 22px system-ui'>LinkedIn kit · {e(c.get('date', ''))} · {e(c.get('day_type', ''))}</h1>",
           sec(f"{next(n)} · Post (copy, paste, publish)"), pre((c.get("post") or {}).get("text", ""))]
    car = c.get("carousel")
    if car:
        out += [sec(f"{next(n)} · Carousel (upload the PDF as a document with the post)"),
                f"<p><b>{e(car.get('title', ''))}</b> · {e(car.get('subtitle', ''))} · {len(car.get('slides') or []) + 2} slides</p>",
                "<ol>" + "".join(f"<li><b>{e(sl.get('title', ''))}</b> {e(sl.get('body', ''))}</li>" for sl in car.get("slides") or []) + f"<li>CTA: {e(car.get('cta', ''))}</li></ol>",
                f"<p>PDF: {e(pdf_url) if pdf_url else 'download it from the kit page'}</p>"]
    out += [sec(f"{next(n)} · Your 2 first-30-min comments (post one, keep one for a reply)")] + [pre(t) for t in c.get("self_comments") or []]
    out += [sec(f"{next(n)} · Engagement Engine: 10 comments on big creators (before you post)"),
            "<ol>" + "".join(f"<li><b>{e(x.get('creator', ''))}</b> · <i>{e(x.get('topic', ''))}</i>{pre(x.get('comment', ''))}</li>" for x in c.get("engagement_comments") or []) + "</ol>"]
    out += [sec(f"{next(n)} · Network Builder: 10 connection requests (evening)"),
            "<ol>" + "".join(f"<li><b>{e(x.get('who', ''))}</b> · {e(x.get('why', ''))}{pre(x.get('message', ''))}</li>" for x in c.get("connection_requests") or []) + "</ol>"]
    out += [sec(f"{next(n)} · 5 value DMs to people who engaged"),
            "<ol>" + "".join(f"<li><b>{e(x.get('to', ''))}</b>{pre(x.get('message', ''))}</li>" for x in c.get("dms") or []) + "</ol>"]
    if c.get("ideas_bank"):
        out += [sec("Idea bank (next posts)"), "<ul>" + "".join(f"<li>{e(i)}</li>" for i in c["ideas_bank"]) + "</ul>"]
    if c.get("intel"):
        out += [sec("Intel used"), "<ul>" + "".join(f"<li>{e(i)}</li>" for i in c["intel"]) + "</ul>"]
    return "\n".join(out)


def memory_text(kits: list[dict], metrics: dict[str, dict]) -> str:
    """What was already posted or banked, so writers never repeat and build on winners."""
    if not kits:
        return "(nothing posted yet)"
    lines = ["| date | type | hook | result |", "|---|---|---|---|"]
    banked = []
    for c in sorted(kits, key=lambda k: k.get("date", "")):
        m = metrics.get(c.get("date"), {})
        res = f"{m.get('impressions', '?')} impressions / {m.get('comments', '?')} comments" if m else "no numbers yet"
        lines.append(f"| {c.get('date')} | {c.get('day_type')} | {(c.get('post') or {}).get('hook', '')} | {res} |")
        banked += [(c.get("post") or {}).get("hook", "")] + [i for i in c.get("ideas_bank") or [] if isinstance(i, str)]
    lines += ["", "Hooks and ideas already used or banked (never repeat, do build on winners):"] + [f"- {b}" for b in banked if b]
    return "\n".join(lines)


def weekly_report(rows: list[dict], mix: dict) -> tuple[list[dict], dict, dict]:
    """rows: metric dicts with date, post_type, impressions. Returns (table, verdicts, proposed mix)."""
    by: dict[str, list[float]] = {}
    for r in sorted(rows, key=lambda r: r.get("date", "")):
        if r.get("impressions") is None:
            continue
        by.setdefault((r.get("post_type") or "?").strip(), []).append(float(r["impressions"]))
    table, verdicts = [], {}
    for t, v in sorted(by.items()):
        avg = sum(v) / len(v)
        lows = sum(1 for x in v[-4:] if x < KILL_BELOW)
        verdict = "KILL (<1000 twice)" if lows >= 2 else ("DOUBLE (>5k)" if avg > DOUBLE_ABOVE else "keep")
        verdicts[t] = verdict
        table.append({"post_type": t, "posts": len(v), "avg": round(avg), "verdict": verdict})
    proposed = dict(mix)
    avg = {t: sum(v) / len(v) for t, v in by.items()}
    killed = {t for t, v in verdicts.items() if v.startswith("KILL")}
    best = max(avg, key=avg.get) if avg else None
    for day, t in proposed.items():
        if t in killed and best and best not in killed:
            proposed[day] = best
    for d in [t for t, v in verdicts.items() if v.startswith("DOUBLE")]:
        if list(proposed.values()).count(d) < 2:
            slot = min((day for day, t in proposed.items() if t != d), key=lambda day: avg.get(proposed[day], 0), default=None)
            if slot:
                proposed[slot] = d
    return table, verdicts, proposed


def next_weekday(today: dt.date) -> dt.date:
    d = today + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def day_type_for(mix: dict, date: dt.date) -> str:
    return mix.get(WEEKDAYS[date.weekday()], "story")
