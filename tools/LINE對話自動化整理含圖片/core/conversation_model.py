from dataclasses import dataclass
from typing import Optional


@dataclass
class Message:
    timestamp: str       # ISO 8601，UTC
    sender_id: str
    sender_name: str
    content: str
    message_type: str    # "text" | "image"
    group_name: str = "群組"
    image_path: Optional[str] = None  # 相對於 output/ 的路徑
