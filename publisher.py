"""Publish short Persian text posts and eligible source videos to Telegram."""
from __future__ import annotations

import dataclasses
import html
import json
import re
import time
from urllib.parse import urlencode

import requests

import config
from sources import UA, Item, Media
import translator
from translator import Post

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
VIDEO_MAX = 49_000_000
PHOTO_MAX = 9_500_000


class TelegramRejected(Exception):
    pass


class AmbiguousSend(TelegramRejected):
    """The request may or may not have reached Telegram (read timeout, connection dropped mid-answer).
    Retrying blindly can post the same story twice, so main.py decides by AMBIGUOUS_SEND_POLICY."""


def _topic_tag(post: Post) -> str:
    text = f"{post.title} {post.body}"
    # Same rule as translator._finish (breaking, military activity, US/Israeli statements about Iran).
    if post.kind == "breaking" or translator.is_urgent(text):
        return "#خبر_فوری"
    # config.fa(): «ارزیابی» is not «ارز», «سریال» is not «ریال».
    if re.search(config.fa(r"دلار|ارز|تورم|نفت|بورس|اقتصاد|بودجه|ریال|تومان"), text):
        return "#اقتصاد"
    if re.search(config.fa(r"مذاکر(?:ه|ات)|توافق|آتش.?بس|دیپلماسی|تحریم"), text):
        return "#مذاکرات"
    return "#اخبار_روز"


def build_html(post: Post, item: Item, body_limit: int | None = None) -> str:
    e = html.escape
    tag = _topic_tag(post)
    head = f"🚨 <b>{tag}</b>" if tag == "#خبر_فوری" else f"📰 <b>{tag}</b>"
    parts = [f"{head}\n\n<b>{e(post.title)}</b>" if post.title else head]
    if post.translation_unavailable:
        parts.append("⚠️ ترجمه در دسترس نیست؛ متن اصلی منتشر شده است.")

    clean_body = translator._clean_source_formatting(post.body)
    if body_limit is not None and len(clean_body) > body_limit:
        clean_body = (clean_body[:body_limit].rstrip() + "…") if body_limit > 0 else ""   # plain text, never half a tag
    # Only the channels in config.CREDITED_SOURCES (wfwitness, Entekhab, Eskan News) are named, inline at the
    # end of the news paragraph. Every other Telegram channel stays anonymous.
    credit = config.CREDITED_SOURCES.get(config.source_key(item.source))
    if credit:
        tail = f"منبع: {credit}"
        if not clean_body:
            clean_body = tail
        elif not clean_body.rstrip().endswith(credit):
            clean_body = f"{clean_body} | {tail}"
    if clean_body:
        parts.append(e(clean_body))

    # Keep two empty lines between the final news/source line and the invitation.
    footer = (
        "برای اطلاع از آخرین اخبار و تحولات، ما را دنبال کنید.\n"
        f"📡 <b>رادار ایران</b> | {e(config.SIGNATURE or '@RadarIranNews')}"
    )
    news = "\n\n".join(parts)
    return f"{news}\n\n\n{footer}"


