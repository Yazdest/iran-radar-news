"""Configuration for IRAN RADAR. Only the Telegram sources selected by the owner are polled."""
import os
import re


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
CHANNEL = _env("TELEGRAM_CHANNEL", "@RadarIranNews")
SIGNATURE = _env("CHANNEL_SIGNATURE", "@RadarIranNews")
CHANNEL_JOIN_URL = _env("CHANNEL_JOIN_URL", "https://t.me/RadarIranNews")

GROQ_API_KEY = _env("GROQ_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")

POLL_SECONDS = int(_env("POLL_SECONDS", "30"))
RUN_MINUTES = float(_env("RUN_MINUTES", "0"))
MAX_AGE_MINUTES = int(_env("MAX_AGE_MINUTES", "360"))
# Publish every eligible non-duplicate story collected in a cycle.
PUBLISH_INTERVAL_SECONDS = max(1, int(_env("PUBLISH_INTERVAL_SECONDS", "60")))
BOOTSTRAP = _env("BOOTSTRAP", "0") == "1"
DRY_RUN = _env("DRY_RUN", "0") == "1"
# AI rewriting is required. Plain machine translation is disabled unless explicitly enabled.
ALLOW_FALLBACK = _env("ALLOW_FALLBACK", "0") == "1"
STATE_FILE = _env("STATE_FILE", "state/seen.json")

# The only source channels. Add/remove channels here when the owner requests it.
TELEGRAM_SOURCES = [
    {"channel": "ScopeHQ", "strip_lines": []},
    {"channel": "entekhab_ir", "strip_lines": []},
    {"channel": "P_raetorio", "strip_lines": []},
    {"channel": "iran_jahan_darlahze", "strip_lines": []},
    {"channel": "IranintlTV", "strip_lines": []},
    {"channel": "eskannews_com", "strip_lines": []},
    {"channel": "Minas_Tirith2024", "strip_lines": []},
    # English-language source: only posts explicitly marked with the Iran flag are eligible.
    {"channel": "wfwitness", "strip_lines": [], "require_iran_flag": True},
]

# Content scope: war/security, Iran-US-Israel positions, negotiations and macroeconomy.
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
