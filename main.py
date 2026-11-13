#!/usr/bin/env python3
"""Radar news channel: collect -> translate -> publish, as fast as possible.

  python main.py            run forever (VPS / server)         -> POLL_SECONDS between checks
  python main.py --once     single pass
  RUN_MINUTES=17 python main.py   loop for 17 minutes (GitHub Actions)
  DRY_RUN=1 python main.py --once  print posts, send nothing
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import config
import publisher
import sources
import translator

STOP = {"the", "and", "for", "with", "after", "over", "says", "said", "that", "this", "from", "amid", "has", "have"}


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9\u0600-\u06ff]+", text.lower()) if len(w) > 2 and w not in STOP}


class State:
    def __init__(self, path: str):
        self.path = path
        self.fresh = not os.path.exists(path)
        self.seen: dict[str, float] = {}
        self.titles: list[str] = []
        if not self.fresh:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.seen = data.get("seen", {})
                self.titles = data.get("titles", [])
            except (OSError, ValueError):
                self.fresh = True

    def mark(self, uid: str) -> None:
        self.seen[uid] = time.time()

    def remember_title(self, title: str) -> None:
        self.titles = (self.titles + [title])[-300:]

    def is_duplicate(self, title: str) -> bool:
        a = tokens(title)
        for old in self.titles:
            b = tokens(old)
            if a and b and len(a & b) / len(a | b) >= 0.6:
                return True
        return False

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        newest = dict(sorted(self.seen.items(), key=lambda kv: kv[1])[-6000:])
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"seen": newest, "titles": self.titles}, fh, ensure_ascii=False)
        os.replace(tmp, self.path)


def prepare(item: sources.Item):
    """Translation and reference image run in parallel for every selected item."""
    try:
        post = translator.make_post(item)
    except Exception as exc:
        print(f"ERROR translate {item.uid}: {exc}", file=sys.stderr)
        return None
    if post is not None and not item.media and not item.skip_og:
        image = sources.fetch_og_image(item.url)
        if image:
            item.media = [sources.Media("photo", image)]
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
    fresh.sort(key=lambda i: i.published or now)

    batch = []
    for i in fresh:
        if state.is_duplicate(i.title):
            state.mark(i.uid)
            continue
        state.remember_title(i.title)
        batch.append(i)
        if len(batch) >= config.MAX_PER_CYCLE:
            break
    if not batch:
        state.save()
        return

    with ThreadPoolExecutor(max_workers=4) as ex:
        posts = list(ex.map(prepare, batch))

    for item, post in zip(batch, posts):
        if post is None:
            state.mark(item.uid)
            continue
        try:
            publisher.publish(item, post)
            print(f"Published [{post.kind}] {item.source}: {post.title}")
        except Exception as exc:  # timeouts are ambiguous: never resend, to avoid duplicates
            print(f"ERROR publish {item.uid}: {exc}", file=sys.stderr)
        state.mark(item.uid)
        state.save()
        time.sleep(1.5)
    state.save()


def main() -> None:
    if not config.DRY_RUN and not (config.BOT_TOKEN and config.CHANNEL):
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing")
    if not config.ANTHROPIC_API_KEY:
        print("NOTE: ANTHROPIC_API_KEY not set -> basic Google translation (lower quality).", file=sys.stderr)
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
