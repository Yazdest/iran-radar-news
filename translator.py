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
    (r"رییس", "رئیس"),
    (r"مسوول|مسول", "مسئول"),
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
# Verb forms that do not exist in Persian (a model/MT builds them from the transitive stem «سوزاندن»):
# «سوزیدند» -> «سوختند». (The transitive «سوزاندند» = "they burned (something)" is correct and is left alone.)
_BURN_RE = re.compile(rf"(?<![{_L}])((?:ن?می[\s\u200c]*)?)سوزید(ند|م|ی|یم|ید|ه|ن)?(?![{_L}])")
_BURN_ENDINGS = {None: "سوخت", "": "سوخت", "ند": "سوختند", "م": "سوختم", "ی": "سوختی", "یم": "سوختیم",
                 "ید": "سوختید", "ه": "سوخته", "ن": "سوختن"}


def _fix_burn(match: "re.Match[str]") -> str:
    return match.group(1) + _BURN_ENDINGS.get(match.group(2) or "", "سوخت")


# «منفجرید» «غرقیدند» «نابودید» ...: a model/MT glues the past-tense ending «-ید» to a noun/adjective. Persian says
# «منفجر شد», «غرق شدند». (Only stems listed here are touched, so real verbs such as «ترکید» stay as they are.)
_PASSIVE_STEMS = "منفجر|غرق|نابود|مجروح|منهدم|ویران|مصدوم|متوقف|سرنگون|هلاک|مختل|متلاشی|مسدود|تخریب"
_COINED_RE = re.compile(rf"(?<![{_L}])((?:ن?می[\s\u200c]*)?)({_PASSIVE_STEMS})ید(ند|م|ی|یم|ید|ه|ن)?(?![{_L}])")
_SHOD = {"": "شد", "ند": "شدند", "م": "شدم", "ی": "شدی", "یم": "شدیم", "ید": "شدید", "ه": "شده", "ن": "شدن"}


def _fix_coined(match: "re.Match[str]") -> str:
    prefix = re.sub(r"[\s\u200c]+", "", match.group(1) or "")
    ending = match.group(3) or ""
    verb = _SHOD[ending]
    if prefix and ending not in ("ه", "ن"):
        verb = prefix + "\u200c" + verb
    return f"{match.group(2)} {verb}"


# Other typical machine-translation slips (word -> correct wording), regex + replacement.
_MISC: list[tuple["re.Pattern[str]", str]] = [
    (re.compile(rf"(?<![{_L}])آتش[\s\u200c]*گرفت(?=(?:ند|م|ی|یم|ید|ه)?(?![{_L}]))"), "آتش گرفت"),
    (re.compile(rf"(?<![{_L}])حمله[\s\u200c]+کرده[\s\u200c]+شد(ند)?(?![{_L}])"), lambda m: "مورد حمله قرار گرفت" + ("ند" if m.group(1) else "")),
    (re.compile(rf"(?<![{_L}])مورد[\s\u200c]+حمله[\s\u200c]+قرار[\s\u200c]+داده[\s\u200c]+شد(ند)?(?![{_L}])"),
     lambda m: "مورد حمله قرار گرفت" + ("ند" if m.group(1) else "")),
    (re.compile(rf"(?<![{_L}])(شلیک|پرتاب|اصابت)[\s\u200c]+کرده[\s\u200c]+شد(ند)?(?![{_L}])"), lambda m: f"{m.group(1)} شد" + ("ند" if m.group(2) else "")),
    (re.compile(rf"(?<![{_L}])انفجار[\s\u200c]+(کرد|کردند)(?![{_L}])"), lambda m: "منفجر " + ("شدند" if m.group(1) == "کردند" else "شد")),
    (re.compile(rf"(?<=ا)ئی(?![{_L}])"), "یی"),                       # دریائی -> دریایی، هوائی -> هوایی
    (re.compile(rf"(?<![{_L}])غیر[\s]+(قانونی|رسمی|نظامی|مجاز|مستقیم|منتظره|علنی|عادی|فعال)(?![{_L}])"), r"غیر\1"),
    (re.compile(rf"(?<![{_L}])(نمی|می)(?=(?:تواند|توانند|شود|شوند|کند|کنند|گوید|گویند|رود|روند|آید|آیند|دهد|دهند|خواهد|"
                rf"خواهند|دانند|دارد|دارند|کرد|کردند|شد|شدند|گفت|گفتند)(?![{_L}]))"), "\\g<0>\u200c"),
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
    text = _BURN_RE.sub(_fix_burn, text)
    text = _COINED_RE.sub(_fix_coined, text)
    for rx, right in _PAIR_RES:
        text = rx.sub(right, text)
    for rx, right in _MISC:
        text = rx.sub(right, text)
    for rx, right in _ZWNJ_RULES:
        text = rx.sub(right, text)
    if _mostly_persian(text):
        for rx, right in _LATIN_RES:
            text = rx.sub(right, text)
    return text
