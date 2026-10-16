#!/usr/bin/env python3
"""LinkedIn Studio CLI: render carousels, publish posts, weekly metrics, email brief.

  python studio.py render  content/2026-10-17.json   # -> content/2026-10-17.pdf (1080x1350 slides)
  python studio.py publish content/2026-10-17.json   # -> posts text (+PDF) via LinkedIn API, prints post URN
  python studio.py comment <post-urn> "text"         # -> comments on a post as you
  python studio.py brief   content/2026-10-17.json   # -> HTML email body on stdout
  python studio.py metrics                           # -> weekly report from data/metrics.csv
  python studio.py auth                              # -> one-time LinkedIn OAuth, writes .env

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
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


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
    d.text((M, y + 40), car.get("subtitle", "Swipe →"), font=_font(40, False), fill=accent)
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
    return "".join("\\" + ch if ch in LTF_SPECIAL else ch for ch in text)


def _headers(extra=None):
    h = {"Authorization": "Bearer " + os.environ["LINKEDIN_ACCESS_TOKEN"],
         "LinkedIn-Version": os.environ.get("LINKEDIN_VERSION", "202608"),
         "X-Restli-Protocol-Version": "2.0.0", "Content-Type": "application/json"}
    h.update(extra or {})
    return h


def _call(method, url, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or _headers())
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode() or "{}"
            return (json.loads(txt) if txt.strip().startswith("{") else txt), dict(r.headers)
    except urllib.error.HTTPError as e:
        sys.exit(f"LinkedIn {e.code} on {method} {url}\n{e.read().decode()[:2000]}")


def publish(path, dry=False):
    _load_env()
    c = json.loads(Path(path).read_text())
    author = os.environ["LINKEDIN_PERSON_URN"]
    body = {"author": author, "commentary": ltf_escape(c["post"]["text"]), "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
    pdf = Path(path).with_suffix(".pdf")
    if c.get("carousel") and pdf.exists():
        init, _ = _call("POST", f"{API}/rest/documents?action=initializeUpload",
                        {"initializeUploadRequest": {"owner": author}})
        v = init["value"]
        _call("PUT", v["uploadUrl"], raw=pdf.read_bytes(),
              headers={"Authorization": "Bearer " + os.environ["LINKEDIN_ACCESS_TOKEN"],
                       "Content-Type": "application/pdf"})
        body["content"] = {"media": {"title": c["carousel"]["title"][:100], "id": v["document"]}}
    if dry:
        print(json.dumps(body, indent=2))
        return None
    _, hdrs = _call("POST", f"{API}/rest/posts", body)
    urn = hdrs.get("x-restli-id") or hdrs.get("X-RestLi-Id")
    with open(ROOT / "data" / "posted.jsonl", "a") as f:
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
    out = [f"<h1 style='font:700 22px system-ui'>LinkedIn kit · {e(c['date'])} · {e(c.get('day_type', ''))}</h1>"]
    out += [sec("1 · Post (copy, paste, publish)"), pre(c["post"]["text"])]
    if c.get("carousel"):
        out += [sec("2 · Carousel"), f"<p>PDF attached: <b>{e(c['carousel']['title'])}</b> ({len(c['carousel']['slides']) + 2} slides). Upload it as a document with the post.</p>"]
    out += [sec("3 · Your 2 first-30-min comments (post one, keep one for a reply)")]
    out += [pre(t) for t in c.get("self_comments", [])]
    out += [sec("4 · Engagement Engine: 10 comments on big creators (before you post)")]
    out += ["<ol>" + "".join(f"<li><b>{e(x['creator'])}</b> · <i>{e(x.get('topic', ''))}</i>{pre(x['comment'])}</li>" for x in c.get("engagement_comments", [])) + "</ol>"]
    out += [sec("5 · Network Builder: 10 connection requests (evening)")]
    out += ["<ol>" + "".join(f"<li><b>{e(x['who'])}</b> · {e(x.get('why', ''))}{pre(x['message'])}</li>" for x in c.get("connection_requests", [])) + "</ol>"]
    out += [sec("6 · 5 value DMs to people who engaged")]
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
    rows = [r for r in csv.DictReader(p.open()) if r.get("impressions")]
    num = lambda r, k: float(r.get(k) or 0)
    by = {}
    for r in rows:
        by.setdefault(r["post_type"], []).append(num(r, "impressions"))
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
    s = "\n".join(lines)
    print(s)
    return verdicts


if __name__ == "__main__":
    cmd, *args = sys.argv[1:] or ["help"]
    {"render": lambda: render(args[0]), "publish": lambda: publish(args[0], dry="--dry" in args),
     "comment": lambda: comment(args[0], args[1]), "brief": lambda: brief(args[0]),
     "metrics": lambda: metrics(*args), "auth": auth}.get(cmd, lambda: print(__doc__))()
