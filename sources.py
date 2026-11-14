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
# STRONG call-to-action phrases are removed from any short sentence. WEAK ones (that can also appear in real
# news: «حمایت مالی», «کانال رسمی», "donate"...) are removed only when the same line carries a link/@mention.
# (The old single pattern removed «عضویت ایران در پیمان شانگهای», any sentence with «تبلیغات», and
# "...to join the talks".)
_CTA_STRONG = re.compile(
    r"follow\s+us|subscribe|join\s+(?:us|our)\b|join\s+the\s+(?:channel|group|chat|community)|our\s+(?:channel|page|group)|"
    r"click\s+(?:here|below)|link\s+in\s+(?:bio|comments)|download\s+(?:the\s+|our\s+)?app|read\s+more\s+(?:at|on)|"
    r"عضو\s*(?:شوید|شو)\b|عضویت\s*در\s*(?:کانال|گروه|ربات|پیج|صفحه|ما)\b|کانال\s*ما\b|ما\s*را\s*دنبال|"
    r"دنبال\s*کنید|فالو\s*کنید|برای\s*تبلیغ|تماس\s*برای", re.I)
_CTA_WEAK = re.compile(
    r"support\s+us|donate|حمایت\s*مالی|کانال\s*رسمی|تبلیغات|عضویت\s*:", re.I)
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
            if kept and (_LINKS.search(tail) or _CTA_STRONG.search(tail)):
                break
            continue
        has_link = bool(_LINKS.search(line))
        bare = re.sub(r"[ \t]+", " ", _LINKS.sub("", line)).strip()
        if (not bare or _ONLY_TAGS.match(bare) or any(r.search(line) for r in extra_res)):
            continue
        sentences = [s for s in _SENTENCES.split(bare)
                     if not ((_CTA_STRONG.search(s) and (has_link or len(s) <= 160)) or (_CTA_WEAK.search(s) and has_link))]
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


_LEAD_DECOR = re.compile(r"^[^\w#]+")
# Seconds to wait before polling a channel again after Telegram answered HTTP 429.
_BACKOFF_UNTIL: dict[str, float] = {}


def message_text(body) -> str:
    """Plain text of a post. The old get_text("\\n", strip=True) put EVERY inline element (<b>, <a>, emoji) on
    its own line, so «🔴 <b>فوری:</b> صدای انفجار در <a>تبریز</a>» became five lines and the title was just «🔴»."""
    for br in body.find_all("br"):
        br.replace_with("\n")
    lines = (re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in body.get_text().splitlines())
    return "\n".join(line for line in lines if line)


def make_title(text: str) -> str:
    """First line that carries real words (leading emoji/bullets removed)."""
    lines = [line for line in text.splitlines() if line.strip()]
    for line in lines:
        cleaned = _LEAD_DECOR.sub("", line).strip()
        if len(cleaned) >= 8:
            return cleaned[:240]
    return (_LEAD_DECOR.sub("", lines[0]).strip() if lines else text)[:240] or text[:240]


def collect_telegram(src: dict) -> list[Item]:
    channel = src["channel"]
    if time.time() < _BACKOFF_UNTIL.get(channel, 0):
        STATUS[channel] = "backoff"
        return []
    soup = None
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            response = requests.get(f"https://t.me/s/{channel}", headers=UA, timeout=TIMEOUT)
            if response.status_code == 429:
                try:
                    wait = min(600.0, max(30.0, float(response.headers.get("Retry-After", "60"))))
                except ValueError:
                    wait = 60.0
                _BACKOFF_UNTIL[channel] = time.time() + wait
                STATUS[channel] = "backoff"
                print(f"WARN telegram {channel}: HTTP 429, pausing this channel for {int(wait)}s", file=sys.stderr)
                return []
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            break
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(1.5)
    if soup is None:
        STATUS[channel] = "error"
        print(f"WARN telegram {channel}: {config.redact(last_exc)}", file=sys.stderr)
        return []

    nodes = soup.select("div.tgme_widget_message")
    STATUS[channel] = "ok" if nodes else "empty"
    items: list[Item] = []
    for node in nodes[-40:]:
        post = node.get("data-post", "")
        body = node.select_one(".tgme_widget_message_text.js-message_text") or node.select_one(".tgme_widget_message_text")
        if not post or not body:
            continue
        raw_text = message_text(body)
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

        items.append(Item(
            uid=_uid("tg", post), origin="telegram", source=channel,
            title=make_title(text), text=text[:5000], url=f"https://t.me/{post}",
            published=when, media=media[:3],
        ))
    return items


def collect_all() -> list[Item]:
    with ThreadPoolExecutor(max_workers=min(8, len(config.TELEGRAM_SOURCES))) as pool:
        results = list(pool.map(collect_telegram, config.TELEGRAM_SOURCES))
    return [item for group in results for item in group]
