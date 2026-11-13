"""Concise, independent Persian rewrite for the seven-source IRAN RADAR channel."""
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
    emoji: str = ""          # one emoji that fits the topic


SYSTEM = """You are the independent Persian-language editor of IRAN RADAR, a fast Telegram news channel.

SCOPE — publish ONLY concise, audience-focused items about:
1) Iran-related war, military operations, attacks, missiles, drones, defense and escalation;
2) negotiations, ceasefires, agreements, sanctions and diplomatic moves involving Iran, the United States or Israel;
3) statements, positions and decisions by Iranian, American and Israeli leaders, governments and militaries when relevant to these issues;
4) meaningful Iranian or regional economic data: currency, inflation, oil, energy, markets, sanctions, trade and major macroeconomic indicators.
Skip unrelated domestic administration, agriculture/livestock, routine official visits, sports, entertainment, crime, generic world news, gossip, long opinion pieces and posts that add no new information.

REWRITE — do NOT copy the source wording or sentence structure. Produce an original, natural Persian news brief in IRAN RADAR's own neutral, polished newsroom voice. Reorganize the sentence, use fresh but ordinary Persian phrasing, and write a new headline. Do not imitate the source channel's signature style. Do not mention the source channel, its username, or phrases such as «این کانال نوشت» / «این رسانه گزارش داد». The source channel is internal metadata only.

READING TIME — the reader should understand the main point in about 5–6 seconds:
- title: one direct, complete headline, preferably 8–14 Persian words and at most 90 characters.
- body: normally one short sentence, at most two; target 100–220 characters. For a major breaking event, up to 300 characters.
- no introduction, history lesson, long storytelling, repeated headline, filler, or decorative conclusion.
- preserve the key actor, action, location, time and important numbers when present. Keep uncertainty and attribution that belong to the claim (e.g. «به گفته ارتش اسرائیل»); never turn an allegation into established fact.
- Do not invent facts, explanations, motives, or consequences. If the text is too vague to make a clear short post, return {"kind":"skip"}.

CLASSIFICATION — return exactly one JSON object:
{"kind":"breaking"|"statement"|"assessment"|"news","impact":"high"|"normal","title":"...","body":"...","emoji":"..."}
- breaking: a genuinely urgent, newly reported attack, strike, explosion, major casualty event or sudden escalation.
- statement: a consequential position or announcement by a named Iranian, US or Israeli official.
- assessment: a clearly attributed assessment or report that has a meaningful implication for the defined scope.
- news: other relevant developments.
- impact high only for major military escalation, consequential decisions, major diplomatic moves or important economic shocks.

HASHTAGS and source labels are added by the publisher. Do not put a hashtag, source/channel name, link or emoji in title/body. emoji must be exactly one suitable emoji, not 🚨 and not a flag.

STYLE: idiomatic, concise, formal Persian; correct نیم‌فاصله, Persian digits and punctuation. If the input is already Persian, rewrite it fully under these rules rather than merely correcting spelling. Remove ads, subscription prompts, signatures, channel IDs, hashtags and promotional footers. If nothing newsworthy remains, return {"kind":"skip"}."""


class TranslationUnavailable(Exception):
    """No AI engine could process the item (it will be retried, not published badly)."""


def _fit(text: str, cap: int) -> str:
    """Never cut a sentence or a word. Keep the full text if no safe boundary exists."""
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    ends = [text.rfind(ch, 0, cap + 1) for ch in ".!؟?؛"]
    cut = max(ends)
    if cut >= int(cap * 0.55):
        return text[:cut + 1].rstrip()
    return text


def _clean_emoji(value: str) -> str:
    value = (value or "").strip()
    ok = 0 < len(value) <= 8 and not re.search(r"[A-Za-z0-9\u0600-\u06ff]", value) and "🚨" not in value
    return value if ok else ""


def _finish(kind: str, title: str, body: str, impact: str = "normal", source: str = "", emoji: str = "") -> Post:
    kind = kind if kind in KINDS else "news"
    impact = "high" if impact == "high" else "normal"
    title = (title or "").translate(_FA_DIGITS).replace("…", "").replace("...", "").strip().rstrip(".")
    body = (body or "").translate(_FA_DIGITS).replace("…", "").replace("...", "").strip()
    if len(title) > 90:
        title = _fit(title, 90)
    if body.strip() == title.strip():
        body = ""
    body = _fit(body, 300 if impact == "high" else 220)
    # Source/channel names are intentionally never part of the public post.
    return Post(kind, title, body, impact, "", _clean_emoji(emoji))


