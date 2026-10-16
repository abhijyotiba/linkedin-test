"""One runnable self-check: python test_studio.py"""
import json, shutil, tempfile
from pathlib import Path
import studio

tmp = Path(tempfile.mkdtemp())
src = Path(__file__).parent / "content" / ".sample.json"
j = tmp / "x.json"
shutil.copy(src, j)

pdf = studio.render(j)
data = pdf.read_bytes()
assert data[:4] == b"%PDF", "not a pdf"
n_slides = len(json.loads(src.read_text())["carousel"]["slides"]) + 2
assert data.count(b"/Type /Page\n") == n_slides or data.count(b"/Type /Page") - data.count(b"/Type /Pages") == n_slides, "page count"
assert len(data) < 2_000_000, f"pdf too big for email: {len(data)}"

assert studio.ltf_escape("a (b) [c] @d #e *f_") == r"a \(b\) \[c\] \@d \#e \*f\_"

html = studio.brief(j)
assert "70% busywork" in html and "Carousel" in html

csvp = tmp / "m.csv"
csvp.write_text("date,post_type,impressions,reactions,comments,reposts,profile_views,connections_sent,connections_accepted,leads\n"
                "2026-01-01,story,800,,,,,,,\n2026-01-08,story,900,,,,,,,\n2026-01-02,carousel,6000,,,,,,,\n")
v = studio.metrics(csvp)
assert v["story"].startswith("KILL") and v["carousel"].startswith("DOUBLE"), v
print("OK", pdf, len(data), "bytes")
