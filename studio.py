#!/usr/bin/env python3
"""LinkedIn Studio CLI: render carousels, publish posts, weekly metrics, email brief.

  python studio.py render  content/2026-10-17.json   # -> content/2026-10-17.pdf (1080x1350 slides)
  python studio.py publish content/2026-10-17.json   # -> posts text (+PDF) via LinkedIn API, prints post URN
  python studio.py comment <post-urn> "text"         # -> comments on a post as you
  python studio.py brief   content/2026-10-17.json   # -> HTML email body on stdout
  python studio.py metrics                           # -> weekly report from data/metrics.csv
  python studio.py auth                              # -> one-time LinkedIn OAuth, writes .env
  python studio.py lint    content/2026-10-17.json   # -> quality gates (hook, length, links, counts); exit 1 on violation
  python studio.py run     content/2026-10-17.json   # -> render + lint + memory, the daily one-shot
  python studio.py memory                            # -> data/memory.md from all kits + metrics (anti-repetition)
  python studio.py doctor                            # -> preflight: deps, fonts, LinkedIn token/expiry, git push

Deps: Pillow, PyYAML. Everything else is stdlib.
"""
import csv, json, os, re, sys, html, datetime as dt, secrets, urllib.request, urllib.parse, urllib.error, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.linkedin.com"
SCOPES = "openid profile w_member_social"
REDIRECT = "http://localhost:8765/callback"


def _load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip().removeprefix("export ").strip(), v.strip().strip("'\""))


def _cfg():
    import yaml
    return yaml.safe_load((ROOT / "config.yaml").read_text())


# ---------------------------------------------------------------- render ----
W, H = 1080, 1350
FONT_DIRS = ["/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/dejavu", str(ROOT / "assets")]


def _font(size, bold=True):
    from PIL import ImageFont
    names = (["Inter-Bold.otf", "Inter-ExtraBold.otf", "DejaVuSans-Bold.ttf"] if bold
             else ["Inter-Regular.otf", "Inter-Medium.otf", "DejaVuSans.ttf"])
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def _wrap(font, text, max_w):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if font.getlength(trial) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def _fit(text, max_w, max_h, start, bold=True, min_size=28):
    """Largest font size at which `text` wraps inside max_w x max_h."""
    size = start
    while size > min_size:
        f = _font(size, bold)
        lines = _wrap(f, text, max_w)
        if len(lines) * size * 1.2 <= max_h and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = _font(min_size, bold)
    return f, _wrap(f, text, max_w)


def _slide(bg, fg, accent):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 14], fill=accent)
    return img, d


def _face(img, size=220, pos=None):
    from PIL import Image, ImageDraw
    p = ROOT / "assets" / "face.png"
    if not p.exists():
        return
    face = Image.open(p).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    img.paste(face, pos or (W - size - 80, H - size - 80), mask)


def render(path):
    """content JSON -> PDF carousel next to it. Slide 1 = bold claim, last = CTA."""
    c = json.loads(Path(path).read_text())
    car = c.get("carousel")
    if not car:
        print("no carousel in", path)
        return None
    cfg = _cfg()
    b = cfg.get("brand", {})
    bg, fg, accent, muted = b.get("bg", "#0B1220"), b.get("fg", "#FFFFFF"), b.get("accent", "#22D3EE"), b.get("muted", "#94A3B8")
    handle = cfg.get("name", "")
    M = 90  # margin
    pages = []

    def footer(d, i, n):
        d.text((M, H - 70), handle, font=_font(28, False), fill=muted)
        d.text((W - M - 120, H - 70), f"{i}/{n}", font=_font(28, False), fill=muted)

    slides = car["slides"]
    n = len(slides) + 2
    # cover
    img, d = _slide(bg, fg, accent)
    f, lines = _fit(car["title"], W - 2 * M, 700, 112)
    y = 260
    for l in lines:
        d.text((M, y), l, font=f, fill=fg)
        y += f.size * 1.2
    f2, sub = _fit(car.get("subtitle", "Swipe →"), W - 2 * M, 160, 40, bold=False, min_size=28)
    y += 40
    for l in sub:
        d.text((M, y), l, font=f2, fill=accent)
        y += f2.size * 1.2
    _face(img, 260)
    footer(d, 1, n)
    pages.append(img)
    # body
    for i, s in enumerate(slides, 2):
        img, d = _slide(bg, fg, accent)
        d.text((M, 120), f"{i - 1:02d}", font=_font(64), fill=accent)
        f, lines = _fit(s["title"], W - 2 * M, 360, 76)
        y = 230
        for l in lines:
            d.text((M, y), l, font=f, fill=fg)
            y += f.size * 1.2
        f2, lines2 = _fit(s.get("body", ""), W - 2 * M, 560, 44, bold=False, min_size=30)
        y += 40
        for l in lines2:
            d.text((M, y), l, font=f2, fill=muted)
            y += f2.size * 1.35
        footer(d, i, n)
        pages.append(img)
    # CTA
    img, d = _slide(accent, bg, bg)
    f, lines = _fit(car.get("cta", "Follow for more."), W - 2 * M, 600, 96)
    y = 300
    for l in lines:
        d.text((M, y), l, font=f, fill=bg)
        y += f.size * 1.2
    d.text((M, y + 40), handle, font=_font(40, False), fill=bg)
    _face(img, 260)
    footer(d, n, n)
    pages.append(img)

    out = Path(path).with_suffix(".pdf")
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=96, quality=90)  # JPEG pages, ~25KB each
    print(out)
    return out


