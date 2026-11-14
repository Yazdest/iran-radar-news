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
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import config
import publisher
import sources
import translator
from textutil import actions, norm, numbers, places, tokens  # noqa: F401  (re-exported for callers/tests)


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
        self.pending: dict[str, float] = {}     # uid -> time a send started (still here = outcome unknown)
        self.events: list[dict] = []            # recent published events (for «confirmed by a second source»)
        self.skips: list[dict] = []             # last stories NOT published and why (review the AI's decisions)
        self.run_skips: list[dict] = []         # same, this run only (job summary)
        self._sigs: dict[str, tuple[frozenset, frozenset]] = {}
        if not self.fresh:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self.seen = data.get("seen", {})
                self.fails = data.get("fails", {})
                self.defer = data.get("defer", {})
                self.chan_fail = data.get("chan_fail", {})
                self.pending = data.get("pending", {})
                self.events = data.get("events", [])
                self.skips = data.get("skips", [])
                now = time.time()
                for entry in data.get("titles", []):
                    if isinstance(entry, str):          # old state format: no timestamp
                        self.titles.append((now, entry))
                    elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                        self.titles.append((float(entry[0]), str(entry[1])))
            except (OSError, ValueError, AttributeError, TypeError):
                self.fresh = True

    def mark(self, uid: str, item: sources.Item | None = None) -> None:
        # (The old per-uid "meta" dict was written on every story and never read: dropped.)
        self.seen[uid] = time.time()
        self.fails.pop(uid, None)
        self.defer.pop(uid, None)
        self.pending.pop(uid, None)

    def remember_title(self, title: str) -> None:
        clean = re.sub(r"\s+", " ", (title or "")).strip()[:700]
        if clean:
            self.titles = (self.titles + [(time.time(), clean)])[-1200:]

    def log_skip(self, item: sources.Item, reason: str) -> None:
        entry = {"ts": int(time.time()), "reason": reason, "source": item.source, "title": item.title[:200], "url": item.url}
        self.skips = (self.skips + [entry])[-300:]
        self.run_skips.append(entry)

    # ---------------------------------------------------------------- duplicates
    def _signature(self, text: str) -> tuple[frozenset, frozenset]:
        sig = self._sigs.get(text)
        if sig is None:
            sig = (frozenset(places(text)), frozenset(actions(text)))
            if len(self._sigs) > 3000:
                self._sigs.clear()
            self._sigs[text] = sig
        return sig

    def find_duplicate(self, text: str, extra=()) -> str | None:
        """Return the earlier text this one repeats, or None.
        Rule 1 (wording): similar wording inside the dedup window AND no conflicting place/number.
        Rule 2 (event): the same city AND the same kind of event (explosion, air defence, strike...) within
        EVENT_DEDUP_MINUTES. Rule 2 works across paraphrases and across Persian/English, where wording
        overlap is low because the AI phrases every rewrite differently."""
        a = tokens(text[:700])
        now = time.time()
        cutoff = now - config.DEDUP_WINDOW_HOURS * 3600
        recent = [(ts, old) for ts, old in self.titles if ts >= cutoff] + [(now, old) for old in extra]

        if len(a) >= 4:
            norm_a = " ".join(sorted(a))
            nums_a = places_a = None
            for _, old in recent:
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
                return old

        if config.EVENT_DEDUP_MINUTES > 0:
            places_a, actions_a = self._signature(text)
            if places_a and actions_a:
                event_cutoff = now - config.EVENT_DEDUP_MINUTES * 60
                nums_a = numbers(text)
                for ts, old in reversed(recent):
                    if ts < event_cutoff:
                        continue
                    places_b, actions_b = self._signature(old)
                    if not (places_a & places_b and actions_a & actions_b):
                        continue
                    nums_b = numbers(old)
                    if nums_a and nums_b and not (nums_a & nums_b):
                        continue
                    return old
        return None

    def is_duplicate(self, text: str, extra=()) -> bool:
        return self.find_duplicate(text, extra) is not None

    # ---------------------------------------------------------------- events / confirmation
    def add_event(self, item: sources.Item, post: translator.Post, result: dict, event_text: str, sources_: set[str]) -> None:
        result = result or {}
        self.events.append({
            "ts": time.time(), "texts": [re.sub(r"\s+", " ", item.title).strip()[:700], re.sub(r"\s+", " ", event_text).strip()[:700]],
            "sources": sorted(sources_ | {item.source}), "mid": result.get("message_id"), "kind": result.get("kind", "text"),
            "post": publisher.post_to_dict(post), "src": item.source,
            # only live military claims carry the «unconfirmed» label; only those can be «confirmed» later
            "label": bool(config.SINGLE_SOURCE_LABEL and translator.needs_confirmation(post)),
            "confirmed": len(sources_ | {item.source}) >= 2,
        })
        self.events = self.events[-300:]

    def confirm(self, matched_text: str, source: str) -> dict | None:
        """Record that `source` also reported the event whose text is `matched_text`. Returns the event when it
        has just become confirmed (second distinct source) so the caller can edit the published message."""
        clean = re.sub(r"\s+", " ", matched_text).strip()[:700]
        for event in reversed(self.events):
            if clean in event["texts"]:
                if source not in event["sources"]:
                    event["sources"].append(source)
                if event["label"] and not event["confirmed"] and len(event["sources"]) >= 2:
                    event["confirmed"] = True
                    return event
                return None
        return None

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        now = time.time()
        newest = dict(sorted(self.seen.items(), key=lambda pair: pair[1])[-8000:])
        titles = [[ts, text] for ts, text in self.titles if now - ts < 24 * 3600][-1200:]
        defer = {k: v for k, v in self.defer.items() if v > now}
        events = [e for e in self.events if now - e["ts"] < 6 * 3600][-300:]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"seen": newest, "titles": titles, "fails": self.fails, "defer": defer,
                       "chan_fail": self.chan_fail, "pending": self.pending, "events": events,
                       "skips": self.skips[-300:]}, fh, ensure_ascii=False)
        os.replace(tmp, self.path)


