import re
from datetime import datetime
from pathlib import Path

import config


def _sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def save_image(image_bytes: bytes, sender_name: str, timestamp: str) -> str:
    """
    存圖片，命名規則：YYYYMMDD_發話人_NNN.jpg
    回傳相對於 output/ 的路徑字串（供 Markdown 連結使用）。
    """
    dt = datetime.fromisoformat(timestamp).astimezone()
    date_str = dt.strftime("%Y%m%d")
    month_dir = config.IMAGES_DIR / dt.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)

    safe_sender = _sanitize(sender_name)
    seq = _next_seq(month_dir, date_str, safe_sender)
    filename = f"{date_str}_{safe_sender}_{seq:03d}.jpg"
    (month_dir / filename).write_bytes(image_bytes)

    rel = (month_dir / filename).relative_to(config.OUTPUT_DIR)
    return str(rel).replace("\\", "/")


def _next_seq(directory: Path, date_str: str, sender: str) -> int:
    prefix = f"{date_str}_{sender}_"
    nums = []
    for f in directory.glob(f"{prefix}*.jpg"):
        try:
            nums.append(int(f.stem[len(prefix):len(prefix) + 3]))
        except ValueError:
            pass
    return max(nums) + 1 if nums else 1
