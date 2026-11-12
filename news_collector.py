#!/usr/bin/env python3
"""Collect Iran-related items from RSS feeds and the public Telegram web preview.

This is a conservative first-stage collector: it never publishes directly.
Items are deduplicated locally and sent to Google Apps Script for human review.
"""
from __future__ import annotations
import hashlib, html, json, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import requests
import feedparser
from bs4 import BeautifulSoup

GAS_URL = os.getenv("APPS_SCRIPT_URL", "").strip()
GAS_SECRET = os.getenv("APPS_SCRIPT_SECRET", "").strip()
STATE_FILE = Path("state/seen.json")
TIMEOUT = 25
HEADERS = {"User-Agent": "IranRadarNewsBot/1.0 (+https://t.me/RadarIranNews)"}

# Feeds are intentionally limited to established international outlets.
RSS_FEEDS = {
    "BBC News": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml",
    "France 24": "https://www.france24.com/en/rss",
    "DW": "https://rss.dw.com/rdf/rss-en-world",
    "The Guardian": "https://www.theguardian.com/world/iran/rss",
}
TELEGRAM_CHANNEL = "wfwitness"
IRAN_TERMS = [
    "iran", "tehran", "isfahan", "tabriz", "shiraz", "kermanshah", "bushehr",
    "natanz", "fordow", "fordo", "persian gulf", "strait of hormuz",
    "iranian", "irgc", "islamic republic", "سپاه", "ایران", "تهران", "اصفهان",
    "تبریز", "شیراز", "کرمانشاه", "بوشهر", "نطنز", "فردو", "خلیج فارس", "تنگه هرمز"
]


def clean_text(value: str) -> str:
    value = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def item_id(source: str, url: str, title: str) -> str:
    raw = f"{source}|{url or ''}|{title}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()[:32]


def is_iran_related(text: str) -> bool:
    low = text.casefold()
    return any(term.casefold() in low for term in IRAN_TERMS)


def load_seen() -> set[str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data if isinstance(data, list) else [])
    except (OSError, ValueError):
        return set()


def save_seen(seen: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Bound state size to keep the Actions cache small.
    STATE_FILE.write_text(json.dumps(sorted(seen)[-12000:], ensure_ascii=False), encoding="utf-8")


def collect_rss() -> list[dict]:
    found = []
    for source, feed_url in RSS_FEEDS.items():
        try:
            response = requests.get(feed_url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for entry in feed.entries[:60]:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", entry.get("description", "")))
                link = entry.get("link", "")
                if not title or not link or not is_iran_related(f"{title} {summary}"):
                    continue
                found.append({
                    "source": source, "source_url": link, "title": title,
                    "summary": summary[:5000], "published_at": entry.get("published", ""),
                    "language": "auto", "media_url": "", "source_type": "rss"
                })
        except Exception as exc:
            print(f"WARN rss {source}: {exc}", file=sys.stderr)
    return found


def collect_telegram_public() -> list[dict]:
    """Read only the publicly accessible t.me/s preview; no user session required."""
    url = f"https://t.me/s/{TELEGRAM_CHANNEL}"
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        items = []
        for node in soup.select(".tgme_widget_message")[-40:]:
            body = node.select_one(".tgme_widget_message_text")
            if not body:
                continue
            text = clean_text(str(body))
            link_node = node.select_one(".tgme_widget_message_date")
            link = link_node.get("href", "") if link_node else ""
            if not text or not link or not is_iran_related(text):
                continue
            media = node.select_one(".tgme_widget_message_photo_wrap")
            media_url = ""
            if media:
                style = media.get("style", "")
                match = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
                media_url = match.group(1) if match else ""
            items.append({
                "source": "Witness (Telegram)", "source_url": link,
                "title": text[:180], "summary": text[:5000],
                "published_at": "", "language": "auto",
                "media_url": media_url, "source_type": "telegram"
            })
        return items
    except Exception as exc:
        print(f"WARN telegram public preview: {exc}", file=sys.stderr)
        return []


def main() -> None:
    if not GAS_URL or not GAS_SECRET:
        raise SystemExit("Missing APPS_SCRIPT_URL or APPS_SCRIPT_SECRET GitHub secrets")
    seen = load_seen()
    candidates = collect_rss() + collect_telegram_public()
    outgoing = []
    for item in candidates:
        uid = item_id(item["source"], item["source_url"], item["title"])
        if uid in seen:
            continue
        item["external_id"] = uid
        item["collected_at"] = datetime.now(timezone.utc).isoformat()
        outgoing.append(item)
    if not outgoing:
        print("No new Iran-related items.")
        return
    # Apps Script accepts bounded batches; retain unacknowledged items for retry.
    acknowledged = set()
    for start in range(0, len(outgoing), 15):
        batch = outgoing[start:start + 15]
        response = requests.post(
            GAS_URL,
            json={"secret": GAS_SECRET, "action": "ingest", "items": batch},
            timeout=90,
        )
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Apps Script rejected batch: {result}")
        acknowledged.update(result.get("accepted_ids", []))
        print(f"Submitted {len(batch)}; accepted {len(result.get('accepted_ids', []))}")
    seen.update(acknowledged)
    save_seen(seen)


if __name__ == "__main__":
    main()
