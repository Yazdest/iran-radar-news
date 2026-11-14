"""Pre-publication filter for Persian spelling: city/island names, organisations, people, common terms and
typical machine-translation slips (mostly seen in posts translated from the English wfwitness channel).

Add a new pair to PAIRS when you spot a mistake: (regex of the WRONG form, correct form). Every pair is matched
as a whole word (optionally followed by a plural/ezafe suffix), so «کشم» (a verb) is never touched unless it is
written as an explicit pair such as «جزیره کشم»."""
from __future__ import annotations

import re

_L = "ء-يٱ-ۓ"                       # Persian/Arabic letters (ZWNJ is NOT a letter here)
_S = r"[\s\u200c]*"                 # optional space / half-space
_SP = r"[\s\u200c]+"                # required space / half-space

PAIRS: list[tuple[str, str]] = [
    # ---- places (Persian Gulf, islands, cities, sites)
    (r"هرموز|هورمز|هورموز|هرمُز", "هرمز"),
    (r"تنگه" + _S + r"ی" + _SP + "هرمز|تنگهٔ" + _SP + "هرمز", "تنگه هرمز"),
    (r"خارگ|خرگ", "خارک"),
    (r"جزیره" + _S + r"(?:ی" + _S + r")?(?:کشم|قیشم)", "جزیره قشم"),
    (r"قیشم", "قشم"),
    (r"ابو" + _SP + r"موسی|ابو" + _S + "موسا", "ابوموسی"),
    (r"تونب", "تنب"),
    (r"بندر" + _SP + r"عباس|باندار" + _S + "عباس", "بندرعباس"),
    (r"باب" + r"[\s\u200c\-]*ال[\s\u200c\-]*مندب", "باب‌المندب"),
    (r"خلیج" + _SP + "عمان", "دریای عمان"),
    (r"اسفهان|ایسفاهان|اصفحان", "اصفهان"),
    (r"طهران", "تهران"),
    (r"تابریز", "تبریز"),
    (r"ماشهد", "مشهد"),
    (r"ناتانز|نتانز", "نطنز"),
    (r"فوردو|فوردوو", "فردو"),
    (r"بوشر", "بوشهر"),
    (r"چابهر|چاباهار", "چابهار"),
    (r"احواز", "اهواز"),
    (r"ارمیه", "ارومیه"),
    (r"کرمانشا", "کرمانشاه"),
    (r"زاحدان", "زاهدان"),
    (r"تل" + _S + r"آویو|تلاویو|تل" + _S + "آبیب", "تل‌آویو"),
    (r"جروزالم|ژروزالم|یروشالایم", "اورشلیم"),
    (r"بیت" + _SP + "المقدس", "بیت‌المقدس"),
    (r"سنعا", "صنعا"),
    (r"هدیده", "حدیده"),
    (r"عین" + _S + r"ال" + _S + "اسد", "عین‌الاسد"),
    (r"بن" + _SP + "گوریون", "بن‌گوریون"),
    # ---- organisations / groups
    (r"گارد" + _S + r"انقلاب(?:ی)?(?:" + _S + "اسلامی)?", "سپاه پاسداران"),
    (r"نیروهای" + _SP + r"دفاعی" + _SP + "اسرائیل", "ارتش اسرائیل"),
    (r"هیزبولا|هزب" + _S + r"الله|حزب" + _SP + "الله", "حزب‌الله"),
    (r"هوثی", "حوثی"),
    (r"هامس", "حماس"),
    (r"سنت" + _S + "کام", "سنتکام"),
    (r"پنتاگان", "پنتاگون"),
    (r"موسساد", "موساد"),
    # ---- people
    (r"نتن" + _S + "یاهو", "نتانیاهو"),
    (r"ترامب|ترمپ", "ترامپ"),
    (r"عراغچی", "عراقچی"),
    (r"خامنه" + _SP + r"ای|خامنهای|خامنئی", "خامنه‌ای"),
    (r"هگسث|هگسس", "هگست"),
    (r"ویتکوف", "ویتکاف"),
    (r"کاتز", "کاتس"),
    # ---- terms
    (r"اسرایی?ل", "اسرائیل"),
    (r"آتش" + _SP + "بس", "آتش‌بس"),
    (r"پهباد", "پهپاد"),
    (r"غنی" + _SP + "سازی", "غنی‌سازی"),
    (r"غنی" + _SP + "شده", "غنی‌شده"),
    (r"هسته" + _S + r"ای|هسته" + _S + "یی", "هسته‌ای"),
    (r"سپاه" + _SP + "سپاه", "سپاه"),          # keep last: «سپاه گارد انقلاب» -> «سپاه سپاه پاسداران» -> «سپاه پاسداران»
]
_PAIR_RES = [
    (re.compile(rf"(?<![{_L}])(?:{wrong})(?=(?:ها|های|ی|یی|ان|ای)?(?![{_L}]))"), right) for wrong, right in PAIRS
]

