"""Officials' tweets: new posts via Nitter RSS, details and photos via api.fxtwitter.com."""
from __future__ import annotations

import calendar
import re
import sys
from datetime import datetime, timezone

import feedparser
import requests

import config
from sources import UA, Item, Media, _uid, strip_html

_warned: set[str] = set()


def _nitter_entries(user: str):
    for base in config.NITTER_INSTANCES:
        try:
            r = requests.get(f"{base}/{user}/rss", headers=UA, timeout=6)
            r.raise_for_status()
            entries = feedparser.parse(r.content).entries
            if entries:
                return entries
        except Exception:
            continue
    if user not in _warned:
        _warned.add(user)
        print(f"WARN tweets @{user}: no Nitter instance answered", file=sys.stderr)
    return []


def collect_tweets(acc: dict) -> list[Item]:
    items: list[Item] = []
    for e in _nitter_entries(acc["user"])[:10]:
        m = re.search(r"/status/(\d+)", e.get("link", ""))
        text = strip_html(e.get("title", ""))
        if not m or not text or text.startswith(("RT ", "R to ")):   # skip retweets and replies
            continue
        if acc.get("filter", True) and not config.RELEVANT_RE.search(text):
            continue
        t = e.get("published_parsed")
        when = datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else None
        tid = m.group(1)
        items.append(Item(
            uid=_uid("tweet", tid), origin="tweet", source=acc["fa"], title=text[:200], text=text,
            url=f"https://x.com/{acc['user']}/status/{tid}", published=when, skip_og=True,
            card={"id": tid, "handle": acc["user"], "name": acc["fa"], "text": text, "avatar": "", "ts": None},
        ))
    return items


def enrich(item: Item) -> None:
    """Fetch the full tweet (display name, avatar, exact text, photos/video) for a tweet we will publish."""
    c = item.card or {}
    try:
        r = requests.get(f"https://api.fxtwitter.com/{c['handle']}/status/{c['id']}", headers=UA, timeout=8)
        t = r.json()["tweet"]
    except Exception:
        return
    author = t.get("author") or {}
    c.update(name=author.get("name") or c.get("name", ""), avatar=author.get("avatar_url", ""),
             text=t.get("text") or c["text"], ts=t.get("created_timestamp"))
    item.text, item.title = c["text"], c["text"][:200]
    media = t.get("media") or {}
    item.media = ([Media("photo", p["url"]) for p in (media.get("photos") or [])[:3] if p.get("url")]
                  + [Media("video", v["url"]) for v in (media.get("videos") or [])[:1] if v.get("url")])
