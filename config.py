"""All settings live here. Secrets come from environment variables only."""
import os
import re


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ---------- secrets / destination ----------
BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
CHANNEL = _env("TELEGRAM_CHANNEL", "@RadarIranNews")
CHANNEL_URL = _env("CHANNEL_URL", f"https://t.me/{CHANNEL.lstrip('@')}" if CHANNEL.startswith("@") else "")
BRAND_FA = _env("BRAND_FA", "رادار ایران")        # clickable name in the last line ("" to disable)
BRAND_EN = _env("BRAND_EN", "IRAN RADAR")          # printed on the images
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-haiku-5-5")
GEMINI_API_KEY = _env("GEMINI_API_KEY")            # free key from aistudio.google.com
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")

# ---------- behaviour ----------
POLL_SECONDS = int(_env("POLL_SECONDS", "30"))
RUN_MINUTES = float(_env("RUN_MINUTES", "0"))          # 0 = forever (VPS); 17 for GitHub Actions
MAX_AGE_MINUTES = int(_env("MAX_AGE_MINUTES", "180"))
MAX_PER_CYCLE = int(_env("MAX_PER_CYCLE", "6"))
BOOTSTRAP = _env("BOOTSTRAP", "1") == "1"
DRY_RUN = _env("DRY_RUN", "0") == "1"
ALLOW_FALLBACK = _env("ALLOW_FALLBACK", "0") == "1"       # 1 = publish with plain Google translation when no AI engine answers (poor quality)
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
# filter=True -> only tweets that mention Iran. Public Nitter instances are unreliable: add/replace as needed.
NITTER_INSTANCES = [u.strip() for u in _env(
    "NITTER_INSTANCES", "https://nitter.net,https://nitter.poast.org,https://xcancel.com").split(",") if u.strip()]
TWEET_ACCOUNTS = [
    {"user": "realDonaldTrump", "fa": "دونالد ترامپ", "filter": True},
    {"user": "marcorubio", "fa": "مارکو روبیو", "filter": True},
    {"user": "PeteHegseth", "fa": "پیت هگست", "filter": True},
    {"user": "netanyahu", "fa": "بنیامین نتانیاهو", "filter": True},
    {"user": "Israel_katz", "fa": "یسرائیل کاتس", "filter": True},
    {"user": "IDF", "fa": "ارتش اسرائیل", "filter": True},
    {"user": "araghchi", "fa": "عباس عراقچی", "filter": False},
    {"user": "drpezeshkian", "fa": "مسعود پزشکیان", "filter": False},
    {"user": "khamenei_ir", "fa": "آیت‌الله خامنه‌ای", "filter": False},
]

# ---------- relevance: the item must be about Iran (whole-word match) ----------
_TERMS = [
    "iran", "iranian", "iranians", "tehran", "isfahan", "esfahan", "tabriz", "shiraz", "mashhad",
    "kermanshah", "bushehr", "bandar abbas", "natanz", "fordow", "fordo", "persian gulf", "hormuz",
    "irgc", "revolutionary guard", "islamic republic", "khamenei", "pezeshkian", "araghchi",
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
    r"killed|assassinat\w*|shot down|earthquake)\b|عاجل|فوری|انفجار|حمله|موشک",
    re.I,
)
