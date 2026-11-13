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
    translation_unavailable: bool = False


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


# ------------------------------------------------------------ Free API providers
# Groq Free Plan: primary and secondary model. OpenRouter's free router is
# the final provider; it only routes to free models (never paid models).
_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_GROQ_MODELS = ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]


def _request_chat(url: str, key: str, model: str, item: Item, provider: str) -> Post | None:
    content = (
        f"Internal source channel (never mention in output): {item.source}\n"
        f"Original headline: {item.title}\n"
        f"Original post text: {item.text or '(none)'}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content},
        ],
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/Yazdest/iran-radar-news"
        headers["X-Title"] = "IRAN RADAR"

    last_error = None
    for attempt in range(2):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=35)
            if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = min(15, max(2, float(retry_after))) if retry_after else 2 ** (attempt + 1)
                except ValueError:
                    delay = 2 ** (attempt + 1)
                last_error = RuntimeError(f"HTTP {response.status_code}; retry after {delay}s")
                if attempt == 0:
                    time.sleep(delay)
                    continue
                break
            response.raise_for_status()
            data = response.json()
            raw = data["choices"][0]["message"]["content"]
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("empty model response")
            return _parse(raw)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            # Authentication/configuration errors cannot be fixed by retrying this provider.
            if status in (400, 401, 403, 404, 422):
                raise
            last_error = exc
            break
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            last_error = exc
            break
    raise RuntimeError(f"{provider}/{model} failed: {last_error}")


def _parse(raw: str) -> Post | None:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError("AI response did not contain JSON")
    data = json.loads(match.group(0))
    if data.get("kind") == "skip":
        return None
    post = _finish(str(data.get("kind", "news")), str(data.get("title", "")), str(data.get("body", "")),
                   str(data.get("impact", "normal")), str(data.get("source", "")), str(data.get("emoji", "")))
    if not post.title or len(post.title) < 18 or (post.body and post.body.casefold() == post.title.casefold()):
        print("INFO editorial quality gate: skipped incomplete or repetitive item", file=sys.stderr)
        return None
    if post.title.casefold().startswith(("در واقع", "به نظر می رسد", "به نظر می‌رسد", "همچنان نیاز دارد")):
        print("INFO editorial quality gate: skipped vague headline", file=sys.stderr)
        return None
    return post


def _groq(item: Item) -> Post | None:
    if not config.GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")
    errors = []
    for model in _GROQ_MODELS:
        try:
            result = _request_chat(_GROQ_URL, config.GROQ_API_KEY, model, item, "groq")
            print(f"INFO rewrite succeeded: Groq/{model}", file=sys.stderr)
            return result
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            print(f"WARN Groq model {model} failed ({exc}); trying next model", file=sys.stderr)
    raise RuntimeError("Groq models unavailable: " + "; ".join(errors))


def _openrouter(item: Item) -> Post | None:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    result = _request_chat(
        _OPENROUTER_URL, config.OPENROUTER_API_KEY, "openrouter/free", item, "openrouter"
    )
    print("INFO rewrite succeeded: OpenRouter free router", file=sys.stderr)
    return result


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


def _publish_original_if_models_fail(item: Item) -> Post:
    """Never lose a relevant item solely because all rewrite/translation APIs failed."""
    title = (item.title or "").strip()
    body = (item.text or "").strip()
    if not title and not body:
        raise TranslationUnavailable("source item is empty")

    # Persian sources can be published as-is. English WFWitness posts remain in English
    # with an explicit notice; do not fabricate a Persian translation.
    if _is_persian(title + " " + body):
        title = title or _first_sentences(body, 1)[:90]
        body = body if body and body != title else ""
        kind = "breaking" if config.BREAKING_RE.search(title + " " + body) else "news"
        return Post(kind, title[:240], body[:3500], "normal")

    title = title[:240] or _first_sentences(body, 1)[:240]
    body = body[:3000] if body and body != title else ""
    kind = "breaking" if config.BREAKING_RE.search(title + " " + body) else "news"
    return Post(kind, title, body, "normal", "", "", True)


def make_post(item: Item) -> Post | None:
    """Try AI engines; if every one fails, publish the source text instead of dropping it."""
    engines = []
    if config.GROQ_API_KEY:
        engines.append(("groq", _groq))
    if config.OPENROUTER_API_KEY:
        engines.append(("openrouter", _openrouter))
    if item.origin != "tweet" and len(item.title) < 25 and len(item.text) < 60:
        return None  # a bare name or fragment: nothing meaningful to publish

    errors = []
    for name, fn in engines:
        try:
            return fn(item)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"WARN {name} rewrite failed ({exc}); trying next engine", file=sys.stderr)

    if config.ALLOW_FALLBACK:
        try:
            return _fallback(item)
        except Exception as exc:
            errors.append(f"translation fallback: {exc}")
            print(f"WARN translation fallback failed ({exc}); publishing original source text", file=sys.stderr)

    print("WARN all rewrite engines unavailable; publishing original source text. " + "; ".join(errors),
          file=sys.stderr)
    return _publish_original_if_models_fail(item)
