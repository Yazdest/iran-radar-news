"""Configuration for IRAN RADAR. Only the Telegram sources selected by the owner are polled."""
import os
import re


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
CHANNEL = _env("TELEGRAM_CHANNEL", "@RadarIranNews")
SIGNATURE = _env("CHANNEL_SIGNATURE", "@RadarIranNews")
CHANNEL_JOIN_URL = _env("CHANNEL_JOIN_URL", "https://t.me/RadarIranNews")
# Optional: chat id of the owner. Receives a short alert when a source channel stops
# responding or when a story keeps failing to publish. Leave empty to disable alerts.
ADMIN_CHAT_ID = _env("ADMIN_CHAT_ID")

GROQ_API_KEY = _env("GROQ_API_KEY")
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
# Comma separated, tried in order. A model that answers "404 not found" is skipped for the rest of the run.
GROQ_MODELS = [m.strip() for m in _env("GROQ_MODELS", "openai/gpt-oss-120b,qwen/qwen3.8-27b").split(",") if m.strip()]

POLL_SECONDS = int(_env("POLL_SECONDS", "30"))
RUN_MINUTES = float(_env("RUN_MINUTES", "0"))
MAX_AGE_MINUTES = int(_env("MAX_AGE_MINUTES", "360"))
# Spacing between two consecutive posts of the same cycle. Telegram tolerates roughly one
# message per second per channel, so 3 seconds is fast and safe.
PUBLISH_INTERVAL_SECONDS = max(1, int(_env("PUBLISH_INTERVAL_SECONDS", "3")))
# Upper bound of stories rewritten/published in one cycle (the rest is handled by the next cycle).
MAX_PER_CYCLE = max(1, int(_env("MAX_PER_CYCLE", "12")))
# AI requests running in parallel. Free tiers rate-limit quickly, so keep this low.
AI_WORKERS = max(1, int(_env("AI_WORKERS", "2")))
# When the saved state is missing (cache evicted) only stories newer than this are published,
# so the channel is not flooded with old news.
FRESH_START_MAX_AGE_MINUTES = int(_env("FRESH_START_MAX_AGE_MINUTES", "60"))
# A new story is compared only with stories published during this window.
DEDUP_WINDOW_HOURS = float(_env("DEDUP_WINDOW_HOURS", "8"))
# After this many failed attempts (AI or Telegram) a story is dropped instead of retried forever.
MAX_FAILS = max(1, int(_env("MAX_FAILS", "5")))
# Alert the admin after this many consecutive polls in which a source returned nothing usable.
CHANNEL_ALERT_AFTER = max(1, int(_env("CHANNEL_ALERT_AFTER", "20")))
BOOTSTRAP = _env("BOOTSTRAP", "0") == "1"
DRY_RUN = _env("DRY_RUN", "0") == "1"
# Machine translation (Google/MyMemory) is used for NON-Persian stories when every AI engine failed.
ALLOW_FALLBACK = _env("ALLOW_FALLBACK", "1") == "1"
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
    # English-language source: a post is eligible when it carries the Iran flag or names Iran/Tehran/IRGC.
    # Remove "require_iran_flag" to accept every relevant post of this channel.
    {"channel": "wfwitness", "strip_lines": [], "require_iran_flag": True},
]