RETRY = object()


def prepare(item: sources.Item):
    try:
        return translator.make_post(item)
    except Exception as exc:
        print(f"WARN rewrite unavailable for {item.source}: {config.redact(exc)}", file=sys.stderr)
        return RETRY


def _register_failure(state: State, item: sources.Item, reason: str) -> None:
    n = state.fails.get(item.uid, 0) + 1
    state.fails[item.uid] = n
    state.defer[item.uid] = time.time() + min(300, 30 * n)   # back off: 30s, 60s, 90s ...
    print(f"WARN {config.redact(reason)} for {item.uid} (attempt {n}/{config.MAX_FAILS})", file=sys.stderr)


def check_channels(state: State) -> None:
    """Count consecutive polls in which a source returned nothing usable and alert the owner once."""
    for src in config.TELEGRAM_SOURCES:
        channel = src["channel"]
        if sources.STATUS.get(channel, "ok") in ("ok", "backoff"):   # backoff = we paused it ourselves (HTTP 429)
            if sources.STATUS.get(channel, "ok") == "ok":
                state.chan_fail[channel] = 0
            continue
        n = state.chan_fail.get(channel, 0) + 1
        state.chan_fail[channel] = n
        if n == config.CHANNEL_ALERT_AFTER:
            msg = f"⚠️ IRAN RADAR: source «{channel}» returned nothing usable for {n} polls in a row ({sources.STATUS.get(channel)})."
            print(msg, file=sys.stderr)
            publisher.notify_admin(msg)


def resolve_pending(state: State, stats: Counter) -> None:
    """A send that started but never finished (process killed, timeout) may or may not be on the channel."""
    for uid in list(state.pending):
        if config.AMBIGUOUS_SEND_POLICY == "retry":
            state.pending.pop(uid, None)         # story is still unseen -> will be sent again (possible duplicate)
            stats["pending_retry"] += 1
        else:
            print(f"WARN send of {uid} was interrupted; treated as published to avoid a duplicate", file=sys.stderr)
            publisher.notify_admin(f"⚠️ IRAN RADAR: a send was interrupted ({uid}); not repeated to avoid a duplicate.")
            state.mark(uid)
            stats["pending_skipped"] += 1


def _confirm(state: State, matched: str, source: str, stats: Counter) -> None:
    event = state.confirm(matched, source)
    if event:
        publisher.update_confirmation(event)
        stats["confirmed"] += 1


