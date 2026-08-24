import json
import threading
from dataclasses import asdict

import config
from core.conversation_model import Message

_lock = threading.Lock()


def append_message(msg: Message) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        messages = _load()
        messages.append(asdict(msg))
        config.PENDING_FILE.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_all() -> list[dict]:
    with _lock:
        return _load()


def clear() -> None:
    with _lock:
        config.PENDING_FILE.write_text("[]", encoding="utf-8")


def _load() -> list[dict]:
    if not config.PENDING_FILE.exists():
        return []
    try:
        return json.loads(config.PENDING_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
