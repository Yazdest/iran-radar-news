"""Configuration for IRAN RADAR. Only the Telegram sources selected by the owner are polled."""
import os
import re


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
CHANNEL = _env("TELEGRAM_CHANNEL", "@RadarIranNews")
SIGNATURE = _env("CHANNEL_SIGNATURE", CHANNEL if CHANNEL.startswith("@") else "")

# Free-tier text providers.
GROQ_API_KEY = _env("GROQ_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")

POLL_SECONDS = int(_env("POLL_SECONDS", "30"))
RUN_MINUTES = float(_env("RUN_MINUTES", "0"))
MAX_AGE_MINUTES = int(_env("MAX_AGE_MINUTES", "360"))
MAX_PER_CYCLE = int(_env("MAX_PER_CYCLE", "20"))
BOOTSTRAP = _env("BOOTSTRAP", "0") == "1"
DRY_RUN = _env("DRY_RUN", "0") == "1"
ALLOW_FALLBACK = _env("ALLOW_FALLBACK", "0") == "1"
STATE_FILE = _env("STATE_FILE", "state/seen.json")

# Keep the seven Telegram sources currently configured in the public main branch.
TELEGRAM_SOURCES = [
    {"channel": "ScopeHQ", "strip_lines": []},
    {"channel": "entekhab_ir", "strip_lines": []},
    {"channel": "P_raetorio", "strip_lines": []},
    {"channel": "iran_jahan_darlahze", "strip_lines": []},
    {"channel": "IranintlTV", "strip_lines": []},
    {"channel": "eskannews_com", "strip_lines": []},
    {"channel": "Minas_Tirith2024", "strip_lines": []},
]
# sources.py in the current branch reads these collections; keep them explicitly empty
# so the configured Telegram-only source list remains authoritative.
RSS_FEEDS = []
TWEET_ACCOUNTS = []

RELEVANT_RE = re.compile(
    r"جنگ|حمله|حملات|موشک|پهپاد|پدافند|انفجار|عملیات نظامی|درگیری|تلافی|تشدید تنش|"
    r"آتش.?بس|مذاکر(?:ه|ات)|گفت.?وگو|توافق|میز مذاکره|تحریم|هسته.?ای|غنی.?سازی|تنگه هرمز|"
    r"ترامپ|روبیو|نتانیاهو|کاتس|ارتش اسرائیل|سپاه|عراقچی|پزشکیان|خامنه.?ای|"
    r"موضع|اظهارات|هشدار|تهدید|بیانیه|تصمیم|"
    r"(?:ایران|آمریکا|اسرائیل|واشنگتن|تل.?آویو).{0,35}(?:موضع|اظهارات|هشدار|تهدید|بیانیه|تصمیم)|"
    r"(?:موضع|اظهارات|هشدار|تهدید|بیانیه|تصمیم).{0,35}(?:ایران|آمریکا|اسرائیل|واشنگتن|تل.?آویو)|"
    r"دلار|نرخ ارز|تومان|ریال|تورم|شاخص قیمت|نفت|اوپک|بنزین|انرژی|بورس|بازار سهام|طلا|سکه|رمزارز|"
    r"بودجه|رشد اقتصادی|تولید ناخالص|صادرات|واردات|قیمت جهانی|اقتصاد ایران|"
    r"\b(war|attack|airstrike|strike|missile|drone|air defense|explosion|military operation|"
    r"retaliation|escalation|ceasefire|negotiat\w*|talks|agreement|deal|sanction\w*|"
    r"nuclear|enrichment|hormuz|trump|rubio|netanyahu|israel\s+(says|said|warns|announces)|"
    r"iran\s+(says|said|warns|announces)|dollar|rial|inflation|oil price|opec|energy|"
    r"stock market|economic data|gdp|budget|exports|imports|gold|cryptocurrency)\b",
    re.I,
)

BREAKING_RE = re.compile(
    r"حمله موشکی|حمله هوایی|حمله آغاز|انفجار|اصابت موشک|کشته شدن|ترور|اعلام جنگ|"
    r"\b(airstrike|missile strike|explosion|assassinat\w*|killed|declares? war|breaking|just in)\b",
    re.I,
)
