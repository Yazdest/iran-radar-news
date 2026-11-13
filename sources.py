"""Collectors: RSS feeds of major outlets + public Telegram channels (text, photos, videos)."""
from __future__ import annotations

import calendar
import hashlib
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import feedparser
import requests
from bs4 import BeautifulSoup

import config

UA = {"User-Agent": "Mozilla/5.0 (compatible; RadarNewsBot/2.0)"}
TIMEOUT = 15


@dataclass
class Media:
    kind: str  # "photo" | "video"
    url: str
    data: bytes | None = None   # already-rendered image (generated cards)


@dataclass
class Item:
    uid: str
    origin: str            # "rss" | "telegram" | "tweet"
    source: str            # RSS: Persian outlet name | Telegram: channel username
    title: str
    text: str
    url: str = ""          # internal use only; never shown for Telegram sources
    published: datetime | None = None
    media: list = field(default_factory=list)
    skip_og: bool = False
    card: dict | None = None   # tweets: data for the tweet screenshot card


def _uid(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8", "ignore")).hexdigest()[:32]


def strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)).strip()


# ------------------------------------------------------------------ RSS
def _rss_image(entry) -> str:
    best, best_w = "", -1
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key) or []:
            url = m.get("url", "")
            if not url or m.get("medium", "image") != "image" or "video" in m.get("type", ""):
                continue
            try:
                w = int(m.get("width") or 0)
            except ValueError:
                w = 0
            if w > best_w:
                best, best_w = url, w
    if best:
        return best
    for enc in entry.get("enclosures") or []:
        if "image" in enc.get("type", "") and enc.get("href"):
            return enc["href"]
    img = BeautifulSoup(entry.get("summary", "") or "", "html.parser").find("img")
    return img.get("src", "") if img and img.get("src", "").startswith("http") else ""


