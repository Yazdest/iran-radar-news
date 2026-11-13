"""All settings live here. Secrets come from environment variables only."""
import os
import re


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ---------- secrets / destination ----------
BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
CHANNEL = _env("TELEGRAM_CHANNEL", "@RadarIranNews")
SIGNATURE = _env("CHANNEL_SIGNATURE", CHANNEL if CHANNEL.startswith("@") else "")   # channel id printed in every post
BRAND_EN = _env("BRAND_EN", "IRAN RADAR")          # printed on the images
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-haiku-5-5")
GEMINI_API_KEY = _env("GEMINI_API_KEY")            # free key from aistudio.google.com
GEMINI_MODEL = _env("GEMINI_MODEL")                 # leave empty: the newest free Flash model is found automatically

# ---------- behaviour ----------
POLL_SECONDS = int(_env("POLL_SECONDS", "30"))
RUN_MINUTES = float(_env("RUN_MINUTES", "0"))          # 0 = forever (VPS); 4 for GitHub Actions
MAX_AGE_MINUTES = int(_env("MAX_AGE_MINUTES", "360"))
MAX_PER_CYCLE = int(_env("MAX_PER_CYCLE", "12"))
BOOTSTRAP = _env("BOOTSTRAP", "0") == "1"
DRY_RUN = _env("DRY_RUN", "0") == "1"
ALLOW_FALLBACK = _env("ALLOW_FALLBACK", "1") == "1"       # 1 = try Google Translate, then MyMemory, if AI engines fail
SHOW_ARTICLE_LINK = _env("SHOW_ARTICLE_LINK", "0") == "1"   # link the outlet name (normal RSS items only)
STATE_FILE = _env("STATE_FILE", "state/seen.json")

# ---------- sources: a few strong ones ----------
def _gn(query: str, lang: str = "en", country: str = "US") -> str:
    return (f"https://news.google.com/rss/search?q={query}+when:1d"
            f"&hl={lang}&gl={country}&ceid={country}:{lang}")