# Latin leftovers inside an otherwise Persian text (the translator sometimes leaves an acronym or a name).
LATIN_TO_FA = {
    "IRGC": "سپاه پاسداران", "IDF": "ارتش اسرائیل", "CENTCOM": "سنتکام",
    "Tehran": "تهران", "Isfahan": "اصفهان", "Esfahan": "اصفهان", "Tabriz": "تبریز", "Shiraz": "شیراز",
    "Mashhad": "مشهد", "Hormuz": "هرمز", "Kharg": "خارک", "Qeshm": "قشم", "Bushehr": "بوشهر", "Jask": "جاسک",
    "Bandar Abbas": "بندرعباس", "Natanz": "نطنز", "Fordow": "فردو", "Chabahar": "چابهار",
}
_LATIN_RES = [(re.compile(rf"\b{re.escape(k)}\b", re.I), v) for k, v in sorted(LATIN_TO_FA.items(), key=lambda kv: -len(kv[0]))]

_ARABIC_LETTERS = str.maketrans({"ي": "ی", "ك": "ک", "ى": "ی", "ۀ": "ه", "ة": "ه"})
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "۰۱۲۳۴۵۶۷۸۹")
_VOWEL_MARKS_RE = re.compile("[\u064b-\u065f\u0670\u0640]")      # fatha, kasra, damma, shadda, tatweel ...

# Half-space (ZWNJ) slips: «می کنند» «هسته ای» «حمله ها» «بزرگ ترین».
_ZWNJ_RULES = [
    (re.compile(rf"(?<![{_L}])(نمی|می) (?=[{_L}])"), "\\1\u200c"),
    # after a non-joining letter (ا آ د ذ ر ز ژ و) the suffix is simply attached: «پهپادها», «بزرگ‌ترین»
    (re.compile(rf"(?<=[آادذرزژو]) (ها|های|ای|ترین)(?![{_L}])"), "\\1"),
    (re.compile(rf"(?<=[{_L}]) (ها|های|ای|ترین)(?![{_L}])"), "\u200c\\1"),
]
_JASK_RE = re.compile(rf"(?<![{_L}])جسک(?![{_L}])")      # strict: no suffix («جسکی‌ها» stays as it is)


def _mostly_persian(text: str) -> bool:
    letters = re.findall(r"[^\W\d_]", text)
    return bool(letters) and sum("\u0600" <= c <= "\u06ff" for c in letters) / len(letters) > 0.5


def fix(text: str) -> str:
    if not text:
        return text
    text = _VOWEL_MARKS_RE.sub("", text.translate(_ARABIC_LETTERS).translate(_ARABIC_DIGITS))
    text = _JASK_RE.sub("جاسک", text)
    for rx, right in _PAIR_RES:
        text = rx.sub(right, text)
    for rx, right in _ZWNJ_RULES:
        text = rx.sub(right, text)
    if _mostly_persian(text):
        for rx, right in _LATIN_RES:
            text = rx.sub(right, text)
    return text
