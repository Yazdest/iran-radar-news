"""Images for posts without a photo: a branded news card, and a tweet 'screenshot' card (drawn, not scraped)."""
from __future__ import annotations

import datetime as dt
import io
import re
from pathlib import Path
from functools import lru_cache

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, ImageEnhance, features

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
    # B Titr is reserved for headlines; B Nazanin is used for supporting labels.
    engine = ImageFont.Layout.RAQM if USE_RAQM else ImageFont.Layout.BASIC
    path = _BOLD if bold else (_REG or _BOLD)
    return ImageFont.truetype(path, size, layout_engine=engine)

def _latin_font(size: int, bold: bool = False):
    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if Path(path).exists():
        return ImageFont.truetype(path, size)
    return _font(size, bold)


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


def _draw_line(draw, line: str, y: int, font, fill, rtl: bool, left: int, right: int, force_right: bool = False) -> None:
    w = _width(draw, line, font, rtl)
    draw.text((right - w if (rtl or force_right) else left, y), _vis(line, rtl), font=font, fill=fill, **_kw(rtl))


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------- news card
ACCENT = {"breaking": "#e11d2e", "statement": "#f59e0b", "assessment": "#3b82f6", "story": "#8b5cf6", "news": "#64748b"}
LABEL = {"breaking": "خبر فوری"}           # no label words on other cards



@lru_cache(maxsize=16)
def _speaker_image(url: str):
    try:
        r = requests.get(url, headers={"User-Agent": "IranRadarNews/2.0 (news-card; Wikimedia Commons)"},
                         timeout=12)
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content)).convert("RGB")
        im.thumbnail((900, 900))
        return im
    except Exception as exc:
        print(f"WARN speaker portrait unavailable: {exc}")
        return None


def _speaker(post, item):
    """Identify the quoted/issuing official from both the article and account metadata."""
    card = getattr(item, "card", None) or {}
    parts = [
        getattr(post, "title", ""), getattr(post, "body", ""),
        getattr(post, "source", ""), getattr(item, "title", ""),
        getattr(item, "text", ""), getattr(item, "source", ""),
        card.get("name", ""), card.get("handle", ""),
        getattr(item, "url", ""),
    ]
    text = " ".join(str(x or "") for x in parts).casefold()
    # Prefer account identity for official-post items, even if the post text omits its author's name.
    handle = str(card.get("handle", "")).casefold().lstrip("@")
    for key, spec in config.SPEAKER_PORTRAITS.items():
        patterns = [str(x).casefold() for x in spec.get("patterns", [])]
        if (handle and any(handle == p.lstrip("@") for p in patterns)) or any(p in text for p in patterns):
            return key, spec
    return None, None


def _draw_justified(draw, line: str, y: int, font, fill, rtl: bool, left: int, right: int):
    """True block justification for all non-final lines, including two-word lines."""
    words = line.split()
    # Very short lines are not stretched; that creates unnatural gaps in Persian.
    if len(words) < 3:
        _draw_line(draw, line, y, font, fill, rtl, left, right)
        return
    widths = [_width(draw, word, font, rtl) for word in words]
    free = max(0, (right - left) - sum(widths))
    gap = free / (len(words) - 1)
    if not rtl:
        x = left
        for word, width in zip(words, widths):
            draw.text((round(x), y), _vis(word, False), font=font, fill=fill, **_kw(False))
            x += width + gap
    else:
        x = right
        for word, width in zip(words, widths):
            x -= width
            draw.text((round(x), y), _vis(word, True), font=font, fill=fill, **_kw(True))
            x -= gap


