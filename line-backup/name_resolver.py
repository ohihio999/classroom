# v1.0.0 | 2026-05-20
# 四層命名解析：自訂名稱 > notes 群組名 > 參與者組合 > 首則文字提示

import json
import pathlib

CHAT_NAMES_FILE = pathlib.Path(__file__).parent / "chat_names.json"


def _load_notes_names(backup_acct_dir: pathlib.Path) -> tuple[dict, dict]:
    """從 notes/*.json 讀取 group_names 和 user_names。"""
    group_names: dict[str, str] = {}
    user_names: dict[str, str] = {}
    notes_dir = backup_acct_dir / "notes"
    if not notes_dir.exists():
        return group_names, user_names
    for f in notes_dir.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            for note in d.get("notes", []):
                gm = note.get("groupMid")
                gn = (note.get("groupName") or "").strip()
                if gm and gn:
                    group_names[gm] = gn
                am = note.get("authorMid")
                an = (note.get("authorName") or "").strip()
                if am and an:
                    user_names[am] = an
                for c in note.get("comments", []) or []:
                    cm = c.get("authorMid")
                    cn = (c.get("authorName") or "").strip()
                    if cm and cn:
                        user_names[cm] = cn
        except Exception:
            pass
    return group_names, user_names


def load_custom_names() -> dict[str, str]:
    if CHAT_NAMES_FILE.exists():
        try:
            return json.loads(CHAT_NAMES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_custom_names(names: dict[str, str]) -> None:
    CHAT_NAMES_FILE.write_text(
        json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class NameResolver:
    def __init__(self, backup_acct_dir: pathlib.Path):
        self.group_names, self.user_names = _load_notes_names(backup_acct_dir)
        self.custom_names = load_custom_names()

    def reload_custom(self) -> None:
        self.custom_names = load_custom_names()

    def set_custom(self, mid: str, name: str) -> None:
        self.custom_names[mid] = name
        save_custom_names(self.custom_names)

    def resolve_chat(self, mid: str, messages: list[dict] | None = None) -> tuple[str, str]:
        """
        回傳 (display_name, tier)。
        tier: "custom" / "group" / "composite" / "text" / "hash"
        """
        # Tier 1: 自訂名稱
        if mid in self.custom_names:
            return self.custom_names[mid], "custom"

        # Tier 2: notes 群組名
        if mid in self.group_names:
            return self.group_names[mid], "group"

        # Tier 3: 參與者組合
        if messages:
            senders: dict[str, str] = {}
            for m in messages[:100]:
                fm = m.get("fromMid", "")
                if fm and fm in self.user_names and fm not in senders:
                    senders[fm] = self.user_names[fm]
                if len(senders) >= 4:
                    break
            if senders:
                names = list(senders.values())[:3]
                label = " / ".join(names)
                if len(senders) > 3:
                    label += " ..."
                return f"({label})", "composite"

        # Tier 4: 首則文字提示
        if messages:
            for m in messages[:10]:
                text = (m.get("text") or "").strip()
                if text and m.get("contentType", 0) == 0:
                    snippet = text[:30].replace("\n", " ")
                    if len(text) > 30:
                        snippet += "…"
                    return f"「{snippet}」", "text"

        # Fallback: 短 hash
        return mid[:20] + "...", "hash"

    def resolve_sender(self, mid: str, fallback_name: str = "") -> str:
        if mid in self.custom_names:
            return self.custom_names[mid]
        if mid in self.user_names:
            return self.user_names[mid]
        return fallback_name or mid[-8:]
