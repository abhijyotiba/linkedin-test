"""Carousel renderer: kit carousel -> PDF bytes (1080x1350 slides, cover claim, numbered slides, CTA)."""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, M = 1080, 1350, 90
FONTS = Path(__file__).parent / "assets" / "fonts"


def _font(size: int, bold: bool = True):
    p = FONTS / ("Inter-Bold.otf" if bold else "Inter-Regular.otf")
    return ImageFont.truetype(str(p), size) if p.exists() else ImageFont.load_default(size)


def _wrap(font, text, max_w):
    lines = []
    for para in str(text).split("\n"):
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
    size = start
    while size > min_size:
        f = _font(size, bold)
        lines = _wrap(f, text, max_w)
        if len(lines) * size * 1.2 <= max_h and all(f.getlength(l) <= max_w for l in lines):
            return f, lines
        size -= 4
    f = _font(min_size, bold)
    return f, _wrap(f, text, max_w)


def _lines(d, x, y, font, lines, fill, lh=1.2):
    for l in lines:
        d.text((x, y), l, font=font, fill=fill)
        y += font.size * lh
    return y


def carousel_pdf(kit: dict, brand: dict, face_png: bytes | None = None) -> bytes:
    car = kit.get("carousel") or {}
    bg, fg, accent, muted = brand.get("bg", "#0B1220"), brand.get("fg", "#FFFFFF"), brand.get("accent", "#22D3EE"), brand.get("muted", "#94A3B8")
    handle = brand.get("handle") or ""
    slides = car.get("slides") or []
    n = len(slides) + 2
    pages = []

    def slide(color, bar):
        img = Image.new("RGB", (W, H), color)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 14], fill=bar)
        return img, d

    def footer(d, i, color):
        d.text((M, H - 70), handle, font=_font(28, False), fill=color)
        d.text((W - M - 120, H - 70), f"{i}/{n}", font=_font(28, False), fill=color)

    def face(img):
        if not face_png:
            return
        size = 260
        f = Image.open(io.BytesIO(face_png)).convert("RGBA").resize((size, size))
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
        img.paste(f, (W - size - 80, H - size - 80), mask)

    img, d = slide(bg, accent)
    f, lines = _fit(car.get("title", ""), W - 2 * M, 700, 112)
    y = _lines(d, M, 260, f, lines, fg)
    f2, sub = _fit(car.get("subtitle", "Swipe →"), W - 2 * M, 160, 40, bold=False)
    _lines(d, M, y + 40, f2, sub, accent)
    face(img)
    footer(d, 1, muted)
    pages.append(img)
    for i, s in enumerate(slides, 2):
        img, d = slide(bg, accent)
        d.text((M, 120), f"{i - 1:02d}", font=_font(64), fill=accent)
        f, lines = _fit(s.get("title", ""), W - 2 * M, 360, 76)
        y = _lines(d, M, 230, f, lines, fg)
        f2, lines2 = _fit(s.get("body", ""), W - 2 * M, 560, 44, bold=False, min_size=30)
        _lines(d, M, y + 40, f2, lines2, muted, 1.35)
        footer(d, i, muted)
        pages.append(img)
    img, d = slide(accent, bg)
    f, lines = _fit(car.get("cta", "Follow for more."), W - 2 * M, 600, 96)
    y = _lines(d, M, 300, f, lines, bg)
    d.text((M, y + 40), handle, font=_font(40, False), fill=bg)
    face(img)
    footer(d, n, bg)
    pages.append(img)
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=96, quality=90)
    return buf.getvalue()
