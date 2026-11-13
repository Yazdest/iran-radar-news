"""Fluent Persian news copy: Claude (paid) or Gemini (free tier), then plain Google translate as last fallback."""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass

import requests

import config
from sources import Item

KINDS = {"breaking", "news", "story"}
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


@dataclass
class Post:
    kind: str   # breaking | news | story
    title: str
    body: str


SYSTEM = """You are the night-desk editor of a premium Persian-language news channel on Telegram.
Turn the news item you receive (any language) into a polished Persian post. Reply with ONE JSON object only:
{"kind": "breaking" | "news" | "story" | "skip", "title": "...", "body": "..."}

kind:
- "breaking": something major has just happened or been announced (attack, strike, explosion, deaths, arrests, major official statement, sudden escalation).
- "story": narrative / eyewitness / human-interest / testimony / feature content told as a story.
- "news": ordinary news.
- "skip": advertising, pure promotion, off-topic, or no real news content.

Writing rules:
- Natural, fluent, journalistic Persian, as written by a native editor. Never word-for-word.
- Be exact with names, numbers, places, dates and quotes. Never add facts that are not in the item.
- Keep attribution when the item attributes a claim ("به گفته ارتش اسرائیل", "به ادعای ..."). Do NOT add your own remarks about verification, e.g. never write "تأییدنشده" yourself.
- Do not mention the outlet or channel name, no links, no hashtags, no emoji, no "breaking" word in the text.
- title: one punchy line, max 90 characters, no final period.
- body: lead with the most important fact. "breaking"/"news": 1-4 short sentences, max 600 characters. "story": max 850 characters, keep the narrative voice.
- Use Persian digits (۱۲۳), Persian punctuation (، ؛ ؟) and «» for quotes. Use the common Persian spelling of foreign names.
- If the input is already Persian, only polish it under the same rules."""


def _trim(value: str, limit: int) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    m = max(cut.rfind("."), cut.rfind("؟"), cut.rfind("!"))
    return cut[: m + 1] if m > limit * 0.5 else cut.rsplit(" ", 1)[0].rstrip("،, ") + "…"


def _finish(kind: str, title: str, body: str) -> Post:
    kind = kind if kind in KINDS else "news"
    title = _trim(title.translate(_FA_DIGITS), 110).rstrip(".")
    body = _trim(body.translate(_FA_DIGITS), 900 if kind == "story" else 650)
    if body.strip() == title.strip():
        body = ""
    return Post(kind, title, body)


# ------------------------------------------------------------ Claude
def _claude(item: Item) -> Post | None:
    content = f"Source: {item.source}\nTitle: {item.title}\nText: {item.text or '(none)'}"
    payload = {"model": config.ANTHROPIC_MODEL, "max_tokens": 900, "system": SYSTEM,
               "messages": [{"role": "user", "content": content}]}
    headers = {"x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
               "content-type": "application/json"}
    for attempt in range(3):
        r = requests.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload, timeout=40)
        if r.status_code in (429, 500, 502, 503, 529):
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return _parse("".join(b.get("text", "") for b in r.json().get("content", [])))
    raise RuntimeError("Claude API unavailable")


def _parse(raw: str) -> Post | None:
    data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    if data.get("kind") == "skip":
        return None
    return _finish(str(data.get("kind", "news")), str(data.get("title", "")), str(data.get("body", "")))


# ------------------------------------------------------------ Gemini (free tier)
def _gemini(item: Item) -> Post | None:
    content = f"Source: {item.source}\nTitle: {item.title}\nText: {item.text or '(none)'}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": content}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3, "maxOutputTokens": 1200},
    }
    for attempt in range(3):
        r = requests.post(url, headers={"x-goog-api-key": config.GEMINI_API_KEY}, json=payload, timeout=40)
        if r.status_code in (429, 500, 503):
            time.sleep(4 * (attempt + 1))
            continue
        r.raise_for_status()
        parts = r.json()["candidates"][0]["content"]["parts"]
        return _parse("".join(p.get("text", "") for p in parts))
    raise RuntimeError("Gemini API unavailable")


# ------------------------------------------------------------ fallback
def _is_persian(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text)
    return bool(letters) and sum("\u0600" <= c <= "\u06ff" for c in letters) / len(letters) > 0.6


def _google(text: str) -> str:
    text = text.strip()
    if not text or _is_persian(text):
        return text
    r = requests.get("https://translate.googleapis.com/translate_a/single", timeout=15,
                     params={"client": "gtx", "sl": "auto", "tl": "fa", "dt": "t", "q": text[:1800]})
    r.raise_for_status()
    return "".join(part[0] for part in r.json()[0] if part and part[0])


def _fallback(item: Item) -> Post:
    title = _google(item.title)
    body = _google(item.text) if item.text and item.text != item.title else ""
    if item.origin == "telegram" and body.startswith(title[:30]):
        body = body[len(title):].lstrip(" \n:،.") if len(body) > len(title) else ""
    kind = "breaking" if config.BREAKING_RE.search(f"{item.title} {item.text}") else "news"
    return _finish(kind, title, body)


def make_post(item: Item) -> Post | None:
    """Returns None when the item should not be published."""
    engines = []
    if config.ANTHROPIC_API_KEY:
        engines.append(("claude", _claude))
    if config.GEMINI_API_KEY:
        engines.append(("gemini", _gemini))
    for name, fn in engines:
        try:
            return fn(item)
        except Exception as exc:
            print(f"WARN {name} translation failed ({exc}); trying next engine", file=sys.stderr)
    return _fallback(item)
