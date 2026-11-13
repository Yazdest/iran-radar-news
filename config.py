"""All settings live here. Secrets come from environment variables only."""
import os
import re


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ---------- secrets / destination ----------
BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
CHANNEL = _env("TELEGRAM_CHANNEL", "@RadarIranNews")      # where posts are published
SIGNATURE = _env("CHANNEL_SIGNATURE", "@RadarIranNews")    # last line of every post ("" to disable)
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")              # for fluent translation
ANTHROPIC_MODEL = _env("ANTHROPIC_MODEL", "claude-haiku-5-5")

# ---------- behaviour ----------
POLL_SECONDS = int(_env("POLL_SECONDS", "30"))        # delay between checks of all sources
RUN_MINUTES = float(_env("RUN_MINUTES", "0"))         # 0 = run forever (VPS); ~17 for GitHub Actions
MAX_AGE_MINUTES = int(_env("MAX_AGE_MINUTES", "180"))  # never publish items older than this
MAX_PER_CYCLE = int(_env("MAX_PER_CYCLE", "6"))        # flood guard per check
BOOTSTRAP = _env("BOOTSTRAP", "1") == "1"              # first ever run: mark current items as seen, publish nothing
DRY_RUN = _env("DRY_RUN", "0") == "1"                  # print posts instead of sending
SHOW_ARTICLE_LINK = _env("SHOW_ARTICLE_LINK", "0") == "1"  # add a link to the original article (RSS only)
STATE_FILE = _env("STATE_FILE", "state/seen.json")
DEFAULT_TAGS = "#ایران"

# ---------- sources ----------
_GN = "https://news.google.com/rss/search?q={q}+when:1d&hl=en-US&gl=US&ceid=US:en"

RSS_FEEDS = [
    {"name": "BBC", "fa": "بی‌بی‌سی", "url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"},
    {"name": "Al Jazeera", "fa": "الجزیره", "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    {"name": "France 24", "fa": "فرانس ۲۴", "url": "https://www.france24.com/en/rss"},
    {"name": "DW", "fa": "دویچه‌وله", "url": "https://rss.dw.com/rdf/rss-en-world"},
    {"name": "The Guardian", "fa": "گاردین", "url": "https://www.theguardian.com/world/iran/rss"},
    {"name": "NYT", "fa": "نیویورک‌تایمز", "url": "https://rss.nytimes.com/services/xml/rss/nyt/MiddleEast.xml"},
    {"name": "Sky News", "fa": "اسکای‌نیوز", "url": "https://feeds.skynews.com/feeds/rss/world.xml"},
    {"name": "CNN", "fa": "سی‌ان‌ان", "url": "http://rss.cnn.com/rss/edition_meast.rss"},
    # Reuters / AP no longer publish RSS: Google News search is used, only headlines are available.
    {"name": "Reuters", "fa": "رویترز", "gnews": True, "strip": "Reuters",
     "url": _GN.format(q="Iran+site:reuters.com")},
    {"name": "AP", "fa": "آسوشیتدپرس", "gnews": True, "strip": "AP News",
     "url": _GN.format(q="Iran+site:apnews.com")},
]

# Public Telegram channels (read through https://t.me/s/<channel>).
# filter_iran=False -> publish everything from that channel.
TELEGRAM_SOURCES = [
    {"channel": "wfwitness", "filter_iran": True},
]

# ---------- Iran relevance filter (whole-word match: "Tirana" no longer matches "iran") ----------
_TERMS = [
    "iran", "iranian", "iranians", "tehran", "isfahan", "esfahan", "tabriz", "shiraz", "mashhad",
    "kermanshah", "bushehr", "bandar abbas", "natanz", "fordow", "fordo", "persian gulf", "hormuz",
    "irgc", "revolutionary guard", "islamic republic", "khamenei", "pezeshkian", "araghchi",
    "ایران", "ایرانی", "تهران", "اصفهان", "تبریز", "شیراز", "مشهد", "کرمانشاه", "بوشهر", "بندرعباس",
    "نطنز", "فردو", "خلیج فارس", "تنگه هرمز", "سپاه", "جمهوری اسلامی", "خامنه‌ای", "پزشکیان", "عراقچی",
    "إيران", "طهران", "الحرس الثوري", "مضيق هرمز",
]
IRAN_RE = re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(re.escape(t) for t in _TERMS), re.I)

# Used only by the offline fallback (no AI key); the AI classifies by itself.
BREAKING_RE = re.compile(
    r"\b(breaking|urgent|just in|explosion|explosions|airstrike|strike|strikes|attack|attacked|missile|"
    r"killed|dead|assassinat\w*|shot down|arrested|earthquake)\b|عاجل|فوری|انفجار|حمله|موشک|کشته",
    re.I,
)
