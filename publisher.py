
#!/usr/bin/env python3
"""
IRAN RADAR - Telegram Publisher

Two publication queues:
1. Verified news.
2. Preliminary breaking reports from Witness (Telegram).

Breaking reports are always explicitly labeled as unverified.
"""

import os
import html
import requests

APPS_SCRIPT_URL = os.environ["APPS_SCRIPT_URL"].strip()
APPS_SCRIPT_SECRET = os.environ["APPS_SCRIPT_SECRET"].strip()
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()

TELEGRAM_CHANNEL = "@RadarIranNews"

TIMEOUT = 45
MAX_MESSAGE_LENGTH = 4000
MAX_CAPTION_LENGTH = 1000


# ---------------------------------------------------------
# EXCEPTIONS
# ---------------------------------------------------------

class TelegramRejected(Exception):
    """Telegram explicitly rejected a request."""
    pass


# ---------------------------------------------------------
# GOOGLE APPS SCRIPT API
# ---------------------------------------------------------

def api_request(action, **extra):

    payload = {
        "secret": APPS_SCRIPT_SECRET,
        "action": action,
        **extra
    }

    response = requests.post(
        APPS_SCRIPT_URL,
        json=payload,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Invalid JSON response from Apps Script"
        ) from exc

    if not data.get("ok"):
        raise RuntimeError(
            data.get("error", "Apps Script error")
        )

    return data


# ---------------------------------------------------------
# TELEGRAM API
# ---------------------------------------------------------

def telegram_request(method, payload):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )

    response = requests.post(
        url,
        json=payload,
        timeout=TIMEOUT
    )

    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Invalid response from Telegram"
        ) from exc

    if not data.get("ok"):
        raise TelegramRejected(
            data.get("description", "Telegram rejected request")
        )

    return data["result"]


# ---------------------------------------------------------
# TEXT HELPERS
# ---------------------------------------------------------

def clean_value(value, limit):
    """
    Truncate raw text before HTML escaping.
    This prevents broken HTML entities and tags.
    """

    value = str(value or "").strip()

    if len(value) > limit:
        value = value[:limit].rsplit(" ", 1)[0].rstrip()
        value += "…"

    return value


def safe_link(value):
    """
    Only allow HTTP/HTTPS source links.
    """

    value = str(value or "").strip()

    if not value.startswith(("https://", "http://")):
        return ""

    return value


# ---------------------------------------------------------
# MESSAGE BUILDER
# ---------------------------------------------------------

def build_message(item):

    is_breaking = bool(item.get("breaking"))

    raw_title = clean_value(
        item.get("persian_title", "خبر"),
        300
    )

    raw_text = clean_value(
        item.get("persian_text", ""),
        2800
    )

    raw_source = clean_value(
        item.get("source", "منبع خبری"),
        100
    )

    title = html.escape(raw_title)
    text = html.escape(raw_text)
    source = html.escape(raw_source)

    link = safe_link(item.get("source_link", ""))

    if is_breaking:

        header = (
            "⚠️ <b>گزارش اولیه | تأییدنشده</b>\n"
            "━━━━━━━━━━━━━━\n"
        )

        warning = (
            "\n\n⚠️ <i>این گزارش هنوز به‌صورت مستقل "
            "تأیید نشده است.</i>"
        )

    else:

        header = "🛰 <b>خبر تأییدشده</b>\n"

        warning = ""

    message = (
        f"{header}"
        f"<b>{title}</b>\n\n"
        f"{text}"
        f"{warning}\n\n"
        f"📰 منبع: {source}\n"
    )

    if link:
        message += (
            f'🔗 <a href="{html.escape(link, quote=True)}">'
            f"مشاهده خبر اصلی</a>\n"
        )

    message += (
        "\n#ایران #اخبار\n"
        "@RadarIranNews"
    )

    # Final safety limit. The source and warning are preserved
    # by conservative limits above.
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValueError(
            "Generated Telegram message exceeds safe length"
        )

    return message


# ---------------------------------------------------------
# PUBLISH ONE ITEM
# ---------------------------------------------------------

