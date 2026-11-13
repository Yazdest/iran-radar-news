"""Images for posts without a photo: a branded news card, and a tweet 'screenshot' card (drawn, not scraped)."""
from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, features

import config

try:                                    # preferred: explicit Persian shaping
    import arabic_reshaper
    try:
        from bidi import get_display
    except ImportError:
        from bidi.algorithm import get_display
    HAVE_SHAPER = True
except ImportError:
    HAVE_SHAPER = False
USE_RAQM = (not HAVE_SHAPER) and features.check("raqm")   # Pillow's own shaping as a fallback

_HERE = Path(__file__).parent
_SYSTEM = [
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-{w}.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-{w}.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-{w}.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans{d}.ttf",
]


def _find_font(bold: bool) -> str | None:
    mine = sorted((_HERE / "fonts").glob("*.[to]tf"))           # fonts you put in the repo win
    pick = [f for f in mine if ("bold" in f.name.lower()) == bold] or mine
    if pick:
        return str(pick[0])
    for pattern in _SYSTEM:
        path = pattern.format(w="Bold" if bold else "Regular", d="-Bold" if bold else "")
        if Path(path).exists():
            return path
    return None


_BOLD, _REG = _find_font(True), _find_font(False)
AVAILABLE = bool(_BOLD) and (HAVE_SHAPER or USE_RAQM)


def _font(size: int, bold: bool = True):
    engine = ImageFont.Layout.RAQM if USE_RAQM else ImageFont.Layout.BASIC
    return ImageFont.truetype((_BOLD if bold else (_REG or _BOLD)), size, layout_engine=engine)


# ---------------------------------------------------------------- text helpers
def is_rtl(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum("\u0590" <= c <= "\u08ff" for c in letters) / len(letters) > 0.4


def _vis(text: str, rtl: bool) -> str:
    if HAVE_SHAPER:
        return get_display(arabic_reshaper.reshape(text), base_dir="R" if rtl else "L")
    return text


def _kw(rtl: bool) -> dict:
    return {"direction": "rtl" if rtl else "ltr", "language": "fa" if rtl else "en"} if USE_RAQM else {}


def _width(draw, text: str, font, rtl: bool) -> float:
    return draw.textlength(_vis(text, rtl), font=font, **_kw(rtl))


def _wrap(draw, text: str, font, max_w: int, rtl: bool, max_lines: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if cur and _width(draw, trial, font, rtl) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" ,.،") + " …"
    return lines


def _draw_line(draw, line: str, y: int, font, fill, rtl: bool, left: int, right: int) -> None:
    w = _width(draw, line, font, rtl)
    draw.text((right - w if rtl else left, y), _vis(line, rtl), font=font, fill=fill, **_kw(rtl))


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- news card
ACCENT = {"breaking": "#e11d2e", "statement": "#f59e0b", "assessment": "#3b82f6", "story": "#8b5cf6", "news": "#64748b"}
LABEL = {"breaking": "خبر فوری", "statement": "اظهارات", "assessment": "ارزیابی", "story": "روایت", "news": "خبر"}


def news_card(post, item) -> bytes | None:
    if not AVAILABLE:
        return None
    W, H, M = 1280, 720, 90
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):                                   # navy gradient
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(10 + 18 * t), int(16 + 22 * t), int(30 + 38 * t)))
    accent = ACCENT.get(post.kind, ACCENT["news"])
    d.rectangle([0, 0, 16, H], fill=accent)

    d.text((M, 52), config.BRAND_EN, font=_font(30), fill="#94a3b8")
    label = LABEL.get(post.kind, "خبر")
    lf = _font(34)
    lw = _width(d, label, lf, True)
    d.rounded_rectangle([W - M - lw - 44, 40, W - M, 104], radius=14, fill=accent)
    _draw_line(d, label, 50, lf, "white", True, 0, W - M - 22)

    rtl = is_rtl(post.title)
    for size in (64, 58, 52, 46):                        # biggest size that fits in 4 lines
        font = _font(size)
        lines = _wrap(d, post.title, font, W - 2 * M, rtl, 6)
        if len(lines) <= 4:
            break
    lines = lines[:4]
    step = int(size * 1.45)
    y = 170 + (4 - len(lines)) * step // 3
    for ln in lines:
        _draw_line(d, ln, y, font, "white", rtl, M, W - M)
        y += step

    d.line([(M, H - 120), (W - M, H - 120)], fill="#334155", width=2)
    d.text((M, H - 86), CHANNEL_TEXT(), font=_font(30, False), fill="#94a3b8")
    src = item.source if item.origin != "tweet" else f"{item.source} (X)"
    _draw_line(d, src, H - 92, _font(36), "#e2e8f0", is_rtl(src), M, W - M)
    return _jpeg(img)


def CHANNEL_TEXT() -> str:
    return config.CHANNEL if config.CHANNEL.startswith("@") else config.BRAND_EN


# ---------------------------------------------------------------- tweet card
def _avatar(url: str, name: str, size: int) -> Image.Image:
    img = None
    if url:
        try:
            r = requests.get(url, timeout=6)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGB").resize((size, size))
        except Exception:
            img = None
    if img is None:
        img = Image.new("RGB", (size, size), "#1d9bf0")
        ImageDraw.Draw(img).text((size // 2, size // 2), (name or "X")[:1].upper(), font=_font(size // 2),
                                 fill="white", anchor="mm")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    out = Image.new("RGBA", (size, size))
    out.paste(img, (0, 0), mask)
    return out


def tweet_card(card: dict) -> bytes | None:
    if not AVAILABLE:
        return None
    W, P = 1280, 64
    text = card.get("text", "")
    rtl = is_rtl(text)
    body_font, small = _font(46, False), _font(30, False)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap(probe, text, body_font, W - 2 * P, rtl, 11)
    H = 210 + len(lines) * 68 + 150

    img = Image.new("RGB", (W, H), "#15202b")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20, 20, W - 20, H - 20], radius=36, outline="#2f3b47", width=2)
    av = _avatar(card.get("avatar", ""), card.get("name", ""), 112)
    img.paste(av, (P, P), av)
    name = card.get("name", "")
    nrtl = is_rtl(name)
    d.text((P + 140, P + 8), _vis(name, nrtl), font=_font(42), fill="white", **_kw(nrtl))   # next to the avatar
    d.text((P + 140, P + 66), f"@{card.get('handle', '')}", font=small, fill="#8b98a5")
    d.text((W - P - 44, P + 10), "X", font=_font(64), fill="white")

    y = 210
    for ln in lines:
        _draw_line(d, ln, y, body_font, "#e7e9ea", rtl, P, W - P)
        y += 68
    when = ""
    if card.get("ts"):
        when = dt.datetime.fromtimestamp(int(card["ts"]), dt.timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    d.text((P, H - 120), when, font=small, fill="#8b98a5")
    d.text((W - P - _width(d, config.BRAND_EN, small, False), H - 120), config.BRAND_EN, font=small, fill="#8b98a5")
    return _jpeg(img)
