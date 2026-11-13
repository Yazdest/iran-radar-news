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
    emoji: str = ""          # one emoji that fits the topic


SYSTEM = """You are the editor of a Persian-language Telegram news channel with ONE beat: Iran, and the confrontation between Iran, the United States and Israel.

STEP 1 - does the item belong? PUBLISH only if it is clearly about Iran AND is one of:
 a) statements, threats, decisions or positions of Trump and his cabinet, Netanyahu and the Israeli government or military, or Iranian officials, concerning Iran, US-Iran or Israel-Iran. For a post from one of these officials, the word "Iran" need not appear if the text clearly refers to the Iran confrontation (for example, retaliation, Tehran, Iranian nuclear sites, Hormuz, or the current military exchange);
 b) an important military, nuclear, sanctions, diplomatic or security development involving Iran;
 c) an assessment, leak or report about Iran from Israeli TV or radio (Kan, Channel 12, Channel 13, Army Radio, i24NEWS) or from media close to the Iranian, American or Israeli governments.
Otherwise reply {"kind": "skip"}. Always skip: Gaza, Lebanon, Hamas, Hezbollah, Houthi, Syria or Iraq stories unless Iran's role is the core of the story; general world news; economy, sport, culture, crime, human interest; opinion columns; minor side stories and gossip; routine live-blog filler. When in doubt, skip.

STEP 2 - write the post. Reply with ONE JSON object only:
{"kind": "breaking" | "statement" | "assessment" | "news" | "story", "impact": "high" | "normal", "title": "...", "body": "...", "source": "...", "emoji": "..."}

kind:
- "breaking": something major has just happened or been announced (strike, attack, explosion, deaths, sudden escalation, major decision).
- "statement": remarks, threats or announcements by a named official. The title names the speaker and the gist ("ترامپ: ..."); the body gives the key quote or point.
- "assessment": an Israeli TV/radio or analyst assessment, leak or report. The title states the claim with its outlet.
- "news": any other qualifying news.
- "story": eyewitness or narrative content.
impact: "high" ONLY for consequential items (war, strikes, escalation, nuclear, a major threat or decision, a big diplomatic move). Everything else "normal".

BREVITY AND COMPLETENESS:
- Write a clear, natural Persian news report, not a terse machine-like summary. Preserve the complete central information: who did what, where, when, why (if stated), figures, and attribution.
- title: a complete, informative Persian headline, preferably 8–18 words and no more than 110 characters. Never cut a sentence short or replace missing information with ellipses.
- body: normally 2–4 fluent sentences (up to 650 characters); for major developments up to 6 sentences (up to 1100 characters). Do not omit important facts merely to make the text shorter. Avoid repeating the headline, but include details needed to understand the event.
- If the source text is long, retain the facts in the article rather than dropping the final paragraphs. Never invent context or details.
- Input headlines may be truncated or cryptic. If you cannot understand the news well enough to write a complete, meaningful post, reply {"kind": "skip"}.

ADVERTISING AND SELF-PROMOTION - the input often has ads or footers mixed into the news. Delete ALL of it: calls to subscribe, follow, join or share; channel, group or social-media links and @handles; signatures and slogans; sponsor messages; VPN, discount, product, donation or business offers; "read more" lines. Never translate, summarize or paraphrase it and never mention the promoted party or the channel's own brand. Write the post only from the real news. If the item is mainly an advertisement, or no real news remains after removing it, reply {"kind": "skip"}.

Style and accuracy:
- Write idiomatic, polished Persian in the style of a professional Iranian newsroom. Avoid literal English word order, awkward loan translations, robotic phrasing, fragments, and repetitive sentence openings.
- Use active voice where natural; connect related facts smoothly. Prefer established Persian equivalents and correct Persian half-spaces (نیم‌فاصله).
- Exact names, numbers, places, dates, quotes. Never add facts. Never omit a clause just because it is difficult to translate.
- Keep attribution when the item attributes a claim ("به گفته ارتش اسرائیل", "به ادعای ..."). Do not add remarks about verification yourself.
- No links, no hashtags, no emoji and no outlet name inside title/body.
- emoji: exactly ONE emoji that fits the topic (e.g. 🚀 missiles, ☢️ nuclear, 🚢 Hormuz and sea, 💥 explosion, ⚖️ court, 🕊 talks and diplomacy, 🛡 defense, ✈️ air force, 🗣 a statement, 🛢 oil and energy, 🔍 intelligence). Never 🚨 and never a flag.
- Persian digits, Persian punctuation (، ؛ ؟), «» for quotes, common Persian spelling of foreign names.
- source: the outlet or channel name in common Persian spelling (e.g. رویترز, آسوشیتدپرس, کان, شبکه ۱۲); if the given Source is already Persian or a Telegram channel or an X account, copy it unchanged.
- If the input is already Persian, only edit it under the same rules."""


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
    if len(title) > 100:                       # too long for a headline: make it one single text
        body = f"{title}. {body}".strip() if body else title
        title = ""
    if body.strip() == title.strip():
        body = ""
    body = _fit(body, 3400)
    return Post(kind, title, body, impact, source.strip()[:40], _clean_emoji(emoji))


# ------------------------------------------------------------ Claude
def _claude(item: Item) -> Post | None:
    content = f"Source: {item.source}\nTitle: {item.title}\nText: {item.text or '(none)'}"
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
    data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    if data.get("kind") == "skip":
        return None
    return _finish(str(data.get("kind", "news")), str(data.get("title", "")), str(data.get("body", "")),
                   str(data.get("impact", "normal")), str(data.get("source", "")), str(data.get("emoji", "")))


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
    content = f"Source: {item.source}\nTitle: {item.title}\nText: {item.text or '(none)'}"
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
