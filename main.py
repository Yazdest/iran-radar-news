#!/usr/bin/env python3
"""IRAN RADAR: eight Telegram sources -> concise rewrite -> channel publishing."""
from __future__ import annotations

import json
import os
import re
import sys
import time
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
}


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+|[\u0621-\u06d3\u06f0-\u06f9]+", (text or "").lower()) if len(w) > 2 and w not in STOP}


class State:
    def __init__(self, path: str):
        self.path = path
        self.fresh = not os.path.exists(path)
        self.seen: dict[str, float] = {}
        self.fails: dict[str, int] = {}
        self.titles: list[str] = []
        self.meta: dict[str, dict] = {}
        if not self.fresh:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.seen = data.get("seen", {})
                self.titles = data.get("titles", [])
                self.fails = data.get("fails", {})
                self.meta = data.get("meta", {})
            except (OSError, ValueError, AttributeError):
                self.fresh = True

    def mark(self, uid: str, item: sources.Item | None = None) -> None:
        self.seen[uid] = time.time()
        if item is not None:
            self.meta[uid] = {
                "source": item.source, "origin": item.origin, "url": item.url,
                "published": item.published.isoformat() if item.published else None,
            }

    def remember_title(self, title: str) -> None:
        clean = re.sub(r"\s+", " ", (title or "")).strip()[:700]
        if clean:
            self.titles = (self.titles + [clean])[-1200:]

    def is_duplicate(self, text: str, extra=()) -> bool:
        # Compare title plus first body sentence. This improves event-level dedup after WFWitness translation.
        a = tokens(text[:700])
        if len(a) < 4:
            return False
        norm_a = " ".join(sorted(a))
        for old in list(self.titles) + list(extra):
            b = tokens(old[:700])
            if len(b) < 4:
                continue
            common = len(a & b)
            overlap = common / len(a | b)
            ratio = SequenceMatcher(None, norm_a, " ".join(sorted(b))).ratio()
            if (overlap >= 0.60 and common >= 5) or ratio >= 0.92:
                return True
        return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        newest = dict(sorted(self.seen.items(), key=lambda pair: pair[1])[-8000:])
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            kept_meta = {key: value for key, value in self.meta.items() if key in newest}
            json.dump({"seen": newest, "titles": self.titles, "fails": self.fails, "meta": kept_meta}, fh, ensure_ascii=False)
        os.replace(tmp, self.path)


RETRY = object()


def prepare(item: sources.Item):
    try:
        return translator.make_post(item)
    except Exception as exc:
        print(f"WARN rewrite unavailable for internal source {item.source}: {exc}", file=sys.stderr)
        return RETRY


def cycle(state: State) -> bool:
    now = datetime.now(timezone.utc)
    items = sources.collect_all()
    new = [item for item in items if item.uid not in state.seen]

    if state.fresh and config.BOOTSTRAP:
        for item in items:
            state.mark(item.uid, item)
            state.remember_title(item.title)
        state.fresh = False
        state.save()
        print(f"Bootstrap: {len(items)} existing Telegram posts marked as seen.")
        return False
    state.fresh = False

    cutoff = now - timedelta(minutes=config.MAX_AGE_MINUTES)
    fresh = []
    for item in new:
        if item.published and item.published < cutoff:
            state.mark(item.uid, item)
        else:
            fresh.append(item)

    fresh.sort(key=lambda item: (1 if item.source.casefold() == "wfwitness" else 0,
                                 0 if config.BREAKING_RE.search(f"{item.title} {item.text}") else 1,
                                 -(item.published or now).timestamp()))

    batch, raw_pool = [], []
    for item in fresh:
        if state.is_duplicate(item.title, raw_pool):
            state.mark(item.uid, item)
            continue
        raw_pool.append(item.title)
        batch.append(item)
        if len(batch) >= config.MAX_PER_CYCLE:
            break

    if not batch:
        state.save()
        return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        posts = list(pool.map(prepare, batch))

    new_headlines: list[str] = []
    published_any = False
    published_count = 0
    for item, post in zip(batch, posts):
        if post is RETRY:
            # Last-resort source-text publication: model errors must never block the news queue.
            try:
                post = translator._publish_original_if_models_fail(item)
            except Exception as exc:
                state.fails[item.uid] = state.fails.get(item.uid, 0) + 1
                print(f"ERROR emergency fallback failed for {item.uid}: {exc}", file=sys.stderr)
                continue
        if post is None:
            state.mark(item.uid, item)
            state.fails.pop(item.uid, None)
            continue

        # Second duplicate gate after AI rewrite catches same event with different source wording.
        event_text = f"{post.title} {post.body}"
        if state.is_duplicate(event_text, new_headlines):
            print(f"INFO duplicate event suppressed: {post.title}")
            state.mark(item.uid, item)
            state.fails.pop(item.uid, None)
            continue

        # Keep a steady cadence when a cycle contains multiple eligible stories.
        if published_count:
            time.sleep(config.PUBLISH_INTERVAL_SECONDS)

        try:
            publisher.publish(item, post)
            published_any = True
            published_count += 1
            state.mark(item.uid, item)
            state.fails.pop(item.uid, None)
            state.remember_title(item.title)
            state.remember_title(f"{post.title} {post.body}")
            new_headlines.append(event_text)
            print(f"Published [{post.kind}] internal_source={item.source}: {post.title}")
        except Exception as exc:
            print(f"ERROR publish {item.uid}: {exc}; will retry next cycle", file=sys.stderr)
        state.save()
        time.sleep(0.5)
    state.save()
    return published_any


def main() -> None:
    if not config.DRY_RUN and not (config.BOT_TOKEN and config.CHANNEL):
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing")
    # Only Groq and OpenRouter are supported. Never reference removed Gemini/Anthropic settings.
    if not (config.GROQ_API_KEY or config.OPENROUTER_API_KEY):
        print("WARN no Groq/OpenRouter API key configured; source-text fallback will be used", file=sys.stderr)
    # Missing/failed models must never stop source-text publication.
    state = State(config.STATE_FILE)
    once = "--once" in sys.argv
    deadline = time.time() + config.RUN_MINUTES * 60 if config.RUN_MINUTES else None
    last_published_at: float | None = None
    while True:
        if deadline and time.time() >= deadline:
            break
        started = time.time()
        published = False
        try:
            published = cycle(state)
        except Exception as exc:
            print(f"ERROR cycle: {exc}", file=sys.stderr)
        if once:
            break

        # Poll frequently; each cycle may publish up to MAX_PER_CYCLE stories.
        # cycle() spaces multiple successful posts; this outer wait spaces cycles.
        if published:
            last_published_at = time.time()
        sleep_for = max(1, config.POLL_SECONDS - (time.time() - started))
        if last_published_at is not None:
            remaining = config.PUBLISH_INTERVAL_SECONDS - (time.time() - last_published_at)
            sleep_for = max(sleep_for, remaining)
        if deadline:
            runtime_left = deadline - time.time()
            if runtime_left <= 1:
                break
            sleep_for = min(sleep_for, runtime_left)
        time.sleep(max(0.1, sleep_for))


if __name__ == "__main__":
    main()