RSS_FEEDS = [
    {"name": "BBC", "fa": "بی‌بی‌سی", "url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"},
    {"name": "Al Jazeera", "fa": "الجزیره", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    # Google News search = one feed for several outlets; the outlet name is read from each headline.
    # Wires (Reuters / AP / Bloomberg / Axios) have no RSS of their own any more.
    {"name": "Wires", "gnews": True,
     "url": _gn("Iran+(site:reuters.com+OR+site:apnews.com+OR+site:bloomberg.com+OR+site:axios.com+OR+site:timesofisrael.com)")},
    # Focused conflict queries catch headlines that omit the word Iran.
    {"name": "Conflict radar", "gnews": True,
     "url": _gn('("Iran" OR "Iranian") (Israel OR Israeli OR US OR Trump) (war OR strike OR attack OR missile OR military)')},
    {"name": "Military developments", "gnews": True,
     "url": _gn('Iran (airstrike OR missile OR nuclear OR enrichment OR retaliation OR explosion OR "military operation")')},
    {"name": "Diplomacy and officials", "gnews": True,
     "url": _gn('Iran (Trump OR Rubio OR Netanyahu OR "Iranian officials" OR negotiations OR sanctions)')},
    # Israeli TV / radio: Kan, Channel 12 (Mako), Channel 13, i24NEWS, Ynet  (Hebrew headlines)
    {"name": "Israeli TV", "gnews": True,
     "url": _gn("איראן+(site:kan.org.il+OR+site:mako.co.il+OR+site:13tv.co.il+OR+site:i24news.tv+OR+site:ynet.co.il)",
                "he", "IL")},
    # Iranian state / IRGC-linked media
    {"name": "Iranian media", "gnews": True,
     "url": _gn("Iran+(site:presstv.ir+OR+site:tasnimnews.com+OR+site:en.mehrnews.com+OR+site:en.irna.ir+OR+site:farsnews.ir)")},
    # Trump's own posts (Truth Social mirror)
    {"name": "Truth Social", "fa": "ترامپ (تروث سوشال)", "url": "https://trumpstruth.org/feed"},
]

# strip_lines: your own regexes; any line matching one is deleted (add the channel's typical ad/footer wording here).
TELEGRAM_SOURCES = [
    {"channel": "wfwitness", "filter_iran": True, "strip_lines": []},
]

# Officials' tweets: read through Nitter RSS, details/photos through api.fxtwitter.com, then drawn as a tweet card.
# Official accounts are passed to the AI editor even when a tweet omits the word Iran;
# the editor applies the Iran-conflict context filter. Public Nitter instances are unreliable.
NITTER_INSTANCES = [u.strip() for u in _env(
    "NITTER_INSTANCES", "https://nitter.net,https://nitter.poast.org,https://xcancel.com").split(",") if u.strip()]
TWEET_ACCOUNTS = [
    {"user": "realDonaldTrump", "fa": "دونالد ترامپ", "filter": False},
    {"user": "marcorubio", "fa": "مارکو روبیو", "filter": False},
    {"user": "PeteHegseth", "fa": "پیت هگست", "filter": False},
    {"user": "netanyahu", "fa": "بنیامین نتانیاهو", "filter": False},
    {"user": "Israel_katz", "fa": "یسرائیل کاتس", "filter": False},
    {"user": "IDF", "fa": "ارتش اسرائیل", "filter": False},
    {"user": "araghchi", "fa": "عباس عراقچی", "filter": False},
    {"user": "mb_ghalibaf", "fa": "محمدباقر قالیباف", "filter": False},
    {"user": "drpezeshkian", "fa": "مسعود پزشکیان", "filter": False},
    {"user": "khamenei_ir", "fa": "آیت‌الله خامنه‌ای", "filter": False},
]

# Public portraits used on speaker-led statement cards. Keep attribution in README.
SPEAKER_PORTRAITS = {
    "ترامپ": {
        "patterns": ["ترامپ", "دونالد ترامپ", "donald trump", "realDonaldTrump"],
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Official_Presidential_Portrait_of_President_Donald_J._Trump_(2025).jpg",
        "credit": "The White House / Public domain",
    },
    "روبیو": {
        "patterns": ["روبیو", "مارکو روبیو", "marco rubio", "marcorubio"],
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Official_Portrait_of_Secretary_Rubio_Headshot.jpg",
        "credit": "U.S. Department of State / Public domain",
    },
    "نتانیاهو": {
        "patterns": ["نتانیاهو", "بنیامین نتانیاهو", "benjamin netanyahu", "netanyahu"],
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Netanyahu_official_portrait.jpg",
        "credit": "Avi Ohayon / Government Press Office, CC BY-SA 3.0",
    },
    "قالیباف": {
        "patterns": ["قالیباف", "محمدباقر قالیباف", "mohammad bagher ghalibaf", "ghalibaf"],
        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/Mohammadbagher_Ghalibaf_in_2024.jpg",
        "credit": "Khamenei.ir, CC BY 4.0",
    },
}

# ---------- relevance: the item must be about Iran (whole-word match) ----------
_TERMS = [
    "iran", "iranian", "iranians", "tehran", "isfahan", "esfahan", "tabriz", "shiraz", "mashhad",
    "kermanshah", "bushehr", "bandar abbas", "natanz", "fordow", "fordo", "persian gulf", "hormuz",
    "irgc", "revolutionary guard", "islamic republic", "khamenei", "pezeshkian", "araghchi",
    "islamic revolutionary guard", "iran war", "iran conflict", "iran attack",
    "iran strike", "iran missile", "iran nuclear", "iran retaliation",
    "ایران", "حمله اسرائیل به ایران", "حمله آمریکا به ایران", "جنگ ایران",
    "موشک ایرانی", "حمله موشکی به ایران", "تأسیسات هسته‌ای ایران", "تاسیسات هسته‌ای ایران",
    "ایران", "ایرانی", "تهران", "اصفهان", "تبریز", "شیراز", "مشهد", "کرمانشاه", "بوشهر", "بندرعباس",
    "نطنز", "فردو", "خلیج فارس", "تنگه هرمز", "سپاه", "جمهوری اسلامی", "خامنه‌ای", "پزشکیان", "عراقچی",
    "إيران", "طهران", "الحرس الثوري", "مضيق هرمز",
]
_HEBREW = ["איראן", "איראני", "איראנים", "טהרן", "חמינאי", "המשמרות המהפכה", "פזשכיאן", "עראקצ'י"]

_main = "(?<!\\w)(?:%s)(?!\\w)" % "|".join(re.escape(t) for t in _TERMS)
_he = "(?<!\\w)[בלמהוכש]?(?:%s)(?!\\w)" % "|".join(re.escape(t) for t in _HEBREW)   # Hebrew prefixes: באיראן, מאיראן...
IRAN_RE = re.compile(f"{_main}|{_he}", re.I)
RELEVANT_RE = IRAN_RE

BREAKING_RE = re.compile(
    r"\b(breaking|urgent|just in|explosion|explosions|airstrike|strike|strikes|attack|attacked|missile|"
    r"killed|assassinat\w*|shot down|earthquake|war|retaliation|military operation|air defense|"
    r"nuclear facility|drone|bombardment)\b|عاجل|فوری|انفجار|حمله|موشک|جنگ|پاسخ نظامی|پدافند|پهپاد",
    re.I,
)
