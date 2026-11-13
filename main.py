#!/usr/bin/env python3
"""IRAN RADAR: Telegram sources -> concise rewrite -> channel publishing."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import config
import publisher
import sources
import translator

STOP = {
    "the", "and", "for", "with", "after", "over", "says", "said", "that", "this", "from", "amid",
    "است", "شد", "شود", "کرد", "کرده", "اعلام", "گفت", "گفته", "درباره", "برای", "از", "به", "در", "با",
    "این", "آن", "که", "را", "هم", "یک", "خبر", "فوری", "منتشر", "گزارش", "شده", "بود", "اند",
}

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MARKS = re.compile(r"[\u064b-\u065f\u0670\u0640\u200e\u200f]")


def norm(text: str) -> str:
    """Normalise Persian/Arabic variants so the same event written two ways compares equal."""
    t = (text or "").replace("ي", "ی").replace("ك", "ک").replace("ة", "ه").replace("ۀ", "ه")
    t = t.replace("\u200c", " ")          # half-space (ZWNJ) -> space
    t = _MARKS.sub("", t)
    return t.translate(_DIGITS).lower()   # Persian/Arabic digits -> ASCII


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+|[\u0621-\u06d3]+", norm(text)) if len(w) > 2 and w not in STOP}


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", norm(text)))


def places(text: str) -> set[str]:
    value = norm(text)
    return {name for name, rx in config.PLACE_PATTERNS if rx.search(value)}


def is_breaking(item: sources.Item) -> bool:
    return translator.is_urgent_item(item)


def sort_items(items: list, now: datetime) -> list:
    """Breaking news first (whatever the source, WFWitness included), then oldest -> newest so the
    channel timeline reads in the order things happened."""
    return sorted(items, key=lambda item: (0 if is_breaking(item) else 1, (item.published or now).timestamp()))


class State:
    def __init__(self, path: str):
        self.path = path
        self.fresh = not os.path.exists(path)
        self.seen: dict[str, float] = {}
        self.fails: dict[str, int] = {}
        self.defer: dict[str, float] = {}       # uid -> do not retry before this time
        self.chan_fail: dict[str, int] = {}     # channel -> consecutive unusable polls
        self.titles: list[tuple[float, str]] = []   # (timestamp, text) of recently published events
        self.meta: dict[str, dict] = {}
        if not self.fresh:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.seen = data.get("seen", {})
                self.fails = data.get("fails", {})
                self.defer = data.get("defer", {})
                self.chan_fail = data.get("chan_fail", {})
                self.meta = data.get("meta", {})
                now = time.time()
                for entry in data.get("titles", []):
                    if isinstance(entry, str):          # old state format: no timestamp
                        self.titles.append((now, entry))
                    elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                        self.titles.append((float(entry[0]), str(entry[1])))
            except (OSError, ValueError, AttributeError, TypeError):
                self.fresh = True

    def mark(self, uid: str, item: sources.Item | None = None) -> None:
        self.seen[uid] = time.time()
        self.fails.pop(uid, None)
        self.defer.pop(uid, None)
        if item is not None:
            self.meta[uid] = {
                "source": item.source, "origin": item.origin, "url": item.url,
                "published": item.published.isoformat() if item.published else None,
            }

    def remember_title(self, title: str) -> None:
        clean = re.sub(r"\s+", " ", (title or "")).strip()[:700]
        if clean:
            self.titles = (self.titles + [(time.time(), clean)])[-1200:]

    def is_duplicate(self, text: str, extra=()) -> bool:
        """Same event = similar wording inside the dedup window AND no conflicting place/number.
        (Old behaviour compared with the last 1200 titles of any age and ignored places, so
        'air defence active in Isfahan' was suppressed by an old 'air defence active in Tehran'.)"""
        a = tokens(text[:700])
        if len(a) < 4:
            return False
        cutoff = time.time() - config.DEDUP_WINDOW_HOURS * 3600
        candidates = [old for ts, old in self.titles if ts >= cutoff] + list(extra)
        norm_a = " ".join(sorted(a))
        nums_a = places_a = None
        for old in candidates:
            b = tokens(old[:700])
            if len(b) < 4:
                continue
            common = len(a & b)
            if common < 3:
                continue
            overlap = common / len(a | b)
            similar = (overlap >= 0.60 and common >= 5)
            if not similar and overlap >= 0.80:
                similar = SequenceMatcher(None, norm_a, " ".join(sorted(b))).ratio() >= 0.92
            if not similar:
                continue
            if nums_a is None:
                nums_a, places_a = numbers(text), places(text)
            nums_b, places_b = numbers(old), places(old)
            if places_a and places_b and not (places_a & places_b):
                continue    # same wording, different place -> different event
            if nums_a and nums_b and not (nums_a & nums_b):
                continue    # different figures -> an update / different event
            return True
        return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        now = time.time()
        newest = dict(sorted(self.seen.items(), key=lambda pair: pair[1])[-8000:])
        titles = [[ts, text] for ts, text in self.titles if now - ts < 24 * 3600][-1200:]
        defer = {k: v for k, v in self.defer.items() if v > now}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            kept_meta = {key: value for key, value in self.meta.items() if key in newest}
            json.dump({"seen": newest, "titles": titles, "fails": self.fails, "defer": defer,
                       "chan_fail": self.chan_fail, "meta": kept_meta}, fh, ensure_ascii=False)
        os.replace(tmp, self.path)


RETRY = object()


def prepare(item: sources.Item):
    try:
        return translator.make_post(item)
    except Exception as exc:
        print(f"WARN rewrite unavailable for {item.source}: {exc}", file=sys.stderr)
        return RETRY


def _register_failure(state: State, item: sources.Item, reason: str) -> None:
    n = state.fails.get(item.uid, 0) + 1
    state.fails[item.uid] = n
    state.defer[item.uid] = time.time() + min(300, 30 * n)   # back off: 30s, 60s, 90s ...
    print(f"WARN {reason} for {item.uid} (attempt {n}/{config.MAX_FAILS})", file=sys.stderr)


def check_channels(state: State) -> None:
    """Count consecutive polls in which a source returned nothing usable and alert the owner once."""
    for src in config.TELEGRAM_SOURCES:
        channel = src["channel"]
        if sources.STATUS.get(channel, "ok") == "ok":
            state.chan_fail[channel] = 0
            continue
        n = state.chan_fail.get(channel, 0) + 1
        state.chan_fail[channel] = n
        if n == config.CHANNEL_ALERT_AFTER:
            msg = f"⚠️ IRAN RADAR: source «{channel}» returned nothing usable for {n} polls in a row ({sources.STATUS.get(channel)})."
            print(msg, file=sys.stderr)
            publisher.notify_admin(msg)


def cycle(state: State, deadline: float | None = None) -> bool:
    now = datetime.now(timezone.utc)
    stats: Counter = Counter()
    items = sources.collect_all()
    check_channels(state)
    stats["collected"] = len(items)
    new = [item for item in items if item.uid not in state.seen]
    stats["new"] = len(new)

    if state.fresh and config.BOOTSTRAP:
        for item in items:
            state.mark(item.uid, item)
            state.remember_title(item.title)
        state.fresh = False
        state.save()
        print(f"Bootstrap: {len(items)} existing Telegram posts marked as seen.")
        return False

    # Missing state (e.g. the Actions cache was evicted) must not re-publish six hours of old news.
    max_age = config.MAX_AGE_MINUTES
    if state.fresh:
        max_age = min(max_age, config.FRESH_START_MAX_AGE_MINUTES)
        print(f"INFO no saved state: only stories from the last {max_age} minutes are eligible")
    state.fresh = False

    cutoff = now - timedelta(minutes=max_age)
    ready = []
    ts_now = time.time()
    for item in new:
        if item.published and item.published < cutoff:
            state.mark(item.uid, item)
            stats["stale"] += 1
        elif state.fails.get(item.uid, 0) >= config.MAX_FAILS:
            print(f"ERROR giving up on {item.uid} after {config.MAX_FAILS} failed attempts: {item.title!r}", file=sys.stderr)
            publisher.notify_admin(f"⚠️ IRAN RADAR: story dropped after {config.MAX_FAILS} failures:\n{item.title[:200]}\n{item.url}")
            state.mark(item.uid, item)
            stats["gave_up"] += 1
        elif state.defer.get(item.uid, 0) > ts_now:
            stats["deferred"] += 1
        else:
            ready.append(item)

    batch, raw_pool = [], []
    for item in sort_items(ready, now):
        if len(batch) >= config.MAX_PER_CYCLE:
            break   # the rest stays unseen and is picked up by the next cycle
        if state.is_duplicate(item.title, raw_pool):
            state.mark(item.uid, item)
            stats["dup_before_ai"] += 1
            continue
        raw_pool.append(item.title)
        batch.append(item)

    if not batch:
        print("CYCLE " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        state.save()
        return False

    with ThreadPoolExecutor(max_workers=config.AI_WORKERS) as pool:
        posts = list(pool.map(prepare, batch))

    new_headlines: list[str] = []
    published_count = 0
    for item, post in zip(batch, posts):
        if deadline and time.time() >= deadline:
            print("INFO run time is over; remaining stories are left for the next run")
            break
        if post is RETRY:
            _register_failure(state, item, "rewrite failed")
            stats["ai_retry"] += 1
            continue
        if post is None:
            # Explicit editorial skip (not a provider failure). Logged so it never looks like a silent failure.
            print(f"INFO NOT_PUBLISHED: editorial skip; source={item.source}; title={item.title!r}", file=sys.stderr)
            state.mark(item.uid, item)
            stats["skipped"] += 1
            continue

        # Second duplicate gate after the rewrite catches the same event told in other words / languages.
        event_text = f"{post.title} {post.body}"
        if state.is_duplicate(event_text, new_headlines):
            print(f"INFO duplicate event suppressed: {post.title}")
            state.mark(item.uid, item)
            stats["dup_after_ai"] += 1
            continue

        if published_count:
            time.sleep(config.PUBLISH_INTERVAL_SECONDS)   # short spacing, posts go out back to back

        try:
            publisher.publish(item, post)
        except Exception as exc:
            _register_failure(state, item, f"publish error: {exc}")
            stats["publish_error"] += 1
        else:
            published_count += 1
            stats["published"] += 1
            state.mark(item.uid, item)
            state.remember_title(item.title)
            state.remember_title(event_text)
            new_headlines.append(event_text)
            print(f"Published [{post.kind}] internal_source={item.source}: {post.title}")
        state.save()

    print("CYCLE " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    state.save()
    return published_count > 0


def main() -> None:
    if not config.DRY_RUN and not (config.BOT_TOKEN and config.CHANNEL):
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing")
    if not (config.GROQ_API_KEY or config.OPENROUTER_API_KEY):
        print("WARN no Groq/OpenRouter API key configured; source text / machine translation will be used", file=sys.stderr)
    state = State(config.STATE_FILE)
    once = "--once" in sys.argv
    deadline = time.time() + config.RUN_MINUTES * 60 if config.RUN_MINUTES else None
    while True:
        if deadline and time.time() >= deadline:
            break
        started = time.time()
        try:
            cycle(state, deadline)
        except Exception as exc:
            print(f"ERROR cycle: {exc}", file=sys.stderr)
        if once:
            break
        # Poll at a fixed rhythm. (The old code also waited PUBLISH_INTERVAL_SECONDS=60 after every
        # publication, which delayed the next poll by a full minute.)
        sleep_for = max(1.0, config.POLL_SECONDS - (time.time() - started))
        if deadline:
            runtime_left = deadline - time.time()
            if runtime_left <= 1:
                break
            sleep_for = min(sleep_for, runtime_left)
        time.sleep(max(0.1, sleep_for))
    try:
        state.save()
    except Exception as exc:
        print(f"WARN final state save failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
