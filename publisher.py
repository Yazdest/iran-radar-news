"""Publish short Persian text posts and eligible source videos to Telegram."""
from __future__ import annotations

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


class TelegramRejected(Exception):
    pass


def _topic_tag(post: Post) -> str:
    text = f"{post.title} {post.body}"
    if post.kind == "breaking" or translator.is_urgent_military_development(text):
        return "#خبر_فوری"
    # Military news and statements by US/Israeli officials about Iran are urgent.
    us_israel = re.search(r"ترامپ|ونس|روبیو|هگست|کاخ سفید|آمریکا|ایالات متحده|نتانیاهو|کاتس|ساعر|ارتش اسرائیل|اسرائیل|واشنگتن|تل.?آویو|\b(trump|vance|rubio|hegseth|white house|united states|israel|netanyahu|katz|idf)\b", text, re.I)
    iran = re.search(r"ایران|تهران|جمهوری اسلامی|\biran\b|\btehran\b", text, re.I)
    statement = re.search(r"اظهار|اظهارات|گفت|اعلام|موضع|هشدار|تهدید|بیانیه|انتقاد|تأکید|تصریح|بررسی|\b(said|says|stated|warned|announced|comments?|remarks?|criticized|statement)\b", text, re.I)
    military = re.search(r"جنگ|حمله|موشک|پهپاد|پدافند|نظامی|درگیری|عملیات|airstrike|missile|military|war|attack|drone", text, re.I)
    if military or (us_israel and iran and statement):
        return "#خبر_فوری"
    if re.search(r"دلار|ارز|تورم|نفت|بورس|اقتصاد|بودجه|ریال|تومان", text):
        return "#اقتصاد"
    if re.search(r"مذاکر(?:ه|ات)|توافق|آتش.?بس|دیپلماسی|تحریم", text):
        return "#مذاکرات"
    return "#اخبار_روز"


def build_html(post: Post, item: Item) -> str:
    e = html.escape
    tag = _topic_tag(post)
    head = f"🚨 <b>{tag}</b>" if tag == "#خبر_فوری" else f"📰 <b>{tag}</b>"
    parts = [f"{head}\n\n<b>{e(post.title)}</b>" if post.title else head]
    if post.translation_unavailable:
        parts.append("⚠️ ترجمه در دسترس نیست؛ متن اصلی منتشر شده است.")

    clean_body = translator._clean_source_formatting(post.body)
    # WFWitness is the sole Telegram source whose plain identifier is public.
    # Keep it inline at the end of the news paragraph, exactly as requested.
    if item.source.casefold().lstrip("@") == "wfwitness":
        clean_body = f"{clean_body} | منبع: wfwitness" if clean_body else "منبع: wfwitness"
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
    for _ in range(3):
        response = requests.post(url, data=data, files=files, timeout=120)
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
        print(f"WARN could not download source video: {exc}")
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
        {"text": "↗️ اشتراک خبر", "url": share_url},
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
        print(f"WARN could not attach share button: {exc}")


def _send_text(text: str) -> dict:
    return _call("sendMessage", {
        "chat_id": config.CHANNEL,
        "text": text[:TEXT_LIMIT],
        "parse_mode": "HTML",
        "link_preview_options": json.dumps({"is_disabled": True}),
    })


def _send_video(filename: str, content: bytes, caption: str) -> dict:
    return _call("sendVideo", {
        "chat_id": config.CHANNEL,
        "caption": caption[:CAPTION_LIMIT],
        "parse_mode": "HTML",
        "supports_streaming": "true",
    }, {"video": (filename, content, "video/mp4")})


def _video_repost_allowed(item: Item) -> bool:
    return item.source.casefold() not in {"iranintltv", "iranintl_tv"}


def publish(item: Item, post: Post) -> None:
    markup = build_html(post, item)
    if config.DRY_RUN:
        eligible_videos = [m for m in item.media if m.kind == "video"] if _video_repost_allowed(item) else []
        print("-" * 40)
        print(f"source(internal)={item.source} video_count={len(eligible_videos)}")
        print(markup)
        return

    # Explicit exception: Iran International videos are never copied/re-uploaded.
    videos = [m for m in item.media if m.kind == "video"] if _video_repost_allowed(item) else []
    sent = None
    if videos:
        downloaded = _download_video(videos[0])
        if downloaded and visible_len(markup) <= CAPTION_LIMIT:
            filename, content = downloaded
            sent = _send_video(filename, content, markup)
        elif not downloaded:
            print(f"INFO video unavailable for {item.source}; publishing concise text instead")

    if sent is None:
        sent = _send_text(markup)
    _attach_share_button(sent, post.title)