# ------------------------------------------------------------ Claude
def _claude(item: Item) -> Post | None:
    content = f"Internal source channel (never mention in output): {item.source}\nOriginal headline: {item.title}\nOriginal post text: {item.text or '(none)'}"
    payload = {"model": config.ANTHROPIC_MODEL, "max_tokens": 1600, "system": SYSTEM,
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
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("AI response did not contain JSON")
    data = json.loads(match.group(0))
    if data.get("kind") == "skip":
        return None
    post = _finish(str(data.get("kind", "news")), str(data.get("title", "")), str(data.get("body", "")),
                   str(data.get("impact", "normal")), str(data.get("source", "")), str(data.get("emoji", "")))
    # Reject vague fragments, but allow a complete short alert whose headline carries the whole update.
    if not post.title or len(post.title) < 18 or (post.body and post.body.casefold() == post.title.casefold()):
        print("INFO editorial quality gate: skipped incomplete or repetitive item", file=sys.stderr)
        return None
    if post.title.casefold().startswith(("در واقع", "به نظر می رسد", "به نظر می‌رسد", "همچنان نیاز دارد")):
        print("INFO editorial quality gate: skipped vague headline", file=sys.stderr)
        return None
    return post


# ------------------------------------------------------------ Gemini (free tier)
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GEMINI_DEFAULTS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash",
                    "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash-lite"]
_gemini_models: list[str] | None = None     # candidates, best first (model names change often: auto-discovered)
_gemini_good: str | None = None             # the model that last worked


def _discover_gemini_models() -> list[str]:
    """Ask the API which models this key can use, newest Flash first (so a retired name never breaks the bot)."""
    found: list[str] = []
    try:
        r = requests.get(f"{_GEMINI_BASE}/models", params={"pageSize": 200},
                         headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=15)
        r.raise_for_status()
        skip = ("tts", "image", "live", "omni", "audio", "embed", "robot", "computer", "veo", "imagen", "native")
        for m in r.json().get("models", []):
            name = m.get("name", "").removeprefix("models/")
            if ("generateContent" in m.get("supportedGenerationMethods", []) and "flash" in name
                    and not any(s in name for s in skip)):
                found.append(name)
    except Exception as exc:
        print(f"WARN could not list Gemini models ({exc})", file=sys.stderr)

    def rank(name: str):
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        return (-(float(v.group(1)) if v else 0), "lite" in name, "preview" in name or "exp" in name, name)

    return sorted(set(found), key=rank)


def _gemini_candidates() -> list[str]:
    global _gemini_models
    if _gemini_models is None:
        _gemini_models = _discover_gemini_models()
    order = ([config.GEMINI_MODEL] if config.GEMINI_MODEL else []) + ([_gemini_good] if _gemini_good else []) \
        + _gemini_models + _GEMINI_DEFAULTS
    return list(dict.fromkeys(m for m in order if m))


def _gemini(item: Item) -> Post | None:
    global _gemini_good
    content = f"Internal source channel (never mention in output): {item.source}\nOriginal headline: {item.title}\nOriginal post text: {item.text or '(none)'}"
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": content}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3, "maxOutputTokens": 3072},
    }
    errors = []
    for model in _gemini_candidates()[:3]:
        r = None
        for attempt in range(2):
            try:
                r = requests.post(f"{_GEMINI_BASE}/models/{model}:generateContent",
                                  headers={"x-goog-api-key": config.GEMINI_API_KEY}, json=payload, timeout=30)
            except requests.RequestException as exc:      # timeout / network: try the next model right away
                errors.append(f"{model}: {type(exc).__name__}")
                r = None
                break
            if r.status_code in (429, 500, 503) and attempt == 0:
                time.sleep(4)
                continue
            break
        if r is None:
            continue
        if r.status_code == 200:
            _gemini_good = model
            parts = r.json()["candidates"][0]["content"]["parts"]
            return _parse("".join(p.get("text", "") for p in parts))
        errors.append(f"{model}: HTTP {r.status_code}")
        if r.status_code in (401, 403) and "API key" in r.text:
            break                                   # the key itself is wrong: other models will not help
    raise RuntimeError("Gemini failed: " + "; ".join(errors))


# ------------------------------------------------------------ fallback
def _is_persian(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text)
    return bool(letters) and sum("\u0600" <= c <= "\u06ff" for c in letters) / len(letters) > 0.6