# --------------------------------------------------------------- linkedin ----
LTF_SPECIAL = r"\()[]{}<>@#*_~|"


def ltf_escape(text):
    """Posts API 'commentary' is Little Text Format: these chars must be backslash-escaped."""
    s = "".join("\\" + ch if ch in LTF_SPECIAL else ch for ch in text)
    return re.sub(r"\\#(\w+)", r"{hashtag|\\#|\1}", s)


def _headers(extra=None):
    h = {"Authorization": "Bearer " + os.environ["LINKEDIN_ACCESS_TOKEN"],
         "LinkedIn-Version": os.environ.get("LINKEDIN_VERSION", "202608"),
         "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def _call(method, url, body=None, headers=None, raw=None, tries=4):
    """HTTP with retry on 429/5xx (2,4,8s). 401 => token expired. Exits with the LinkedIn error body otherwise."""
    import time
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or _headers())
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                txt = r.read().decode() or "{}"
                return (json.loads(txt) if txt.strip().startswith("{") else txt), r.headers
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:2000]
            if e.code == 401:
                sys.exit("LinkedIn 401: token expired or invalid. Run `python studio.py auth` and update the secret.")
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** (attempt + 1)); continue
            sys.exit(f"LinkedIn {e.code} on {method} {url}\n{msg}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < tries - 1:
                time.sleep(2 ** (attempt + 1)); continue
            sys.exit(f"LinkedIn unreachable ({e}). In the cloud env allow api.linkedin.com under Network access.")


def publish(path, dry=False):
    """Post the kit (text + carousel PDF). Idempotent per date via data/posted.jsonl. --dry makes no network calls."""
    _load_env()
    c = json.loads(Path(path).read_text())
    log = ROOT / "data" / "posted.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            e = json.loads(line) if line.strip() else {}
            if e.get("date") == c.get("date") and e.get("urn"):
                sys.exit(f"already posted {c.get('date')}: {e['urn']}")
    author = os.environ.get("LINKEDIN_PERSON_URN", "urn:li:person:DRY" if dry else None) or os.environ["LINKEDIN_PERSON_URN"]
    body = {"author": author, "commentary": ltf_escape(c["post"]["text"]), "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    pdf = Path(path).with_suffix(".pdf")
    if c.get("carousel"):
        if not pdf.exists():
            sys.exit(f"{pdf} missing; run `python studio.py render {path}` first")
        title = c["carousel"]["title"][:100]
        if dry:
            body["content"] = {"media": {"title": title, "id": f"urn:li:document:DRY (would upload {pdf.name})"}}
        else:
            init, _ = _call("POST", f"{API}/rest/documents?action=initializeUpload",
                            {"initializeUploadRequest": {"owner": author}})
            v = init["value"]
            _call("PUT", v["uploadUrl"], raw=pdf.read_bytes(),
                  headers={"Authorization": "Bearer " + os.environ["LINKEDIN_ACCESS_TOKEN"],
                           "Content-Type": "application/pdf"})
            body["content"] = {"media": {"title": title, "id": v["document"]}}
    if dry:
        print(json.dumps(body, indent=2))
        return None
    _, hdrs = _call("POST", f"{API}/rest/posts", body, tries=1)  # never auto-retry a create: a lost response must not double-post
    urn = hdrs.get("x-restli-id")
    if not urn:
        sys.exit("post created but no x-restli-id header; check LinkedIn before retrying")
    with open(log, "a") as f:
        f.write(json.dumps({"date": c.get("date"), "urn": urn, "type": c.get("day_type"),
                            "ts": dt.datetime.now(dt.UTC).isoformat()}) + "\n")
    print(urn)
    return urn


def comment(urn, text):
    """Comment as you on a post (works on your own posts with w_member_social)."""
    _load_env()
    author = os.environ["LINKEDIN_PERSON_URN"]
    url = f"{API}/rest/socialActions/{urllib.parse.quote(urn, safe='')}/comments"
    out, _ = _call("POST", url, {"actor": author, "message": {"text": text}})
    print(out if isinstance(out, str) else out.get("$URN", out))


def auth():
    """One-time OAuth: opens browser, catches the callback on localhost:8765, writes .env."""
    _load_env()
    cid, sec = os.environ["LINKEDIN_CLIENT_ID"], os.environ["LINKEDIN_CLIENT_SECRET"]
    state = secrets.token_hex(8)
    url = "https://www.linkedin.com/oauth/v2/authorization?" + urllib.parse.urlencode(
        {"response_type": "code", "client_id": cid, "redirect_uri": REDIRECT, "state": state, "scope": SCOPES})
    print("Open this URL if the browser did not:\n", url)
    webbrowser.open(url)
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Done. You can close this tab.")

        def log_message(self, *a):
            pass

    HTTPServer(("localhost", 8765), H).handle_request()
    assert got.get("state") == state and "code" in got, f"bad callback: {got}"
    tok, _ = _call("POST", "https://www.linkedin.com/oauth/v2/accessToken",
                   raw=urllib.parse.urlencode({"grant_type": "authorization_code", "code": got["code"],
                                               "redirect_uri": REDIRECT, "client_id": cid,
                                               "client_secret": sec}).encode(),
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
    me, _ = _call("GET", f"{API}/v2/userinfo", headers={"Authorization": "Bearer " + tok["access_token"]})
    exp = (dt.date.today() + dt.timedelta(seconds=tok.get("expires_in", 0))).isoformat()
    env = (ROOT / ".env")
    keep = [l for l in env.read_text().splitlines() if not l.startswith(("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_PERSON_URN", "LINKEDIN_TOKEN_EXPIRES"))] if env.exists() else []
    keep += [f"LINKEDIN_ACCESS_TOKEN={tok['access_token']}", f"LINKEDIN_PERSON_URN=urn:li:person:{me['sub']}",
             f"LINKEDIN_TOKEN_EXPIRES={exp}"]
    env.write_text("\n".join(keep) + "\n")
    print(f"OK: {me.get('name')} -> urn:li:person:{me['sub']}  (token expires {exp}; re-run `auth` before then)")
    print("Copy LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN from .env into your cloud environment secrets.")


# ------------------------------------------------------------------ brief ----
def brief(path):
    """content JSON -> HTML email body (the daily kit)."""
    c = json.loads(Path(path).read_text())
    e = html.escape
    sec = lambda t: f"<h2 style='margin:28px 0 8px;font:600 18px system-ui'>{t}</h2>"
    pre = lambda t: f"<pre style='white-space:pre-wrap;font:15px/1.5 system-ui;background:#f6f8fa;padding:14px;border-radius:8px'>{e(t)}</pre>"
    counter = iter(range(1, 20))
    n = lambda: next(counter)
    out = [f"<h1 style='font:700 22px system-ui'>LinkedIn kit · {e(c['date'])} · {e(c.get('day_type', ''))}</h1>"]
    out += [sec(f"{n()} · Post (copy, paste, publish)"), pre(c["post"]["text"])]
    if c.get("carousel"):
        car = c["carousel"]
        out += [sec(f"{n()} · Carousel (upload the PDF as a document with the post)"),
                f"<p><b>{e(car['title'])}</b> · {e(car.get('subtitle', ''))} · {len(car['slides']) + 2} slides</p>",
                "<ol>" + "".join(f"<li><b>{e(sl['title'])}</b> {e(sl.get('body', ''))}</li>" for sl in car["slides"]) + f"<li>CTA: {e(car.get('cta', ''))}</li></ol>",
                f"<p>PDF: {e(c.get('pdf_url') or 'not rendered yet: run  python studio.py run  on this kit')}</p>"]
    out += [sec(f"{n()} · Your 2 first-30-min comments (post one, keep one for a reply)")]
    out += [pre(t) for t in c.get("self_comments", [])]
    out += [sec(f"{n()} · Engagement Engine: 10 comments on big creators (before you post)")]
    out += ["<ol>" + "".join(f"<li><b>{e(x['creator'])}</b> · <i>{e(x.get('topic', ''))}</i>{pre(x['comment'])}</li>" for x in c.get("engagement_comments", [])) + "</ol>"]
    out += [sec(f"{n()} · Network Builder: 10 connection requests (evening)")]
    out += ["<ol>" + "".join(f"<li><b>{e(x['who'])}</b> · {e(x.get('why', ''))}{pre(x['message'])}</li>" for x in c.get("connection_requests", [])) + "</ol>"]
    out += [sec(f"{n()} · 5 value DMs to people who engaged")]
    out += ["<ol>" + "".join(f"<li><b>{e(x['to'])}</b>{pre(x['message'])}</li>" for x in c.get("dms", [])) + "</ol>"]
    if c.get("ideas_bank"):
        out += [sec("Idea bank (next posts)"), "<ul>" + "".join(f"<li>{e(i)}</li>" for i in c["ideas_bank"]) + "</ul>"]
    if c.get("intel"):
        out += [sec("Intel used"), "<ul>" + "".join(f"<li>{e(i)}</li>" for i in c["intel"]) + "</ul>"]
    s = "\n".join(out)
    print(s)
    return s


# ---------------------------------------------------------------- metrics ----
KILL_BELOW, DOUBLE_ABOVE = 1000, 5000


def metrics(csv_path=None):
    """Weekly report + kill/double rules from data/metrics.csv."""
    p = Path(csv_path or ROOT / "data" / "metrics.csv")
    rows = sorted((r for r in csv.DictReader(p.open()) if (r.get("impressions") or "").strip()), key=lambda r: r.get("date", ""))
    num = lambda r, k: float((r.get(k) or "0").replace(",", "").strip() or 0)
    by = {}
    for r in rows:
        by.setdefault((r.get("post_type") or "?").strip(), []).append(num(r, "impressions"))
    lines = [f"# Weekly LinkedIn report ({dt.date.today()})", "", "| post_type | posts | avg impressions | verdict |", "|---|---|---|---|"]
    verdicts = {}
    for t, v in sorted(by.items()):
        avg = sum(v) / len(v)
        lows = sum(1 for x in v[-4:] if x < KILL_BELOW)
        verdict = "KILL (<1000 twice)" if lows >= 2 else ("DOUBLE (>5k)" if avg > DOUBLE_ABOVE else "keep")
        verdicts[t] = verdict
        lines.append(f"| {t} | {len(v)} | {avg:,.0f} | {verdict} |")
    last7 = [r for r in rows if r["date"] >= (dt.date.today() - dt.timedelta(days=7)).isoformat()]
    if last7:
        tot = lambda k: sum(num(r, k) for r in last7)
        acc = tot("connections_accepted") / tot("connections_sent") * 100 if tot("connections_sent") else 0
        lines += ["", f"Last 7 days: {len(last7)} posts · {tot('impressions'):,.0f} impressions · {tot('profile_views'):,.0f} profile views · "
                  f"{tot('connections_sent'):.0f} invites sent, {acc:.0f}% accepted · {tot('leads'):.0f} leads/DMs"]
    # proposed weekly mix: a doubled type takes a killed type's day, else the lowest-average day; killed days get the best type
    mix = dict(_cfg().get("weekly_mix", {}))
    avg = {t: sum(v) / len(v) for t, v in by.items()}
    killed = {t for t, v in verdicts.items() if v.startswith("KILL")}
    doubled = [t for t, v in verdicts.items() if v.startswith("DOUBLE")]
    best = max(avg, key=avg.get) if avg else None
    for day, t in mix.items():
        if t in killed and best and best not in killed:
            mix[day] = best
    for d in doubled:
        if list(mix.values()).count(d) < 2:
            slot = min((day for day, t in mix.items() if t != d), key=lambda day: avg.get(mix[day], 0), default=None)
            if slot:
                mix[slot] = d
    lines += ["", f"proposed weekly_mix: {mix}"]
    s = "\n".join(lines)
    print(s)
    return verdicts


# ------------------------------------------------------------------- lint ----
REQUIRED = {"engagement_comments": 10, "connection_requests": 10, "dms": 5, "ideas_bank": 5, "intel": 3}
MIN_CHARS, MAX_CHARS = 600, 1300


def lint(path):
    """Quality gates for a kit JSON. Prints one violation per line; exit 1 if any. These encode what LinkedIn's
    2026 ranking rewards (hook, dwell, specificity, no external links) and what the kit email needs."""
    c = json.loads(Path(path).read_text())
    cfg = _cfg()
    v = []
    for k in ("date", "day_type"):
        if not c.get(k):
            v.append(f"{k} is required")
    for k, keys in {"engagement_comments": ("creator", "comment"), "connection_requests": ("who", "message"), "dms": ("to", "message")}.items():
        for i, x in enumerate(c.get(k, [])):
            for kk in keys:
                if not isinstance(x, dict) or not x.get(kk):
                    v.append(f"{k}[{i}] missing {kk}")
    post = c.get("post", {})
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
    if "\u2014" in text or "\u2014" in hook:
        v.append("no em-dashes in the post")
    if "http://" in text or "https://" in text:
        v.append("no external links in the post (reach penalty); put links in a comment")
    if len(re.findall(r"(?<!\w)#[A-Za-z]\w*", text)) > 3:
        v.append("max 3 hashtags")
    low = text.lower()
    for b in cfg.get("banned_phrases", []):
        if b.lower() in low:
            v.append(f"banned phrase: {b!r}")
    if len(c.get("self_comments", [])) < 2:
        v.append("need 2 self_comments")
    for k, n in REQUIRED.items():
        if len(c.get(k, [])) < n:
            v.append(f"{k}: {len(c.get(k, []))} (need {n})")
    for i, x in enumerate(c.get("engagement_comments", [])):
        lines = [l for l in x.get("comment", "").split("\n") if l.strip()]
        if not 2 <= len(lines) <= 4:
            v.append(f"engagement_comments[{i}] has {len(lines)} lines (want 2-4)")
        if x.get("comment", "").lower().startswith(("great post", "nice post", "love this")):
            v.append(f"engagement_comments[{i}] opens with filler")
    for i, x in enumerate(c.get("connection_requests", [])):
        if len(x.get("message", "")) > 300:
            v.append(f"connection_requests[{i}] message > 300 chars")
    car = c.get("carousel")
    if car:
        if not car.get("title") or not car.get("slides"):
            v.append("carousel needs title and slides")
        if len(car.get("subtitle", "").split()) > 12:
            v.append("carousel subtitle > 12 words")
        if len(car.get("cta", "").split()) > 8:
            v.append("carousel cta > 8 words")
        if not 5 <= len(car.get("slides", [])) <= 8:
            v.append(f"carousel has {len(car.get('slides', []))} slides (want 5-8)")
        if len(car.get("title", "").split()) > 10:
            v.append("carousel title > 10 words")
        for i, sl in enumerate(car.get("slides", [])):
            if not sl.get("title"):
                v.append(f"slide {i + 1} missing title")
            if len(sl.get("title", "").split()) > 8:
                v.append(f"slide {i + 1} title > 8 words")
            if len(sl.get("body", "").split()) > 40:
                v.append(f"slide {i + 1} body > 40 words")
    for i in c.get("intel", []):
        if "http" not in i:
            v.append(f"intel without source url: {i[:50]!r}")
    print("\n".join(v) if v else f"lint ok: {path}")
    return v


def memory():
    """Rebuild data/memory.md from every kit + metrics so the writers never repeat themselves and build on winners."""
    rows = {}
    mp = ROOT / "data" / "metrics.csv"
    if mp.exists():
        for r in csv.DictReader(mp.open()):
            rows[r["date"]] = r
    lines = ["# Studio memory (auto-generated by `python studio.py memory`; do not edit)", "",
             "| date | type | hook | carousel | result |", "|---|---|---|---|---|"]
    topics = []
    for p in sorted((ROOT / "content").glob("2*.json")):
        try:
            c = json.loads(p.read_text())
            hook = (c.get("post") or {}).get("hook", "")
        except (json.JSONDecodeError, AttributeError) as e:
            print(f"memory: skipping {p.name}: {e}", file=sys.stderr)
            continue
        m = rows.get(c.get("date"), {})
        res = f"{m.get('impressions', '?')} imp / {m.get('comments', '?')} cmts" if m else "not posted yet"
        car = (c.get("carousel") or {}).get("title", "")
        lines.append(f"| {c.get('date')} | {c.get('day_type')} | {hook} | {car} | {res} |")
        topics += [hook] + [i for i in c.get("ideas_bank", []) if isinstance(i, str)]
    lines += ["", "## Hooks and ideas already used or banked (do not repeat, do build on winners)", ""] + [f"- {t}" for t in topics if t]
    out = ROOT / "data" / "memory.md"
    out.write_text("\n".join(lines) + "\n")
    print(out)
    return out


def doctor():
    """Preflight. FAIL (exit 1) only for deps and fonts; LinkedIn and git problems are WARN because a manual path exists."""
    _load_env()
    hard, warn = [], []
    try:
        import PIL, yaml  # noqa
    except ImportError as e:
        hard.append(f"missing dependency: {e}; run pip install -r requirements.txt")
    if not any((Path(d) / n).exists() for d in FONT_DIRS for n in ("Inter-Bold.otf", "DejaVuSans-Bold.ttf")):
        hard.append("no TTF/OTF font found; copy Inter-Bold.otf into assets/")
    tok, urn, exp = os.environ.get("LINKEDIN_ACCESS_TOKEN"), os.environ.get("LINKEDIN_PERSON_URN"), os.environ.get("LINKEDIN_TOKEN_EXPIRES")
    if not (tok and urn):
        warn.append("LinkedIn not connected: auto-publish off (README > Connect LinkedIn)")
    else:
        if exp:
            try:
                days = (dt.date.fromisoformat(exp[:10]) - dt.date.today()).days
                if days <= 7:
                    warn.append(f"LinkedIn token expires in {days} days ({exp}); run `python studio.py auth` and update the secret")
            except ValueError:
                warn.append(f"LINKEDIN_TOKEN_EXPIRES={exp!r} is not a date")
        else:
            warn.append("LINKEDIN_TOKEN_EXPIRES not set: expiry unknown (copy it from .env)")
        ver = os.environ.get("LINKEDIN_VERSION", "202608")
        lo, hi = (dt.date.today() - dt.timedelta(days=365)).strftime("%Y%m"), dt.date.today().strftime("%Y%m")
        if not lo <= ver <= hi:
            warn.append(f"LINKEDIN_VERSION {ver} is outside the 12-month window LinkedIn keeps live ({lo}..{hi})")
        try:
            urllib.request.urlopen(urllib.request.Request(f"{API}/v2/userinfo", headers={"Authorization": "Bearer " + tok}), timeout=8)
        except urllib.error.HTTPError as e:
            warn.append(f"LinkedIn API answered {e.code}" + (": token expired, run `python studio.py auth`" if e.code == 401 else ""))
        except Exception as e:
            warn.append(f"api.linkedin.com unreachable from here ({type(e).__name__}); allow it under Network access")
    import subprocess
    r = subprocess.run(["git", "push", "--dry-run", "origin", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode:
        warn.append("git push refused: " + (r.stderr.strip().splitlines() or ["?"])[-1][:120])
    for x in hard:
        print("FAIL", x)
    for x in warn:
        print("WARN", x)
    print("doctor:", "FAIL" if hard else "ok", f"({len(warn)} warnings)")
    return not hard


def run(path):
    """lint -> (set pdf_url, render) -> memory. The one command the daily run calls. Exit 1 before touching anything if lint fails."""
    if lint(path):
        sys.exit(1)
    c = json.loads(Path(path).read_text())
    if c.get("carousel"):
        base = _cfg().get("pdf_base_url", "").rstrip("/")
        if base:
            c["pdf_url"] = f"{base}/{Path(path).stem}.pdf"
            Path(path).write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
        render(path)
    memory()


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:] or ["help"]
    flags = {a for a in rest if a.startswith("--")}
    args = [a for a in rest if not a.startswith("--")]
    need = {"render": 1, "publish": 1, "comment": 2, "brief": 1, "lint": 1, "run": 1}
    cmds = {"render": lambda: render(args[0]), "publish": lambda: publish(args[0], dry="--dry" in flags),
            "comment": lambda: comment(args[0], args[1]), "brief": lambda: brief(args[0]),
            "metrics": lambda: metrics(*args[:1]), "auth": auth, "lint": lambda: sys.exit(1 if lint(args[0]) else 0),
            "memory": memory, "doctor": lambda: sys.exit(0 if doctor() else 1), "run": lambda: run(args[0])}
    if cmd not in cmds:
        print(__doc__)
        sys.exit(0 if cmd == "help" else 2)
    if len(args) < need.get(cmd, 0):
        sys.exit(f"usage: studio.py {cmd} needs {need[cmd]} argument(s)")
    cmds[cmd]()
