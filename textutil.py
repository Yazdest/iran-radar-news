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