def _photo_panel(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Keep the entire source image visible; blurred side-fill avoids destructive crops."""
    w, h = size
    source = img.convert("RGB")
    backdrop = ImageOps.fit(source, (w, h), method=Image.Resampling.LANCZOS)
    backdrop = ImageEnhance.Brightness(backdrop.filter(ImageFilter.GaussianBlur(26))).enhance(0.38)
    foreground = ImageOps.contain(source, (w - 32, h - 28), method=Image.Resampling.LANCZOS)
    backdrop.paste(foreground, ((w - foreground.width)//2, (h - foreground.height)//2))
    return backdrop


def _story_image(post, item):
    """Prefer the actual article image; only then try a topic-matched Commons image."""
    # RSS / Telegram / article OpenGraph image is the strongest available match.
    for media in (getattr(item, "media", None) or []):
        if getattr(media, "kind", "") != "photo":
            continue
        if getattr(media, "data", None):
            try:
                im = Image.open(io.BytesIO(media.data)).convert("RGB")
                if im.width >= 240 and im.height >= 160:
                    return im, "article"
            except Exception:
                pass
        url = getattr(media, "url", "")
        if url:
            im = _speaker_image(url)
            if im is not None and im.width >= 240 and im.height >= 160:
                return im, "article"

    # If the feed has no picture, query Wikimedia Commons using the original
    # headline (not a generic politician portrait). Never fabricate an image.
    raw = " ".join([getattr(item, "title", "") or "", getattr(item, "text", "") or "",
                     getattr(post, "title", "") or "", getattr(post, "body", "") or ""])
    # Topic-specific English search terms help Commons find useful archive photos
    # even when the original feed headline is Persian.
    topic_terms = []
    rules = [
        (r"کوه کلنگ|کلنگ|تأسیسات هسته|تاسیسات هسته|سانتریفیوژ|غنی.?سازی|nuclear|centrifuge|enrichment", "Iran nuclear facility satellite; uranium centrifuge"),
        (r"نطنز|natanz", "Natanz nuclear facility satellite"),
        (r"فردو|fordow|fordo", "Fordow uranium enrichment facility"),
        (r"موشک|missile", "Iran ballistic missile"),
        (r"پهپاد|drone", "Iran military drone"),
        (r"نفت|نفتکش|oil|tanker", "Persian Gulf oil tanker"),
        (r"تنگه هرمز|هرمز|hormuz", "Strait of Hormuz satellite"),
        (r"پدافند|air defense", "Iran air defense system"),
        (r"جنگنده|fighter jet|aircraft", "military aircraft Middle East"),
    ]
    for pattern, term in rules:
        if re.search(pattern, raw, re.I):
            topic_terms.append(term)
    query = " ".join(topic_terms[:2]) or re.sub(r"[^\w\s-]", " ", raw)
    query = " ".join(query.split()[:18])
    if query:
        try:
            response = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={"action": "query", "generator": "search", "gsrsearch": query,
                        "gsrnamespace": 6, "gsrlimit": 12,
                        "prop": "imageinfo", "iiprop": "url|mime",
                        "iiurlwidth": 1400, "format": "json"},
                headers={"User-Agent": "IranRadarNews/3.0 (topic image lookup; contact: admin)"},
                timeout=10)
            response.raise_for_status()
            pages = response.json().get("query", {}).get("pages", {})
            for page in pages.values():
                info = (page.get("imageinfo") or [{}])[0]
                url = info.get("thumburl") or info.get("url", "")
                if not url or info.get("mime", "").split("/")[0] != "image":
                    continue
                im = _speaker_image(url)
                if im is not None and im.width >= 240 and im.height >= 160:
                    return im, "commons"
        except Exception as exc:
            print(f"WARN topic image lookup unavailable: {exc}")

    # Never substitute a recurring official portrait for a missing story image.
    # A topic archive image is preferable; otherwise use a clean typography-only card.
    return None, ""


def news_card(post, item) -> bytes | None:
    """Square news poster with a topic-relevant image and a compact, adaptive headline."""
    if not AVAILABLE:
        return None

    W = H = 1080
    M = 54
    img = Image.new("RGB", (W, H), "#fffdf5")
    d = ImageDraw.Draw(img)
    # Channel-wide visual convention requested by the editor.
    urgent = True
    accent = "#e11d2e"
    d.rectangle([0, 0, W, 116], fill=accent)
    d.rectangle([0, 116, W, 128], fill="#141414")
    d.text((M, 39), config.BRAND_EN, font=_latin_font(31, True), fill="#ffffff")
    label = "خبر فوری"
    lf = _font(48, True)
    lw = _width(d, label, lf, True)
    _draw_line(d, label, 29, lf, "#ffffff", True, W-M-lw-10, W-M)

    title = re.sub(r"\s+", " ", (post.title or _first_sentence(post.body)).strip())
    speaker_name, speaker = _speaker(post, item)
    story_image, image_kind = _story_image(post, item)

    # With an image, allocate almost half the canvas to the visual and half to
    # the headline. Without a credible image, keep a clean graphic-only card.
    if story_image is not None:
        photo_top, photo_bottom = 128, 590
        photo = _photo_panel(story_image, (W, photo_bottom-photo_top))
        img.paste(photo, (0, photo_top))
        # Gentle lower fade-like band keeps the photo and text visually separated.
        d = ImageDraw.Draw(img)
        d.rectangle([0, 580, W, 600], fill="#141414")
        text_top, text_bottom = 620, 1010
        left, right = 76, W-76
    else:
        text_top, text_bottom = 180, 990
        left, right = 76, W-76

    max_w = right-left
    chosen_font, lines = None, []
    # Editorial headline scale: compact, readable, never stretch spaces to fill a line.
    max_size = 68 if story_image is not None else 76
    min_size = 42 if story_image is not None else 46
    max_lines = 5 if story_image is not None else 6
    for size in range(max_size, min_size-1, -2):
        f = _font(size, True)
        candidate = _wrap(d, title, f, max_w, True, max_lines)
        line_step = int(size * 1.24)
        if len(candidate) <= max_lines and len(candidate)*line_step <= (text_bottom-text_top):
            chosen_font, lines = f, candidate
            break
    if chosen_font is None:
        chosen_font = _font(min_size, True)
        lines = _wrap(d, title, chosen_font, max_w, True, max_lines)
    step = int(chosen_font.size * 1.24)
    block_h = len(lines)*step
    y = text_top + max(0, (text_bottom-text_top-block_h)//2)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([W-58, text_top+4, W-42, min(text_bottom-8, y+block_h-8)], radius=8, fill=accent)
    for i, line in enumerate(lines):
        _draw_line(d, line, y, chosen_font, "#101522", True, left, right)
        y += step

    # Speaker attribution is a restrained ticker, not a substitute for the
    # article's actual subject image. It appears only for identified speakers.
    d.rectangle([0, 1010, W, H], fill="#141414")
    d.text((M, 1025), config.BRAND_EN, font=_latin_font(27, True), fill="#ffffff")
    _draw_line(d, "رادار ایران", 1019, _font(33, True), "#ffffff", True, W-280, W-M)
    return _jpeg(img)


def _first_sentence(text: str) -> str:
    import re
    m = re.search(r"[.!؟?]\s", text or "")
    return (text[: m.start() + 1] if m else (text or "")).strip()


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
    W = H = 1080
    P = 64
    text = card.get("text", "")
    rtl = is_rtl(text)
    body_font = _font(42, False)
    small = _latin_font(24, False)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = _wrap(probe, text, body_font, W - 2*P, rtl, 9)

    img = Image.new("RGB", (W, H), "#fbfaf6")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([20, 20, W-20, H-20], radius=34, outline="#e2e8f0", width=3, fill="#ffffff")

    av = _avatar(card.get("avatar", ""), card.get("name", ""), 112)
    img.paste(av, (P, P), av)
    name = card.get("name", "")
    nrtl = is_rtl(name)
    d.text((P+140, P+8), _vis(name, nrtl), font=_font(42), fill="#172033", **_kw(nrtl))
    d.text((P+140, P+66), f"@{card.get('handle', '')}", font=small, fill="#64748b")
    d.text((W-P-44, P+10), "X", font=_latin_font(64, True), fill="#172033")

    d.line([(P, 205), (W-P, 205)], fill="#e2e8f0", width=2)
    y = 250
    for i, ln in enumerate(lines):
        _draw_line(d, ln, y, body_font, "#172033", rtl, P, W-P)
        y += 66

    when = ""
    if card.get("ts"):
        when = dt.datetime.fromtimestamp(int(card["ts"]), dt.timezone.utc).strftime("%d %b %Y · %H:%M UTC")
    d.line([(P, 930), (W-P, 930)], fill="#e2e8f0", width=2)
    d.text((P, 958), when, font=small, fill="#64748b")
    d.text((W-P-_width(d, config.BRAND_EN, small, False), 958),
           config.BRAND_EN, font=small, fill="#64748b")
    return _jpeg(img)
