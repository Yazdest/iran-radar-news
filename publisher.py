"""Builds the post layout and sends it (media uploaded as files, never as links) to the channel."""
from __future__ import annotations

import html
import json
import re
import time

import requests

import cards
import config
from sources import UA, Item, Media
from translator import Post

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
PHOTO_MAX = 9_500_000
VIDEO_MAX = 49_000_000


class TelegramRejected(Exception):
    """Telegram answered with an explicit error (safe to fall back to another format)."""


# ------------------------------------------------------------------ layout
LABELS = {
    "breaking": "🚨 <b>#خبر_فوری</b>",
    "statement": "🗣 <b>اظهارات</b>",
    "assessment": "📊 <b>ارزیابی</b>",
    "story": "🎙 <b>روایت</b>",
    "news": "📰",
}


def _source(post: Post, item: Item) -> str:
    e = html.escape
    if item.origin == "telegram":
        return e(item.source)                                   # plain word: no @, no link
    if item.origin == "tweet":
        return f'<a href="{e(item.url, quote=True)}">توییت {e(item.source)}</a>'
    name = e(post.source or item.source)
    if config.SHOW_ARTICLE_LINK and item.url.startswith("http") and "news.google.com" not in item.url:
        return f'<a href="{e(item.url, quote=True)}">{name}</a>'
    return name


def _split_lead(text: str) -> tuple[str, str]:
    m = re.search(r"[.!؟?]\s+", text)
    return (text[: m.start() + 1], text[m.end():]) if m else (text, "")


def build_html(post: Post, item: Item) -> str:
    """The FIRST LINE is what the phone notification shows, so it holds the label and the core of the news:
         🚨 #خبر_فوری · عنوان کامل
         متن کوتاه
         رویترز · رادار ایران"""
    e = html.escape
    label = LABELS.get(post.kind, LABELS["news"])
    title, body = post.title, post.body
    if not title:                                   # single-text news: its first sentence becomes the first line
        title, body = _split_lead(body)
    sep = " " if post.kind == "news" else " · "
    shown = f"<b>{e(title)}</b>" if len(title) <= 110 else e(title)       # a long single sentence stays plain
    parts = [f"{label}{sep}{shown}"]
    if body:
        quoted = post.kind in ("statement", "assessment", "story")
        parts.append(f"<blockquote>{e(body)}</blockquote>" if quoted else e(body))
    tail = _source(post, item)
    if config.BRAND_FA and config.CHANNEL_URL:
        tail += f' · <a href="{e(config.CHANNEL_URL, quote=True)}">{e(config.BRAND_FA)}</a>'
    parts.append(tail)
    return "\n\n".join(parts)


def visible_len(markup: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", markup)))


# ------------------------------------------------------------------ Telegram API
def _call(method: str, data: dict | None = None, files: dict | None = None):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    for _ in range(3):
        r = requests.post(url, data=data, files=files, timeout=120)
        try:
            js = r.json()
        except ValueError:
            js = {}
        if js.get("ok"):
            return js["result"]
        if r.status_code == 429:  # flood control: request was NOT accepted, so retrying is safe
            time.sleep(min(int(js.get("parameters", {}).get("retry_after", 5)), 30))
            continue
        raise TelegramRejected(js.get("description", f"HTTP {r.status_code}"))
    raise TelegramRejected("rate limited")


def _download(m: Media) -> tuple[str, bytes] | None:
    limit = VIDEO_MAX if m.kind == "video" else PHOTO_MAX
    if m.data:
        return "photo.jpg", m.data
    try:
        with requests.get(m.url, headers=UA, timeout=40, stream=True) as r:
            r.raise_for_status()
            buf = bytearray()
            for chunk in r.iter_content(65536):
                buf += chunk
                if len(buf) > limit:
                    return None
        return ("video.mp4" if m.kind == "video" else "photo.jpg"), bytes(buf)
    except Exception:
        return None


def _send_text(text: str):
    return _call("sendMessage", {"chat_id": config.CHANNEL, "text": text, "parse_mode": "HTML",
                                 "link_preview_options": json.dumps({"is_disabled": True})})


def _send_media(files: list[tuple[Media, tuple[str, bytes]]], caption: str | None):
    cap = {"caption": caption, "parse_mode": "HTML"} if caption else {}
    if len(files) == 1:
        m, f = files[0]
        field = "video" if m.kind == "video" else "photo"
        data = {"chat_id": config.CHANNEL, **cap}
        if m.kind == "video":
            data["supports_streaming"] = "true"
        return _call("sendVideo" if m.kind == "video" else "sendPhoto", data, {field: f})
    media, attach = [], {}
    for i, (m, f) in enumerate(files):
        entry = {"type": m.kind, "media": f"attach://f{i}"}
        if i == 0:
            entry.update(cap)
        media.append(entry)
        attach[f"f{i}"] = f
    return _call("sendMediaGroup", {"chat_id": config.CHANNEL, "media": json.dumps(media)}, attach)


# ------------------------------------------------------------------ publish one item
def publish(item: Item, post: Post) -> None:
    markup = build_html(post, item)
    if config.DRY_RUN:
        print("-" * 40, f"\n[{post.kind}] media={[(m.kind, m.url[:60]) for m in item.media]}\n{markup}\n")
        return

    files = [(m, d) for m in item.media if (d := _download(m))]
    if not files:                                     # no usable picture: draw a branded card instead
        card = cards.news_card(post, item)
        if card:
            files = [(Media("photo", "", card), ("photo.jpg", card))]
    if not files:
        _send_text(markup[:TEXT_LIMIT])
        return

    fits = visible_len(markup) <= CAPTION_LIMIT
    try:
        _send_media(files, markup if fits else None)
        if not fits:                      # long text: media first, full text right after it
            _send_text(markup[:TEXT_LIMIT])
    except TelegramRejected as exc:       # e.g. unsupported file: still deliver the news itself
        print(f"WARN media rejected ({exc}); sending text only")
        _send_text(markup[:TEXT_LIMIT])