def visible_len(markup: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", markup)))


def _call(method: str, data: dict | None = None, files: dict | None = None):
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    timeout = (10, 120) if files else (10, 30)
    for _ in range(3):
        try:
            response = requests.post(url, data=data, files=files, timeout=timeout)
        except requests.exceptions.ConnectTimeout:
            raise TelegramRejected("connect timeout") from None            # never reached Telegram: safe to retry
        except requests.exceptions.ReadTimeout:
            raise AmbiguousSend("read timeout") from None                  # may have been delivered
        except requests.exceptions.RequestException as exc:
            # requests puts the whole URL (= the bot token) into its messages: never let that escape.
            text = config.redact(exc)
            if "Connection aborted" in text or "RemoteDisconnected" in text or "Read timed out" in text:
                raise AmbiguousSend("connection dropped: " + type(exc).__name__) from None
            raise TelegramRejected("network error: " + type(exc).__name__) from None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if payload.get("ok"):
            return payload["result"]
        if response.status_code == 429:
            time.sleep(min(int(payload.get("parameters", {}).get("retry_after", 5)), 30))
            continue
        raise TelegramRejected(payload.get("description", f"HTTP {response.status_code}"))
    raise TelegramRejected("rate limited")


def notify_admin(text: str) -> None:
    """Best-effort private alert to the owner (ADMIN_CHAT_ID). Never raises."""
    if not (config.ADMIN_CHAT_ID and config.BOT_TOKEN) or config.DRY_RUN:
        return
    try:
        _call("sendMessage", {"chat_id": config.ADMIN_CHAT_ID, "text": text[:3900]})
    except Exception as exc:
        print(f"WARN could not notify admin: {config.redact(exc)}")


def _download_video(media: Media) -> tuple[str, bytes] | None:
    if media.kind != "video" or not media.url:
        return None
    try:
        with requests.get(media.url, headers=UA, timeout=45, stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "video" not in content_type and not media.url.lower().split("?")[0].endswith((".mp4", ".mov", ".m4v")):
                print(f"WARN video URL did not return a video content type: {content_type}")
                return None
            data = bytearray()
            for chunk in response.iter_content(65536):
                if chunk:
                    data.extend(chunk)
                    if len(data) > VIDEO_MAX:
                        print("WARN source video exceeds Telegram bot upload limit")
                        return None
        if not data:
            return None
        return "source-video.mp4", bytes(data)
    except Exception as exc:
        print(f"WARN could not download source video: {config.redact(exc)}")
        return None


def _share_keyboard(message_id: int, title: str) -> str | None:
    channel = config.CHANNEL.strip()
    if not channel.startswith("@"):
        return None
    username = channel[1:]
    post_url = f"https://t.me/{username}/{message_id}"
    share_url = "https://t.me/share/url?" + urlencode({"url": post_url, "text": title[:180]})
    join_url = config.CHANNEL_JOIN_URL.strip() or f"https://t.me/{username}"
    return json.dumps({"inline_keyboard": [[
        {"text": "↗️ اشتراک‌گذاری", "url": share_url},
        {"text": "➕ عضویت در کانال", "url": join_url},
    ]]}, ensure_ascii=False)


def _attach_share_button(result: dict, title: str) -> None:
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if not message_id:
        return
    markup = _share_keyboard(int(message_id), title)
    if not markup:
        return
    try:
        _call("editMessageReplyMarkup", {
            "chat_id": config.CHANNEL,
            "message_id": message_id,
            "reply_markup": markup,
        })
    except Exception as exc:
        # The news has already been sent; a missing optional share button must not cause a duplicate retry.
        print(f"WARN could not attach share button: {config.redact(exc)}")


def _download_photo(media: Media) -> tuple[str, bytes] | None:
    if media.kind != "photo" or not media.url:
        return None
    try:
        with requests.get(media.url, headers=UA, timeout=30, stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "image" not in content_type:
                print(f"WARN photo URL did not return an image content type: {content_type}")
                return None
            data = bytearray()
            for chunk in response.iter_content(65536):
                if chunk:
                    data.extend(chunk)
                    if len(data) > PHOTO_MAX:
                        print("WARN source photo exceeds Telegram bot upload limit")
                        return None
        if not data:
            return None
        ext = "png" if "png" in content_type else "webp" if "webp" in content_type else "jpg"
        return f"source-photo.{ext}", bytes(data)
    except Exception as exc:
        print(f"WARN could not download source photo: {config.redact(exc)}")
        return None


def _send_text(text: str, silent: bool = False) -> dict:
    data = {
        "chat_id": config.CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    }
    if silent:
        data["disable_notification"] = "true"
    return _call("sendMessage", data)


def _send_video(filename: str, content: bytes, caption: str, silent: bool = False, reply_to: int | None = None) -> dict:
    data = {"chat_id": config.CHANNEL, "supports_streaming": "true"}
    if caption:
        data.update({"caption": caption, "parse_mode": "HTML"})
    if silent:
        data["disable_notification"] = "true"
    if reply_to:
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    return _call("sendVideo", data, {"video": (filename, content, "video/mp4")})


def _send_photo(filename: str, content: bytes, caption: str, silent: bool = False, reply_to: int | None = None) -> dict:
    data = {"chat_id": config.CHANNEL}
    if caption:
        data.update({"caption": caption, "parse_mode": "HTML"})
    if silent:
        data["disable_notification"] = "true"
    if reply_to:
        data["reply_parameters"] = json.dumps({"message_id": reply_to, "allow_sending_without_reply": True})
    mime = "image/png" if filename.endswith(".png") else "image/webp" if filename.endswith(".webp") else "image/jpeg"
    return _call("sendPhoto", data, {"photo": (filename, content, mime)})


def _video_repost_allowed(item: Item) -> bool:
    return config.video_allowed(item.source)


def _photo_repost_allowed(item: Item) -> bool:
    return config.photo_allowed(item.source)


def _pick_media(item: Item) -> Media | None:
    """One visual per post: the source video if there is one, otherwise the first source photo."""
    if _video_repost_allowed(item):
        for m in item.media:
            if m.kind == "video":
                return m
    if _photo_repost_allowed(item):
        for m in item.media:
            if m.kind == "photo":
                return m
    return None


def _fit_markup(post: Post, item: Item, limit: int) -> str:
    markup = build_html(post, item)
    body_limit = 3500
    while visible_len(markup) > limit and body_limit > -400:
        markup = build_html(post, item, body_limit=max(0, body_limit))
        body_limit -= 400
    return markup


def publish(item: Item, post: Post, silent: bool = False) -> dict:
    """Send one story. Returns {"message_id", "kind"}."""
    markup = _fit_markup(post, item, TEXT_LIMIT)
    chosen = _pick_media(item)
    if config.DRY_RUN:
        print("-" * 40)
        print(f"source(internal)={item.source} media={chosen.kind if chosen else 'none'} silent={silent}")
        print(markup)
        return {}

    sent = None
    kind = "text"
    if chosen is not None:
        is_video = chosen.kind == "video"
        downloaded = _download_video(chosen) if is_video else _download_photo(chosen)
        send = _send_video if is_video else _send_photo
        if downloaded and visible_len(markup) <= CAPTION_LIMIT:
            filename, content = downloaded
            sent = send(filename, content, markup, silent)
            kind = chosen.kind
        elif downloaded:
            # The caption would exceed Telegram's 1024 limit. The full text goes out first (it is the news),
            # and the video/photo follows as a reply.
            filename, content = downloaded
            sent = _send_text(markup, silent)
            _attach_share_button(sent, post.title)
            try:
                send(filename, content, "", silent, reply_to=sent.get("message_id"))
            except AmbiguousSend:
                print(f"WARN {chosen.kind} upload state unknown; not retrying (the text is already published)")
            except Exception as exc:
                print(f"WARN {chosen.kind} could not be attached to the published text: {config.redact(exc)}")
            return {"message_id": sent.get("message_id"), "kind": "text"}
        else:
            print(f"INFO {chosen.kind} unavailable for {item.source}; publishing concise text instead")

    if sent is None:
        sent = _send_text(markup, silent)
    _attach_share_button(sent, post.title)
    return {"message_id": sent.get("message_id") if isinstance(sent, dict) else None, "kind": kind}


def post_to_dict(post: Post) -> dict:
    return dataclasses.asdict(post)