def cycle(state: State, deadline: float | None = None) -> bool:
    now = datetime.now(timezone.utc)
    stats: Counter = Counter()
    translator.GLOBAL_DEADLINE = deadline
    resolve_pending(state, stats)
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
            state.log_skip(item, "gave_up")
            state.mark(item.uid, item)
            stats["gave_up"] += 1
        elif state.defer.get(item.uid, 0) > ts_now:
            stats["deferred"] += 1
        else:
            ready.append(item)

    batch, raw_pool = [], []
    pool_sources: dict[str, set[str]] = {}      # raw title of a batch story -> channels that reported it
    for item in sort_items(ready, now):
        if len(batch) >= config.MAX_PER_CYCLE:
            break   # the rest stays unseen and is picked up by the next cycle
        dup = state.find_duplicate(item.title, raw_pool)
        if dup is not None:
            state.mark(item.uid, item)
            stats["dup_before_ai"] += 1
            if dup in pool_sources:
                pool_sources[dup].add(item.source)        # same cycle, not published yet
            else:
                _confirm(state, dup, item.source, stats)  # already on the channel
            continue
        raw_pool.append(item.title)
        pool_sources[item.title] = {item.source}
        batch.append(item)

    if not batch:
        print("CYCLE " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())))
        state.save()
        return False

    # Rewrites run in parallel but are consumed IN ORDER (breaking first). The old code waited for the whole
    # batch (list(pool.map(...))), so a breaking story could sit behind the slowest rewrite of 11 other stories,
    # and nothing enforced the run deadline while waiting.
    pool = ThreadPoolExecutor(max_workers=config.AI_WORKERS)
    futures = [pool.submit(prepare, item) for item in batch]
    published_count = 0
    try:
        for item, future in zip(batch, futures):
            remaining = None if not deadline else deadline - time.time()
            if remaining is not None and remaining <= 0:
                print("INFO run time is over; remaining stories are left for the next run")
                break
            try:
                post = future.result(timeout=remaining)
            except FutureTimeout:
                print("INFO run time is over while waiting for a rewrite; remaining stories are left for the next run")
                break

            if post is RETRY:
                _register_failure(state, item, "rewrite failed")
                stats["ai_retry"] += 1
                continue
            if post is None:
                # Explicit editorial skip (not a provider failure). Logged so it never looks like a silent failure.
                reason = "too_short" if translator.too_short_to_publish(item) else "ai_skip"
                print(f"INFO NOT_PUBLISHED: {reason}; source={item.source}; title={item.title!r}", file=sys.stderr)
                state.log_skip(item, reason)
                state.mark(item.uid, item)
                stats["skipped"] += 1
                continue

            # Second duplicate gate after the rewrite catches the same event told in other words / languages.
            event_text = f"{post.title} {post.body}"
            dup = state.find_duplicate(event_text)
            if dup is not None:
                print(f"INFO duplicate event suppressed: {post.title}")
                state.mark(item.uid, item)
                stats["dup_after_ai"] += 1
                _confirm(state, dup, item.source, stats)
                continue

            if published_count:
                time.sleep(config.PUBLISH_INTERVAL_SECONDS)   # short spacing, posts go out back to back

            reporters = pool_sources.get(item.title, {item.source}) | {item.source}
            label = None
            if config.SINGLE_SOURCE_LABEL and translator.needs_confirmation(post):
                label = "confirmed" if len(reporters) >= 2 else "unconfirmed"
            silent = bool(config.SILENT_NON_URGENT and post.kind != "breaking" and post.impact != "high")

            state.pending[item.uid] = time.time()    # «send started»: survives a crash between send and mark
            state.save()
            try:
                result = publisher.publish(item, post, status=label, silent=silent)
            except publisher.AmbiguousSend as exc:
                state.pending.pop(item.uid, None)
                if config.AMBIGUOUS_SEND_POLICY == "retry":
                    _register_failure(state, item, f"publish outcome unknown ({exc}); will retry")
                else:
                    print(f"WARN publish outcome unknown ({exc}); NOT retrying to avoid a duplicate: {item.title!r}", file=sys.stderr)
                    publisher.notify_admin(f"⚠️ IRAN RADAR: send outcome unknown ({exc}); not repeated:\n{item.title[:200]}")
                    state.log_skip(item, "send_unknown")
                    state.mark(item.uid, item)
                stats["send_unknown"] += 1
            except Exception as exc:
                state.pending.pop(item.uid, None)
                _register_failure(state, item, f"publish error: {exc}")
                stats["publish_error"] += 1
            else:
                published_count += 1
                stats["published"] += 1
                state.mark(item.uid, item)
                state.remember_title(item.title)
                state.remember_title(event_text)
                state.add_event(item, post, result, event_text, reporters)
                print(f"Published [{post.kind}{'/' + label if label else ''}{'/silent' if silent else ''}] "
                      f"internal_source={item.source}: {post.title}")
            state.save()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    print("CYCLE " + " ".join(f"{k}={v}" for k, v in sorted(stats.items())))
    state.save()
    return published_count > 0


def write_job_summary(state: State) -> None:
    """On GitHub Actions: list this run's unpublished stories in the job summary so the AI's decisions can be reviewed."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path or not state.run_skips:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("### Stories not published in this run\n\n| reason | source | title |\n|---|---|---|\n")
            for entry in state.run_skips[-100:]:
                title = entry["title"].replace("|", "\\|").replace("\n", " ")
                fh.write(f"| {entry['reason']} | {entry['source']} | {title} |\n")
    except OSError:
        pass


def main() -> None:
    if "--show-skips" in sys.argv:
        for entry in State(config.STATE_FILE).skips[-50:]:
            when = datetime.fromtimestamp(entry["ts"], timezone.utc).strftime("%m-%d %H:%M")
            print(f"{when}  {entry['reason']:<12} {entry['source']:<20} {entry['title']}")
        return
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
            print(f"ERROR cycle: {config.redact(exc)}", file=sys.stderr)
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
    write_job_summary(state)


if __name__ == "__main__":
    main()
