"""Text helpers shared by main (dedup) and translator (grounding checks)."""
from __future__ import annotations

import re

import config

STOP = {
    "the", "and", "for", "with", "after", "over", "says", "said", "that", "this", "from", "amid",
    "است", "شد", "شود", "کرد", "کرده", "اعلام", "گفت", "گفته", "درباره", "برای", "از", "به", "در", "با",
    "این", "آن", "که", "را", "هم", "یک", "خبر", "فوری", "منتشر", "گزارش", "شده", "بود", "اند",
}

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MARKS = re.compile(r"[ً-ٰٟـ‎‏]")


def norm(text: str) -> str:
    """Normalise Persian/Arabic variants so the same event written two ways compares equal."""
    t = (text or "").replace("ي", "ی").replace("ك", "ک").replace("ة", "ه").replace("ۀ", "ه")
    t = t.replace("‌", " ")          # half-space (ZWNJ) -> space
    t = _MARKS.sub("", t)
    return t.translate(_DIGITS).lower()   # Persian/Arabic digits -> ASCII


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+|[ء-ۓ]+", norm(text)) if len(w) > 2 and w not in STOP}


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", norm(text)))


def numbers_loose(text: str) -> set[str]:
    """Like numbers() but «1,500» / «۱٬۵۰۰» count as 1500 (used to compare a rewrite with its source)."""
    value = re.sub(r"(?<=\d)[,٬،](?=\d{3}\b)", "", norm(text))
    return set(re.findall(r"\d+", value))


def places(text: str) -> set[str]:
    value = norm(text)
    return {name for name, rx in config.PLACE_PATTERNS if rx.search(value)}


def actions(text: str) -> set[str]:
    value = norm(text)
    return {name for name, rx in config.ACTION_PATTERNS if rx.search(value)}


# ---- advertising lines (wfwitness and others append casino / betting / crypto promos) -------------------------
_AD_LINE_RE = re.compile(
    r"\bcasino\b|\bsportsbook\b|\brainbet\b|\bcrypto\s+(?:casino|betting|gambling)\b|"
    r"\b(?:non|no)[-\s]?kyc\b|غیر\s*kyc|\bfree\s+spins?\b|\bpromo\s*code\b|\bbetting\s+(?:site|platform|bonus)\b|"
    r"\bwelcome\s+bonus\b|\bdeposit\s+bonus\b|\bbonus\s+up\s+to\b|\b\d+\s*%\s*bonus\b|\bplay\s+now\b|\bwin\s+big\b|"
    r"\b(?:use|enter)\s+(?:promo\s+)?code\b|\bsign\s*up\s+(?:now|today|and\s+get)\b|\bprediction\s+market\s+bonus\b|[+＋]\s*(?:18|۱۸)(?!\d)|(?<!\d)(?:18|۱۸)\s*[+＋]|"
    r"کازینو|شرط[\s\u200c]*بندی|سایت\s+شرط|کد\s*تخفیف|کد\s*پروموشن", re.I)
# A bare domain («Rainbet.com», «site.io/ref») on a short line is a promo, except well-known news/social hosts.
_BARE_DOMAIN_RE = re.compile(
    r"(?<![\w@.])(?!(?:x|twitter|youtube|youtu|t|truthsocial|instagram|facebook|telegram|wikipedia)\.)"
    r"[a-z0-9][a-z0-9-]{1,30}\.(?:com|io|net|org|bet|gg|me|co|xyz|app|ai|ly|tv|win|club|vip|casino|fun|top)\b", re.I)


def is_ad_line(line: str) -> bool:
    line = (line or "").strip()
    if not line:
        return False
    if _AD_LINE_RE.search(line):
        return True
    return len(line) <= 160 and bool(_BARE_DOMAIN_RE.search(line))


def shorten(text: str, cap: int) -> str:
    """Shorten to at most ~cap characters WITHOUT cutting a word: at a sentence end if there is one,
    otherwise at a space with «…». (A hard text[:cap] produced titles like «…عربس» + «تان سعودی».)"""
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    window = text[:cap + 1]
    cut = max(window.rfind(ch) for ch in ".!؟?؛")
    if cut >= 40:
        return text[:cut + 1].rstrip()
    space = window.rfind(" ")
    if space >= 40:
        return text[:space].rstrip(" ،,;:-–—") + "…"
    return text[:cap]
