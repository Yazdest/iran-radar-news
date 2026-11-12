
import os
import html
import requests

APPS_SCRIPT_URL = os.environ["APPS_SCRIPT_URL"]
APPS_SCRIPT_SECRET = os.environ["APPS_SCRIPT_SECRET"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL = "@RadarIranNews"

TIMEOUT = 45


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

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(data.get("error", "Apps Script error"))

    return data


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

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            data.get("description", "Telegram API error")
        )

    return data["result"]


def build_message(item):
    title = html.escape(item["persian_title"])
    text = html.escape(item["persian_text"])

    source = html.escape(item.get("source", "منبع خبری"))
    link = html.escape(item.get("source_link", ""), quote=True)

    message = (
        f"<b>🛰 {title}</b>\n\n"
        f"{text}\n\n"
        f"📰 منبع: {source}\n"
        f'🔗 <a href="{link}">مشاهده خبر اصلی</a>\n\n'
        f"#ایران #اخبار\n"
        f"@RadarIranNews"
    )

    # Telegram text message limit: 4096 characters.
    if len(message) > 4000:
        message = message[:3950] + "\n\n@RadarIranNews"

    return message


def publish_item(item):
    message = build_message(item)

    image_url = str(item.get("image_url", "")).strip()

    # Use an image only when the caption fits Telegram's limit.
    if image_url and len(message) <= 1000:
        try:
            telegram_request("sendPhoto", {
                "chat_id": TELEGRAM_CHANNEL,
                "photo": image_url,
                "caption": message,
                "parse_mode": "HTML"
            })
            return
        except Exception as error:
            print("Photo delivery failed; trying text:", error)

    telegram_request("sendMessage", {
        "chat_id": TELEGRAM_CHANNEL,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    })


def main():
    print("IRAN RADAR publisher started.")

    result = api_request("get_ready")
    items = result.get("items", [])

    print("Verified news ready:", len(items))

    published = 0
    failed = 0

    for item in items:
        external_id = item["external_id"]

        try:
            publish_item(item)

            # Update the spreadsheet only after Telegram confirms success.
            api_request(
                "mark_published",
                external_ids=[external_id]
            )

            published += 1
            print("Published:", external_id)

        except Exception as error:
            failed += 1
            print("Publication failed:", external_id, str(error))

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

    print("Publication finished.")
    print("Published:", published)
    print("Failed:", failed)


if __name__ == "__main__":
    main()