def _split_for_translation(text: str, limit: int = 3800) -> list[str]:
    """Split long text at paragraph/sentence/word boundaries without dropping any part."""
    text = (text or "").strip()
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = max(text.rfind("\n", 0, limit), text.rfind(". ", 0, limit),
                  text.rfind("! ", 0, limit), text.rfind("? ", 0, limit),
                  text.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        else:
            cut += 1
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return [chunk for chunk in chunks if chunk]


def _google(text: str) -> str:
    """Google's public translate endpoint, chunked so long articles are never silently cut."""
    text = (text or "").strip()
    if not text or _is_persian(text):
        return text
    translated_chunks = []
    for chunk in _split_for_translation(text):
        r = requests.get("https://translate.googleapis.com/translate_a/single", timeout=30,
                         params={"client": "gtx", "sl": "auto", "tl": "fa", "dt": "t", "q": chunk})
        r.raise_for_status()
        data = r.json()
        translated = "".join(part[0] for part in data[0] if part and part[0]).strip()
        if not translated or not _is_persian(translated):
            raise ValueError("Google Translate returned empty or non-Persian text")
        translated_chunks.append(translated)
    return "\n\n".join(translated_chunks)


def _mymemory(text: str) -> str:
    """Secondary public fallback. Split long input to avoid silently truncating it."""
    text = (text or "").strip()
    if not text or _is_persian(text):
        return text
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= 450:
            chunk, remaining = remaining, ""
        else:
            cut = remaining.rfind(" ", 0, 450)
            if cut < 200:
                cut = 450
            chunk, remaining = remaining[:cut], remaining[cut:].lstrip()
        r = requests.get(
            "https://api.mymemory.translated.net/get", timeout=20,
            params={"q": chunk, "langpair": "autodetect|fa"},
        )
        r.raise_for_status()
        data = r.json()
        translated = (data.get("responseData") or {}).get("translatedText", "").strip()
        if not translated or not _is_persian(translated):
            raise ValueError("MyMemory returned empty or non-Persian text")
        chunks.append(translated)
    return " ".join(chunks)


def _translate_with_fallback(text: str) -> str:
    """Try independent fallback providers in order; fail closed if neither works."""
    text = (text or "").strip()
    if not text or _is_persian(text):
        return text
    errors = []
    for name, engine in (("Google Translate", _google), ("MyMemory", _mymemory)):
        try:
            result = engine(text)
            if result and _is_persian(result):
                print(f"INFO fallback translation succeeded with {name}", file=sys.stderr)
                return result
            raise ValueError("empty or non-Persian translation")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"WARN {name} fallback failed ({exc}); trying next translator", file=sys.stderr)
    raise TranslationUnavailable("All fallback translators failed: " + "; ".join(errors))


def _first_sentences(text: str, n: int) -> str:
    parts = re.split(r"(?<=[.!؟?])\s+", text.strip())
    return " ".join(parts[:n])


def _fallback(item: Item) -> Post:
    # Machine translation can translate source facts, but cannot safely add missing editorial context.
    if not item.text or len(item.text.strip()) < 120:
        raise TranslationUnavailable("source excerpt too short for a complete, contextual post")
    title = _translate_with_fallback(item.title)
    body = _translate_with_fallback(item.text) if item.text and item.text != item.title else ""
    if item.origin == "telegram" and body.startswith(title[:30]):
        body = body[len(title):].lstrip(" \n:،.") if len(body) > len(title) else ""
    kind = "breaking" if config.BREAKING_RE.search(f"{item.title} {item.text}") else "news"
    # Do not trim fallback output to two sentences: this was silently removing facts
    # from longer source reports and leaving some translations incomplete.
    return _finish(kind, title, body, source=item.source)


def make_post(item: Item) -> Post | None:
    """Returns None when the item should not be published."""
    engines = []
    if config.ANTHROPIC_API_KEY:
        engines.append(("claude", _claude))
    if config.GEMINI_API_KEY:
        engines.append(("gemini", _gemini))
    if item.origin != "tweet" and len(item.title) < 25 and len(item.text) < 60:
        return None                                  # a bare name or fragment: nothing to report
    last = None
    for name, fn in engines:
        try:
            return fn(item)
        except Exception as exc:
            last = exc
            print(f"WARN {name} translation failed ({exc}); trying next engine", file=sys.stderr)
    if config.ALLOW_FALLBACK:
        try:
            return _fallback(item)
        except Exception as exc:
            last = exc
            print(f"WARN all fallback translators failed ({exc}); retaining item for retry", file=sys.stderr)
    raise TranslationUnavailable(str(last) if last else "no AI key configured")
