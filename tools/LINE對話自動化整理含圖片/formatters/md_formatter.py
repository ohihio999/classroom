from collections import defaultdict
from datetime import datetime
from pathlib import Path

import config
from core import conversation_store


def flush_to_markdown() -> list[Path]:
    messages = conversation_store.load_all()
    if not messages:
        print("沒有待處理的訊息。")
        return []

    # 依 (日期, 群組) 分桶
    buckets: dict[tuple, list] = defaultdict(list)
    for msg in messages:
        local_ts = datetime.fromisoformat(msg["timestamp"]).astimezone()
        date_key = local_ts.strftime("%Y-%m-%d")
        group_name = msg.get("group_name", "群組")
        buckets[(date_key, group_name)].append((local_ts, msg))

    config.MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for (date_key, group_name), entries in sorted(buckets.items()):
        content = _format_day(date_key, group_name, entries)
        # 群組名截短避免檔名過長
        safe_group = group_name[:20].replace("/", "_")
        filepath = config.MARKDOWN_DIR / f"{date_key}_{safe_group}.md"
        filepath.write_text(content, encoding="utf-8")
        print(f"[OK] {filepath}")
        written.append(filepath)

    conversation_store.clear()
    return written


def _format_day(date_key: str, group_name: str, entries: list) -> str:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# LINE 群組：{group_name} {date_key}",
        f"> 產出時間：{now_str}",
        "",
    ]

    for local_ts, msg in sorted(entries, key=lambda x: x[0]):
        time_str = local_ts.strftime("%H:%M")
        sender = msg["sender_name"]
        lines.append(f"**{sender}** `{time_str}`")

        if msg["message_type"] == "image" and msg.get("image_path"):
            img_path = msg["image_path"]
            filename = img_path.split("/")[-1]
            lines.append(f"![{filename}](../{img_path})")
        else:
            lines.append(f"> {msg['content']}")

        lines.append("")

    return "\n".join(lines)
