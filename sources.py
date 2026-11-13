"""Collect posts from the owner-approved public Telegram channels only."""
from __future__ import annotations

import hashlib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

import config

UA = {"User-Agent": "Mozilla/5.0 (compatible; IranRadarNews/3.0)"}
TIMEOUT = 20

# Last poll result per channel: "ok" | "empty" (page parsed but no posts: blocked/changed HTML) | "error".
# main.py uses it to log health and to alert the admin when a source is silent for a long time.
STATUS: dict[str, str] = {}

_IRAN_FLAG = "\U0001F1EE\U0001F1F7"
_IRAN_NAME_RE = re.compile(r"\b(?:iran(?:ian)?|tehran|irgc)\b", re.I)


@dataclass
class Media:
    kind: str  # "video" or "photo" (photos are intentionally not republished)
    url: str
    data: bytes | None = None


@dataclass
class Item:
    uid: str
    origin: str
    source: str                 # internal source channel id; never printed publicly
    title: str
    text: str
    url: str = ""
    published: datetime | None = None
    media: list = field(default_factory=list)


def _uid(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8", "ignore")).hexdigest()[:32]


_BG = re.compile(r"url\(['\"]?(.*?)['\"]?\)")
_LINKS = re.compile(r"(https?://\S+|\bt\.me/\S+|\bwww\.\S+|@\w+)", re.I)
_AD = re.compile(r"#ad\b|#تبلیغ|\bsponsored\b|\badvertisement\b|paid\s+partnership|promo\s*code|کد\s*تخفیف", re.I)
_CTA = re.compile(
    r"follow\s+us|subscribe|join\s+(us|our|the)|our\s+(channel|page|group)|click\s+(here|below)|"
    r"link\s+in\s+(bio|comments)|download\s+(the\s+|our\s+)?app|support\s+us|donate|read\s+more\s+(at|on)|"
    r"عضو\s*(شوید|شو)|عضویت\s*(در|:)|کانال\s*(ما|رسمی)|ما\s*را\s*دنبال|دنبال\s*کنید|فالو\s*کنید|"
    r"حمایت\s*مالی|تبلیغات|برای\s*تبلیغ|تماس\s*برای", re.I)
_SEPARATOR = re.compile(r"^[\s\-–—_=•·*~➖━─═▬▪▫⭐★☆.]{3,}$")
_ONLY_TAGS = re.compile(r"^(?:[#＃]\S+\s*)+$")
_SENTENCES = re.compile(r"(?<=[.!؟?])\s+")


def scrub_promo(text: str, extra=()) -> str:
    text = text or ""
    if _AD.search(text):
        return ""
    extra_res = [re.compile(x, re.I) for x in extra]
    lines, kept = text.splitlines(), []
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        if _SEPARATOR.match(line):
            tail = "\n".join(lines[idx + 1:])
            # Cut the tail only when it looks like a promo footer. The old extra `len(tail) < 200`
            # condition also threw away short legitimate lines that followed a separator.
            if kept and (_LINKS.search(tail) or _CTA.search(tail)):
                break
            continue
        has_link = bool(_LINKS.search(line))
        bare = re.sub(r"[ \t]+", " ", _LINKS.sub("", line)).strip()
        if (not bare or _ONLY_TAGS.match(bare) or any(r.search(line) for r in extra_res)):
            continue
        sentences = [s for s in _SENTENCES.split(bare) if not (_CTA.search(s) and (has_link or len(s) <= 160))]
        bare = " ".join(sentences).strip()
        if bare:
            kept.append(bare)
    return "\n".join(kept).strip()


def _bg(tag) -> str:
    match = _BG.search(tag.get("style", "")) if tag else None
    return match.group(1) if match else ""


def _video_url(player) -> str:
    video = player.select_one("video")
    if video:
        src = video.get("src") or video.get("data-src")
        if src:
            return src
        source = video.select_one("source[src]")
        if source:
            return source.get("src", "")
    for key in ("data-video", "data-src", "href"):
        value = player.get(key, "")
        if value.startswith("http") and (".mp4" in value.lower() or "video" in value.lower()):
            return value
    return ""


def collect_telegram(src: dict) -> list[Item]:
    channel = src["channel"]
    soup = None
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = requests.get(f"https://t.me/s/{channel}", headers=UA, timeout=TIMEOUT)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            break
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(1.5)
    if soup is None:
        STATUS[channel] = "error"
        print(f"WARN telegram {channel}: {last_exc}", file=sys.stderr)
        return []

    nodes = soup.select("div.tgme_widget_message")
    STATUS[channel] = "ok" if nodes else "empty"
    items: list[Item] = []
    for node in nodes[-40:]:
        post = node.get("data-post", "")
        body = node.select_one(".tgme_widget_message_text.js-message_text") or node.select_one(".tgme_widget_message_text")
        if not post or not body:
            continue
        for br in body.find_all("br"):
            br.replace_with("\n")
        raw_text = body.get_text("\n", strip=True)
        if src.get("require_iran_flag") and _IRAN_FLAG not in raw_text and not _IRAN_NAME_RE.search(raw_text):
            continue  # English channel: keep only posts marked with the Iran flag or naming Iran/Tehran/IRGC
        text = scrub_promo(raw_text, src.get("strip_lines", []))
        if not text or not config.RELEVANT_RE.search(text):
            continue

        media: list[Media] = []
        # Only videos are eligible for reposting. Photo-only posts remain text posts.
        for player in node.select("a.tgme_widget_message_video_player, .tgme_widget_message_roundvideo_player"):
            url = _video_url(player)
            if url:
                media.append(Media("video", url))

        time_tag = node.select_one("a.tgme_widget_message_date time")
        when = None
        if time_tag and time_tag.get("datetime"):
            try:
                when = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                pass

        first_line = next((line.strip() for line in text.splitlines() if line.strip()), text)
        items.append(Item(
            uid=_uid("tg", post), origin="telegram", source=channel,
            title=first_line[:240], text=text[:5000], url=f"https://t.me/{post}",
            published=when, media=media[:3],
        ))
    return items


def collect_all() -> list[Item]:
    with ThreadPoolExecutor(max_workers=min(8, len(config.TELEGRAM_SOURCES))) as pool:
        results = list(pool.map(collect_telegram, config.TELEGRAM_SOURCES))
    return [item for group in results for item in group]
