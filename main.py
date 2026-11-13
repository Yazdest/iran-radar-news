#!/usr/bin/env python3
"""Radar news channel: collect -> translate -> publish, as fast as possible.

  python main.py            run forever (VPS / server)         -> POLL_SECONDS between checks
  python main.py --once     single pass
  RUN_MINUTES=4 python main.py   loop for 4 minutes (GitHub Actions)
  DRY_RUN=1 python main.py --once  print posts, send nothing
"""
from __future__ import annotations

import json
import os
import re
import sys
from difflib import SequenceMatcher
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import cards
import config
import publisher
import sources
import translator
import tweets

STOP = {"the", "and", "for", "with", "after", "over", "says", "said", "that", "this", "from", "amid", "has", "have"}


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9\u0600-\u06ff]+", text.lower()) if len(w) > 2 and w not in STOP}


class State:
    def __init__(self, path: str):
        self.path = path
        self.fresh = not os.path.exists(path)
        self.seen: dict[str, float] = {}
        self.fails: dict[str, int] = {}
        self.titles: list[str] = []
        if not self.fresh:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.seen = data.get("seen", {})
                self.titles = data.get("titles", [])
                self.fails = data.get("fails", {})
            except (OSError, ValueError):
                self.fresh = True

    def mark(self, uid: str) -> None:
        self.seen[uid] = time.time()

    def remember_title(self, title: str) -> None:
        self.titles = (self.titles + [title[:700]])[-500:]

    def is_duplicate(self, text: str, extra=()) -> bool:
        a = tokens(text)
        for old in list(self.titles) + list(extra):
            b = tokens(old)
            if not a or not b:
                continue
            overlap = len(a & b) / len(a | b)
            # A high title/text similarity catches syndicated copies with slightly different wording.
            ratio = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
            if overlap >= 0.48 or ratio >= 0.82:
                return True
        return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        newest = dict(sorted(self.seen.items(), key=lambda kv: kv[1])[-6000:])
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"seen": newest, "titles": self.titles, "fails": self.fails}, fh, ensure_ascii=False)
        os.replace(tmp, self.path)


RETRY = object()   # translation not available right now: try again next cycle


def prepare(item: sources.Item):
    """Runs in parallel for every selected item: relevance + translation, then the picture."""
    try:
        post = translator.make_post(item)
    except Exception as exc:
        print(f"WARN translation unavailable for {item.uid}: {exc}", file=sys.stderr)
        return RETRY
    if post is None:
        return None
    if item.origin == "tweet":
        tweets.enrich(item)
        # Build the poster from the tweet's own media first; a speaker portrait
        # is only a fallback when no topic-relevant image exists.
        card = cards.news_card(post, item)
        if card:
            item.media = [sources.Media("photo", "", card)] + item.media[:3]
    else:
        if not item.media and not item.skip_og:
            image = sources.fetch_og_image(item.url)
            if image:
                item.media = [sources.Media("photo", image)]
        # The square editorial card embeds the original article image (or a
        # topic-matched Commons image) whenever one can be found.
        card = cards.news_card(post, item)
        if card:
            item.media = [sources.Media("photo", "", card)] + item.media[:2]
    return post


def cycle(state: State) -> None:
    now = datetime.now(timezone.utc)
    items = sources.collect_all()
    new = [i for i in items if i.uid not in state.seen]

    if state.fresh and config.BOOTSTRAP:
        for i in items:
            state.mark(i.uid)
            state.remember_title(i.title)
        state.fresh = False
        state.save()
        print(f"Bootstrap: {len(items)} existing items marked as seen. New items will be published from now on.")
        return
    state.fresh = False

    cutoff = now - timedelta(minutes=config.MAX_AGE_MINUTES)
    fresh = []
    for i in new:
        if i.published is not None and i.published < cutoff:
            state.mark(i.uid)
        else:
            fresh.append(i)
    def priority(item):
        urgent = bool(config.BREAKING_RE.search(f"{item.title} {item.text}"))
        stamp = (item.published or now).timestamp()
        return (0 if urgent else 1, -stamp)  # breaking first, then newest first
    fresh.sort(key=priority)

    batch, pool = [], []
    for i in fresh:
        candidate_text = f"{i.title} {i.text}"
        if state.is_duplicate(candidate_text, pool):
            state.mark(i.uid)
            continue
        pool.append(candidate_text)
        batch.append(i)
        if len(batch) >= config.MAX_PER_CYCLE:
            break
    if not batch:
        state.save()
        return

    with ThreadPoolExecutor(max_workers=4) as ex:
        posts = list(ex.map(prepare, batch))

    for item, post in zip(batch, posts):
        if post is RETRY:                      # never publish a bad translation: retry, give up after 3 tries
            state.fails[item.uid] = state.fails.get(item.uid, 0) + 1
            if state.fails[item.uid] >= 3:
                print(f"Giving up on {item.uid}", file=sys.stderr)
                state.mark(item.uid)
                state.fails.pop(item.uid, None)
            continue
        if post is None:                       # not relevant / not publishable
            state.mark(item.uid)
            state.save()
            continue
        try:
            publisher.publish(item, post)
            # Mark only after Telegram confirms success. Failed sends remain eligible for retry.
            state.mark(item.uid)
            state.fails.pop(item.uid, None)
            state.remember_title(f"{item.title} {item.text}")
            state.remember_title(f"{post.title} {post.body}")
            print(f"Published [{post.kind}] {item.source}: {post.title}")
        except Exception as exc:
            print(f"ERROR publish {item.uid}: {exc}; will retry next cycle", file=sys.stderr)
        state.save()
        time.sleep(1.0)
    state.save()


def main() -> None:
    if not config.DRY_RUN and not (config.BOT_TOKEN and config.CHANNEL):
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing")
    if not cards.AVAILABLE:
        print("NOTE: no usable font / shaping library -> posts without a photo will have no picture.", file=sys.stderr)
    if not (config.ANTHROPIC_API_KEY or config.GEMINI_API_KEY) and not config.ALLOW_FALLBACK:
        raise SystemExit("No GEMINI_API_KEY / ANTHROPIC_API_KEY: refusing to publish half-translated news. "
                         "Add a key (or set ALLOW_FALLBACK=1 to accept poor Google translation).")
    state = State(config.STATE_FILE)
    once = "--once" in sys.argv
    deadline = time.time() + config.RUN_MINUTES * 60 if config.RUN_MINUTES else None
    while True:
        started = time.time()
        try:
            cycle(state)
        except Exception as exc:
            print(f"ERROR cycle: {exc}", file=sys.stderr)
        if once or (deadline and time.time() + config.POLL_SECONDS > deadline):
            break
        time.sleep(max(1, config.POLL_SECONDS - (time.time() - started)))


if __name__ == "__main__":
    main()