def fetch_og_image(url: str) -> str:
    """Reference picture of the article page (og:image) when the feed has none."""
    if not url or "news.google.com" in url:
        return ""
    try:
        r = requests.get(url, headers=UA, timeout=8)
        soup = BeautifulSoup(r.text[:400000], "html.parser")
        for attrs in ({"property": "og:image"}, {"name": "twitter:image"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and str(tag.get("content", "")).startswith("http"):
                return tag["content"]
    except Exception:
        pass
    return ""


def collect_rss(feed: dict) -> list[Item]:
    items: list[Item] = []
    try:
        r = requests.get(feed["url"], headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
    except Exception as exc:
        print(f"WARN rss {feed['name']}: {exc}", file=sys.stderr)
        return items
    for e in parsed.entries[:40]:
        title = strip_html(e.get("title", ""))
        link = e.get("link", "")
        source = feed.get("fa", feed["name"])
        if feed.get("gnews"):                      # "Headline - Reuters"  ->  title + outlet name
            m = re.search(r"\s+[-–|]\s+([^-–|]{2,40})$", title)
            if m:
                source, title = m.group(1).strip(), title[: m.start()].strip()
        # Google News snippets are often the only context available for its headlines.
        summary = scrub_promo(strip_html(e.get("summary", e.get("description", "")))[:3000])
        # Curated Google News queries are already Iran-focused. Let the AI judge those
        # snippets even when a headline omits "Iran" (e.g. "Israel prepares response").
        if not title or not link or _AD.search(title):
            continue
        if not feed.get("gnews") and not config.RELEVANT_RE.search(f"{title} {summary}"):
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        when = datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else None
        image = _rss_image(e)
        items.append(Item(
            uid=_uid("rss", feed["name"], link or title), origin="rss", source=source,
            title=title, text=summary[:3000], url=link, published=when,
            media=[Media("photo", image)] if image else [], skip_og=bool(feed.get("gnews")),
        ))
    return items


# ------------------------------------------------------------------ Telegram
_BG = re.compile(r"url\(['\"]?(.*?)['\"]?\)")
_LINKS = re.compile(r"(https?://\S+|\bt\.me/\S+|\bwww\.\S+|@\w+)", re.I)

# Advertising / self-promotion. Whole post is an ad -> dropped. Call-to-action sentences or footer lines -> removed.
_AD = re.compile(
    r"#ad\b|#تبلیغ|\bsponsored\b|\badvertisement\b|paid\s+partnership|promo\s*code|کد\s*تخفیف|"
    r"إعلان\s*مدفوع|פרסומת", re.I)
_CTA = re.compile(
    r"follow\s+us|follow\s+(our|the)\s+(channel|page|account)|subscribe|join\s+(us|our|the\s+channel|the\s+group)|"
    r"our\s+(channel|page|group)|click\s+(here|the\s+link|below)|link\s+in\s+(bio|comments)|sign\s+up|"
    r"download\s+(the\s+|our\s+)?app|use\s+code|support\s+us|donate|the\s+post\s+.{0,120}\s+appeared\s+first\s+on|"
    r"read\s+more\s+(at|on)|"
    r"عضو\s*(شوید|شو)|عضویت\s*(در|:)|کانال\s*(ما|رسمی)|ما\s*را\s*دنبال|دنبال\s*کنید|فالو\s*کنید|"
    r"حمایت\s*مالی|تبلیغات|برای\s*تبلیغ|تماس\s*برای|"
    r"انضم\w*|اشترك\w*|تابعونا|قناتنا|"
    r"הצטרפ\w*|עקבו\s+אחרינו|הערוץ\s+שלנו", re.I)
_SEPARATOR = re.compile(r"^[\s\-–—_=•·*~➖━─═▬▪▫⭐★☆.]{3,}$")
_ONLY_TAGS = re.compile(r"^(?:[#＃]\S+\s*)+$")
_SENTENCES = re.compile(r"(?<=[.!؟?])\s+")


def scrub_promo(text: str, extra=()) -> str:
    """Remove ads and self-promotion (calls to subscribe/follow, channel signatures, hashtag footers, links, @handles).
    Returns "" when the whole item is an advertisement. `extra`: your own regexes for lines to delete."""
    text = text or ""
    if _AD.search(text):
        return ""
    extra_res = [re.compile(x, re.I) for x in extra]
    lines, kept = text.splitlines(), []
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            kept.append("")
            continue
        if _SEPARATOR.match(line):                     # footer separator: cut it if what follows is promo-like or short
            tail = "\n".join(lines[idx + 1:])
            if len("\n".join(kept).strip()) >= 40 and (_LINKS.search(tail) or _CTA.search(tail) or len(tail) < 200):
                break
            continue
        has_link = bool(_LINKS.search(line))
        bare = re.sub(r"[ \t]+", " ", _LINKS.sub("", line)).strip()
        stub = has_link and re.fullmatch(r"(?i)\W*(follow|join|subscribe|watch|more|source|via|link|telegram|channel|فالو|عضو|عضویت|کانال|منبع)\W*", bare)
        if stub or not re.search(r"[^\W_]", bare) or _ONLY_TAGS.match(bare) or any(r.search(line) for r in extra_res):
            continue                                   # nothing but links/handles/emoji/hashtags, or your own rule
        sentences = [x for x in _SENTENCES.split(bare)
                     if not (_CTA.search(x) and (has_link or len(x) <= 160))]
        bare = " ".join(sentences).strip()
        if bare:
            kept.append(bare)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def _bg(tag) -> str:
    m = _BG.search(tag.get("style", "")) if tag else None
    return m.group(1) if m else ""


def clean_tg_text(node, extra=()) -> str:
    for br in node.find_all("br"):
        br.replace_with("\n")
    return scrub_promo(node.get_text(""), extra)


def collect_telegram(src: dict) -> list[Item]:
    channel = src["channel"]
    try:
        r = requests.get(f"https://t.me/s/{channel}", headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as exc:
        print(f"WARN telegram {channel}: {exc}", file=sys.stderr)
        return []
    items: list[Item] = []
    for node in soup.select("div.tgme_widget_message")[-30:]:
        post = node.get("data-post", "")
        body = node.select_one(".tgme_widget_message_text.js-message_text") or node.select_one(".tgme_widget_message_text")
        if not post or not body:
            continue
        text = clean_tg_text(body, src.get("strip_lines", []))
        if not text or (src.get("filter_iran", True) and not config.RELEVANT_RE.search(text)):
            continue
        media: list[Media] = []
        for a in node.select("a.tgme_widget_message_photo_wrap"):
            if _bg(a):
                media.append(Media("photo", _bg(a)))
        for v in node.select("a.tgme_widget_message_video_player, .tgme_widget_message_roundvideo_player"):
            video = v.select_one("video")
            if video is not None and video.get("src"):
                media.append(Media("video", video["src"]))
            else:  # large video without a public file: keep at least its still frame
                thumb = v.select_one("i.tgme_widget_message_video_thumb, i.tgme_widget_message_roundvideo_thumb")
                if thumb is not None and _bg(thumb):
                    media.append(Media("photo", _bg(thumb)))
        time_tag = node.select_one("a.tgme_widget_message_date time")
        when = None
        if time_tag and time_tag.get("datetime"):
            try:
                when = datetime.fromisoformat(time_tag["datetime"]).astimezone(timezone.utc)
            except ValueError:
                pass
        first_line = text.split("\n", 1)[0]
        items.append(Item(
            uid=_uid("tg", post), origin="telegram", source=channel, title=first_line[:200],
            text=text[:4000], url=f"https://t.me/{post}", published=when, media=media[:10], skip_og=True,
        ))
    return items


# ------------------------------------------------------------------ all sources at once
def collect_all() -> list[Item]:
    import tweets  # local import: tweets.py needs this module
    jobs = ([(collect_rss, f) for f in config.RSS_FEEDS] + [(collect_telegram, s) for s in config.TELEGRAM_SOURCES]
            + [(tweets.collect_tweets, a) for a in config.TWEET_ACCOUNTS])
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda j: j[0](j[1]), jobs))
    return [i for chunk in results for i in chunk]
