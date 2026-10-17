"""One runnable self-check: python test_studio.py (no network, no writes to the repo)."""
import json, shutil, tempfile
from pathlib import Path
import studio

REPO = Path(__file__).resolve().parent
tmp = Path(tempfile.mkdtemp())
for d in ("content", "data"):
    shutil.copytree(REPO / d, tmp / d)
shutil.copy(REPO / "config.yaml", tmp / "config.yaml")
studio.ROOT = tmp  # every write below lands in the temp copy
src = tmp / "content" / ".sample.json"
j = tmp / "x.json"
shutil.copy(src, j)

# render -> real PDF, small enough to email
pdf = studio.render(j)
data = pdf.read_bytes()
assert data[:4] == b"%PDF", "not a pdf"
n_slides = len(json.loads(src.read_text())["carousel"]["slides"]) + 2
assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") == n_slides, "page count"
assert len(data) < 2_000_000, f"pdf too big: {len(data)}"

# Little Text Format escaping keeps hashtags live
assert studio.ltf_escape("a (b) [c] @d *f_ #AI") == r"a \(b\) \[c\] \@d \*f\_ {hashtag|\#|AI}", studio.ltf_escape("a (b) [c] @d *f_ #AI")

# brief
html = studio.brief(j)
assert "70% busywork" in html and "Carousel" in html

# lint: the minimal sample fails on counts; every real kit passes; missing keys are caught
v = studio.lint(j)
assert any("engagement_comments" in x for x in v) and any("chars" in x for x in v), v
kits = sorted((tmp / "content").glob("2*.json"))
for k in kits:
    assert studio.lint(k) == [], f"{k} fails lint"
broken = json.loads(kits[-1].read_text())
broken.pop("date"); broken["dms"][0].pop("to"); broken["post"]["text"] += " #a #b #c #d"
b = tmp / "broken.json"; b.write_text(json.dumps(broken))
v = studio.lint(b)
assert any("date is required" in x for x in v) and any("dms[0] missing to" in x for x in v) and any("hashtags" in x for x in v), v

# memory survives a corrupt kit and never leaves the temp dir
(tmp / "content" / "2099-01-01.json").write_text("{not json")
mem = studio.memory()
assert mem.parent == tmp / "data" and mem.read_text().count("|") >= 5 * (len(kits) + 1), "memory table"

# metrics: tolerant of "1,234", trims types, orders by date, proposes a mix
csvp = tmp / "m.csv"
csvp.write_text("date,post_type,impressions,reactions,comments,reposts,profile_views,connections_sent,connections_accepted,leads\n"
                "2026-01-08,story ,900,,,,,,,\n2026-01-01,story,800,,,,,,,\n2026-01-02,carousel,\"6,000\",,,,,,,\n")
v = studio.metrics(csvp)
assert v["story"].startswith("KILL") and v["carousel"].startswith("DOUBLE"), v

# publish: dry makes no network calls; a second publish of the same date is refused; create is never retried
calls = []
studio._call = lambda method, url, body=None, headers=None, raw=None, tries=4: (calls.append((method, url, tries)), ({"value": {"uploadUrl": "u", "document": "urn:li:document:1"}}, {"x-restli-id": "urn:li:share:1"}))[1]
import os
os.environ["LINKEDIN_PERSON_URN"] = "urn:li:person:T"; os.environ["LINKEDIN_ACCESS_TOKEN"] = "t"
kit = kits[-1]
studio.publish(kit, dry=True)
assert calls == [], f"dry run touched the network: {calls}"
assert studio.publish(kit) == "urn:li:share:1"
assert [c[0] for c in calls] == ["POST", "PUT", "POST"] and calls[-1][2] == 1, calls
calls.clear()
try:
    studio.publish(kit); raise AssertionError("second publish should be refused")
except SystemExit as e:
    assert "already posted" in str(e) and calls == [], (e, calls)

print("OK", len(kits), "kit(s) lint clean; publish idempotent; dry run offline")