# Content scope: war/security, Iran-US-Israel positions, negotiations and macroeconomy.
# English words allow plural/-ed forms (missiles, drones, attacks, strikes...). The previous
# version used exact words with \b, so "missiles" or "drones" never matched.
RELEVANT_RE = re.compile(
    r"جنگ|حمله|حملات|تهاجم|موشک|پهپاد|پدافند|مقابله\s*هوایی|دفاع\s*هوایی|رادار|جنگنده|رهگیری|شلیک|بمباران|"
    r"انفجار|عملیات نظامی|درگیری|تلافی|تشدید تنش|آژیر|کشته|زخمی|"
    r"\bناو\b|ناوگان|ناوشکن|زیردریایی|پایگاه\s*(?:هوایی|نظامی)|سنتکام|پنتاگون|حوثی|حزب.?الله|"
    r"آتش.?بس|مذاکر(?:ه|ات)|گفت.?وگو|توافق|میز مذاکره|تحریم|هسته.?ای|غنی.?سازی|تنگه هرمز|"
    r"ترامپ|روبیو|نتانیاهو|کاتس|هگست|ونس|ارتش اسرائیل|سپاه|عراقچی|پزشکیان|خامنه.?ای|"
    r"موضع|اظهارات|هشدار|تهدید|بیانیه|تصمیم|"
    r"(?:ایران|آمریکا|اسرائیل|واشنگتن|تل.?آویو).{0,35}(?:موضع|اظهارات|هشدار|تهدید|بیانیه|تصمیم)|"
    r"(?:موضع|اظهارات|هشدار|تهدید|بیانیه|تصمیم).{0,35}(?:ایران|آمریکا|اسرائیل|واشنگتن|تل.?آویو)|"
    r"دلار|نرخ ارز|تومان|ریال|تورم|شاخص قیمت|نفت|اوپک|بنزین|انرژی|بورس|بازار سهام|طلا|سکه|رمزارز|"
    r"بودجه|رشد اقتصادی|تولید ناخالص|صادرات|واردات|قیمت جهانی|اقتصاد ایران|"
    r"\b(?:wars?|attack(?:s|ed|ing)?|air\s*strikes?|strikes?|struck|missiles?|drones?|air\s*defen[cs]e|"
    r"explosions?|blasts?|shelling|bombing|sirens?|intercept\w*|fighter\s*jets?|radar|"
    r"military\s+operations?|retaliat\w*|escalat\w*|cease-?fire|negotiat\w*|talks|agreements?|deals?|"
    r"sanction\w*|nuclear|enrichment|hormuz|trump|rubio|netanyahu|hegseth|vance|"
    r"iran(?:ian)?|tehran|israel(?:i)?|idf|irgc|pentagon|centcom|hezbollah|houthis?|"
    r"dollar|rial|inflation|oil\s+prices?|opec|energy|stock\s+market|economic\s+data|gdp|budget|"
    r"exports?|imports?|gold|cryptocurrency)\b",
    re.I,
)

BREAKING_RE = re.compile(
    r"حمله موشکی|حمله هوایی|حمله پهپادی|حمله آغاز|انفجار|اصابت موشک|کشته شدن|ترور|اعلام جنگ|"
    r"بمباران|آژیر|رهگیری|شلیک موشک|فعال\s*شدن\s*پدافند|پدافند.{0,25}فعال|"
    r"\b(?:air\s*strikes?|missile\s+strikes?|explosions?|blasts?|assassinat\w*|killed|declares?\s+war|"
    r"breaking|just\s+in|sirens?|intercept\w*|air\s*defen[cs]e)\b",
    re.I,
)

# Used only by the duplicate detector: two similar headlines that name DIFFERENT places are different events.
# (canonical name, regex) — Persian and English spellings share one canonical name.
PLACE_PATTERNS = [
    (name, re.compile(rx, re.I)) for name, rx in [
        ("tehran", r"تهران|tehran"), ("isfahan", r"اصفهان|isfahan|esfahan"), ("tabriz", r"تبریز|tabriz"),
        ("shiraz", r"شیراز|shiraz"), ("mashhad", r"مشهد|mashhad"), ("ahvaz", r"اهواز|ahvaz"),
        ("karaj", r"کرج|karaj"), ("qom", r"\bقم\b|\bqom\b"), ("kermanshah", r"کرمانشاه|kermanshah"),
        ("urmia", r"ارومیه|urmia"), ("rasht", r"رشت|rasht"), ("zahedan", r"زاهدان|zahedan"),
        ("bandarabbas", r"بندر\s?عباس|bandar\s?abbas"), ("bushehr", r"بوشهر|bushehr"),
        ("yazd", r"یزد|yazd"), ("kerman", r"\bکرمان\b|\bkerman\b"), ("hamadan", r"همدان|hamadan"),
        ("arak", r"اراک|\barak\b"), ("sanandaj", r"سنندج|sanandaj"), ("zanjan", r"زنجان|zanjan"),
        ("qazvin", r"قزوین|qazvin"), ("ilam", r"\bایلام\b|\bilam\b"), ("gorgan", r"گرگان|gorgan"),
        ("ardabil", r"اردبیل|ardabil"), ("natanz", r"نطنز|natanz"), ("fordow", r"فردو|fordow"),
        ("kharg", r"خارک|kharg"), ("chabahar", r"چابهار|chabahar"), ("qeshm", r"قشم|qeshm"),
        ("haifa", r"حیفا|haifa"), ("telaviv", r"تل\s?آویو|tel\s?aviv"), ("jerusalem", r"اورشلیم|بیت\s?المقدس|jerusalem"),
        ("beirut", r"بیروت|beirut"), ("damascus", r"دمشق|damascus"), ("baghdad", r"بغداد|baghdad"),
        ("sanaa", r"صنعا|sanaa"), ("gaza", r"غزه|gaza"), ("rafah", r"رفح|rafah"),
        ("hormuz", r"هرمز|hormuz"), ("dubai", r"دبی|dubai"), ("doha", r"دوحه|doha"),
    ]
]
