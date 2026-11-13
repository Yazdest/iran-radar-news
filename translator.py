"""Relevance check + short fluent Persian post: Claude (paid) or Gemini (free tier); plain Google translate as last resort."""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass

import requests

import config
from sources import Item

KINDS = {"breaking", "statement", "assessment", "news", "story"}
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


@dataclass
class Post:
    kind: str      # breaking | statement | assessment | news | story
    title: str
    body: str
    impact: str = "normal"   # high | normal
    source: str = ""         # Persian name of the outlet (when the AI supplied one)


SYSTEM = """You are the editor of a Persian-language Telegram news channel with ONE beat: Iran, and the confrontation between Iran, the United States and Israel.

STEP 1 - does the item belong? PUBLISH only if it is clearly about Iran AND is one of:
 a) statements, threats, decisions or positions of Trump and his cabinet, Netanyahu and the Israeli government or military, or Iranian officials, concerning Iran, US-Iran or Israel-Iran;
 b) an important military, nuclear, sanctions, diplomatic or security development involving Iran;
 c) an assessment, leak or report about Iran from Israeli TV or radio (Kan, Channel 12, Channel 13, Army Radio, i24NEWS) or from media close to the Iranian, American or Israeli governments.
Otherwise reply {"kind": "skip"}. Always skip: Gaza, Lebanon, Hamas, Hezbollah, Houthi, Syria or Iraq stories unless Iran's role is the core of the story; general world news; economy, sport, culture, crime, human interest; opinion columns; minor side stories and gossip; routine live-blog filler. When in doubt, skip.

STEP 2 - write the post. Reply with ONE JSON object only:
{"kind": "breaking" | "statement" | "assessment" | "news" | "story", "impact": "high" | "normal", "title": "...", "body": "...", "source": "..."}

kind:
- "breaking": something major has just happened or been announced (strike, attack, explosion, deaths, sudden escalation, major decision).
- "statement": remarks, threats or announcements by a named official. The title names the speaker and the gist ("ترامپ: ..."); the body gives the key quote or point.
- "assessment": an Israeli TV/radio or analyst assessment, leak or report. The title states the claim with its outlet.
- "news": any other qualifying news.
- "story": eyewitness or narrative content.
impact: "high" ONLY for consequential items (war, strikes, escalation, nuclear, a major threat or decision, a big diplomatic move). Everything else "normal".

BREVITY IS THE RULE:
- title: max 80 characters, one punchy line, no final period.
- body: for "normal" ONE short sentence, two at most (max 200 characters). For "high" at most four short sentences (max 380 characters). Give only what is needed: no background, no repetition of the title, no filler. If the title says it all, leave body empty.

Style and accuracy:
- Natural, fluent, journalistic Persian, never word-for-word. Exact names, numbers, places, dates, quotes. Never add facts.
- Keep attribution when the item attributes a claim ("به گفته ارتش اسرائیل", "به ادعای ..."). Do not add remarks about verification yourself.
- No links, no hashtags, no emoji, no outlet name inside title/body.
- Persian digits, Persian punctuation (، ؛ ؟), «» for quotes, common Persian spelling of foreign names.
- source: the outlet or channel name in common Persian spelling (e.g. رویترز, آسوشیتدپرس, کان, شبکه ۱۲); if the given Source is already Persian or a Telegram channel or an X account, copy it unchanged.
- If the input is already Persian, only edit it under the same rules."""


def _trim(value: str, limit: int) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    m = max(cut.rfind("."), cut.rfind("؟"), cut.rfind("!"))
    return cut[: m + 1] if m > limit * 0.5 else cut.rsplit(" ", 1)[0].rstrip("،, ") + "…"


def _finish(kind: str, title: str, body: str, impact: str = "normal", source: str = "") -> Post:
    kind = kind if kind in KINDS else "news"
    impact = "high" if impact == "high" else "normal"
    title = _trim(title.translate(_FA_DIGITS), 95).rstrip(".")
    body = _trim(body.translate(_FA_DIGITS), 400 if impact == "high" else 230)
    if body.strip() == title.strip():
        body = ""
    return Post(kind, title, body, impact, source.strip()[:40])


# ------------------------------------------------------------ Claude
def _claude(item: Item) -> Post | None:
    content = f"Source: {item.source}\nTitle: {item.title}\nText: {item.text or '(none)'}"
    payload = {"model": config.ANTHROPIC_MODEL, "max_tokens": 700, "system": SYSTEM,
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
    return _finish(str(data.get("kind", "news")), str(data.get("title", "")), str(data.get("body", "")),
                   str(data.get("impact", "normal")), str(data.get("source", "")))


# ------------------------------------------------------------ Gemini (free tier)
def _gemini(item: Item) -> Post | None:
    content = f"Source: {item.source}\nTitle: {item.title}\nText: {item.text or '(none)'}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": content}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3, "maxOutputTokens": 900},
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


def _first_sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!؟?])\s+", text.strip())
    return " ".join(parts[:n])


def _fallback(item: Item) -> Post:
    title = _google(item.title)
    body = _google(item.text) if item.text and item.text != item.title else ""
    if item.origin == "telegram" and body.startswith(title[:30]):
        body = body[len(title):].lstrip(" \n:،.") if len(body) > len(title) else ""
    kind = "breaking" if config.BREAKING_RE.search(f"{item.title} {item.text}") else "news"
    return _finish(kind, title, _first_sentences(body, 1))


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