def publish_item(item):

    message = build_message(item)

    image_url = str(
        item.get("image_url", "")
    ).strip()

    # Try photo only when the complete caption fits.
    if image_url and len(message) <= MAX_CAPTION_LENGTH:

        try:

            result = telegram_request(
                "sendPhoto",
                {
                    "chat_id": TELEGRAM_CHANNEL,
                    "photo": image_url,
                    "caption": message,
                    "parse_mode": "HTML"
                }
            )

            return result

        except TelegramRejected as error:

            # Telegram explicitly rejected the photo request.
            # A text-only fallback is safe in this case.
            print(
                "Photo rejected; trying text:",
                str(error)
            )

    # Do not fall back after timeouts or ambiguous network
    # failures: Telegram may already have accepted the message.
    result = telegram_request(
        "sendMessage",
        {
            "chat_id": TELEGRAM_CHANNEL,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
    )

    return result


# ---------------------------------------------------------
# UPDATE SPREADSHEET STATUS
# ---------------------------------------------------------

def mark_published(action, external_id):

    result = api_request(
        action,
        external_ids=[external_id]
    )

    published_ids = result.get("published", [])

    if external_id not in published_ids:
        raise RuntimeError(
            "Apps Script did not confirm publication status: "
            + external_id
        )


def mark_failed(external_id, error):

    try:

        api_request(
            "mark_failed",
            external_ids=[external_id],
            reason=str(error)[:400]
        )

    except Exception as update_error:

        print(
            "Could not update failure status:",
            str(update_error)
        )


# ---------------------------------------------------------
# PROCESS PUBLICATION QUEUE
# ---------------------------------------------------------

def process_queue(items, breaking=False):

    published_count = 0
    failed_count = 0

    status_action = (
        "mark_breaking_published"
        if breaking
        else "mark_published"
    )

    for item in items:

        external_id = str(
            item.get("external_id", "")
        ).strip()

        if not external_id:
            print("Skipping item without external_id")
            continue

        try:

            # Additional protection against accidental
            # cross-queue publication.
            if breaking:
                if item.get("source") != "Witness (Telegram)":
                    raise ValueError(
                        "Breaking source is not authorized"
                    )

                item["breaking"] = True

            else:
                item["breaking"] = False

            telegram_result = publish_item(item)

            message_id = telegram_result.get("message_id")

            print(
                "Telegram accepted:",
                external_id,
                "message_id:",
                message_id
            )

            # Mark only after Telegram confirms success.
            mark_published(
                status_action,
                external_id
            )

            published_count += 1

            print(
                "Publication recorded:",
                external_id
            )

        except Exception as error:

            failed_count += 1

            print(
                "Publication error:",
                external_id,
                str(error)
            )

            mark_failed(
                external_id,
                error
            )

    return published_count, failed_count


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():

    print("===================================")
    print("IRAN RADAR publisher started.")
    print("===================================")

    # Translate up to five pending items before fetching queues.
    try:

        translation = api_request("translate_pending")

        print(
            "Translation:",
            "translated =", translation.get("translated", 0),
            "| failed =", translation.get("failed", 0)
        )

    except Exception as error:

        print(
            "Translation request failed:",
            str(error)
        )

    # Queue 1: independently verified news.
    verified_result = api_request("get_ready")
    verified_items = verified_result.get("items", [])

    print(
        "Verified news ready:",
        len(verified_items)
    )

    verified_published, verified_failed = process_queue(
        verified_items,
        breaking=False
    )

    # Queue 2: preliminary reports from trusted Telegram source.
    breaking_result = api_request("get_breaking_ready")
    breaking_items = breaking_result.get("items", [])

    print(
        "Breaking reports ready:",
        len(breaking_items)
    )

    breaking_published, breaking_failed = process_queue(
        breaking_items,
        breaking=True
    )

    print("===================================")
    print("PUBLICATION SUMMARY")
    print("Verified published:", verified_published)
    print("Breaking published:", breaking_published)
    print("Verified failed:", verified_failed)
    print("Breaking failed:", breaking_failed)
    print("===================================")


if __name__ == "__main__":
    main()
