from datetime import datetime, timezone

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiClient, Configuration, MessagingApi
from linebot.v3.webhooks import ImageMessageContent, MessageEvent, TextMessageContent

import config
from core import content_downloader, conversation_store, image_handler
from core.conversation_model import Message

app = Flask(__name__)
handler = WebhookHandler(config.LINE_CHANNEL_SECRET)

# 簡易 display name 快取，避免重複呼叫 API
_name_cache: dict[str, str] = {}


def _get_display_name(group_id: str | None, user_id: str) -> str:
    if user_id in _name_cache:
        return _name_cache[user_id]

    name = user_id  # 預設 fallback
    try:
        cfg = Configuration(access_token=config.LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(cfg) as api_client:
            api = MessagingApi(api_client)
            if group_id:
                profile = api.get_group_member_profile(group_id, user_id)
            else:
                profile = api.get_profile(user_id)
            name = profile.display_name
    except Exception:
        pass

    _name_cache[user_id] = name
    return name


def _group_id(event: MessageEvent) -> str | None:
    return getattr(event.source, "group_id", None)


def _ts_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event: MessageEvent):
    uid = event.source.user_id or "unknown"
    gid = _group_id(event)
    name = _get_display_name(gid, uid)
    timestamp = _ts_to_iso(event.timestamp)

    msg = Message(
        timestamp=timestamp,
        sender_id=uid,
        sender_name=name,
        content=event.message.text,
        message_type="text",
        group_name=gid or "個人",
    )
    conversation_store.append_message(msg)
    print(f"[TEXT] {name}: {event.message.text[:60]}")


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event: MessageEvent):
    uid = event.source.user_id or "unknown"
    gid = _group_id(event)
    name = _get_display_name(gid, uid)
    timestamp = _ts_to_iso(event.timestamp)

    image_bytes = content_downloader.download_image(event.message.id)
    rel_path = image_handler.save_image(image_bytes, name, timestamp)

    msg = Message(
        timestamp=timestamp,
        sender_id=uid,
        sender_name=name,
        content="[圖片]",
        message_type="image",
        group_name=gid or "個人",
        image_path=rel_path,
    )
    conversation_store.append_message(msg)
    print(f"[IMAGE] {name} → {rel_path}")


def run(port: int = 5000):
    print(f"✅ Webhook server 啟動 → http://localhost:{port}/callback")
    print("請在另一個終端機執行：ngrok http 5000")
    print("並將 HTTPS URL/callback 填入 LINE Developers Console → Webhook URL")
    app.run(port=port, debug=False)
