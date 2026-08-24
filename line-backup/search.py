# v0.2.0 | 2026-05-20
# 變更：使用 name_resolver 顯示群組名稱

import io
import json
import pathlib
import sys
from datetime import datetime

from name_resolver import NameResolver


def ts_to_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M")


def load_chats(chats_dir: pathlib.Path) -> list[dict]:
    """載入所有聊天室，回傳 [{chat_mid, messages}] 清單。"""
    result = []
    for f in sorted(chats_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            result.append(data)
        except Exception:
            pass
    return result


def search(chats: list[dict], query: str, case_sensitive: bool = False,
           limit: int = 50) -> list[dict]:
    """
    全文搜尋，回傳符合的訊息清單：
    [{chat_mid, message_id, fromMid, text, createdTime, time_str}]
    """
    q = query if case_sensitive else query.lower()
    results = []

    for chat in chats:
        mid = chat.get("chat_mid", "")
        for msg in chat.get("messages", []):
            text = msg.get("text", "")
            if not text:
                continue
            haystack = text if case_sensitive else text.lower()
            if q in haystack:
                results.append({
                    "chat_mid": mid,
                    "message_id": msg.get("id", ""),
                    "fromMid": msg.get("fromMid", ""),
                    "text": text,
                    "createdTime": msg.get("createdTime", 0),
                    "time_str": ts_to_str(msg.get("createdTime", 0)),
                })
                if len(results) >= limit:
                    return results

    return results


def highlight(text: str, query: str, case_sensitive: bool = False) -> str:
    """在文字中標記搜尋關鍵字（終端機用）。"""
    if not case_sensitive:
        idx = text.lower().find(query.lower())
    else:
        idx = text.find(query)
    if idx == -1:
        return text
    # 只取關鍵字前後 40 字的片段
    start = max(0, idx - 40)
    end = min(len(text), idx + len(query) + 40)
    snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
    # 用 >> << 標記關鍵字
    kw = text[idx:idx + len(query)]
    return snippet.replace(kw, f">>{kw}<<")


def print_results(results: list[dict], query: str, resolver: "NameResolver | None" = None,
                  chats: list[dict] | None = None):
    if not results:
        print(f'找不到包含「{query}」的訊息。')
        return

    # 建立 chat_mid -> messages 的快速查表（用於 composite 命名）
    chat_msgs: dict[str, list] = {}
    if chats:
        for c in chats:
            chat_msgs[c.get("chat_mid", "")] = c.get("messages", [])

    print(f'\n找到 {len(results)} 筆結果：\n{"─"*60}')
    for r in results:
        if resolver:
            msgs = chat_msgs.get(r["chat_mid"], [])
            chat_name, _ = resolver.resolve_chat(r["chat_mid"], msgs)
            sender = resolver.resolve_sender(r["fromMid"])
        else:
            chat_name = r["chat_mid"][:20]
            sender = r["fromMid"][-8:]
        snippet = highlight(r["text"], query)
        print(f'[{r["time_str"]}] {chat_name}')
        print(f'  {sender}: {snippet}')
        print()


def interactive_search(chats_dir: pathlib.Path, resolver: "NameResolver | None" = None):
    """互動式搜尋介面。"""
    print("載入聊天室...")
    chats = load_chats(chats_dir)
    total_msgs = sum(len(c.get("messages", [])) for c in chats)
    print(f"已載入 {len(chats)} 個聊天室，共 {total_msgs:,} 則訊息")
    print('輸入關鍵字搜尋，按 Ctrl+C 離開\n')

    while True:
        try:
            query = input("搜尋：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n離開")
            break

        if not query:
            continue

        # 支援 limit 參數：「keyword :100」
        limit = 50
        if " :" in query:
            parts = query.rsplit(" :", 1)
            try:
                limit = int(parts[1])
                query = parts[0].strip()
            except ValueError:
                pass

        results = search(chats, query, limit=limit)
        print_results(results, query, resolver, chats)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")

    backup_root = pathlib.Path("D:/LINE自製備份")
    timestamped = sorted(
        [p for p in backup_root.glob("*/47234ddb9e79bc22b3074b31cb876d")
         if p.parent.name.startswith("2")],
        reverse=True,
    )
    if timestamped:
        acct_dir = timestamped[0]
    elif len(sys.argv) > 1:
        acct_dir = pathlib.Path(sys.argv[1])
    else:
        print("[!] 找不到備份目錄，請先執行 backup.py")
        sys.exit(1)
    chats_dir = acct_dir / "chats"

    # notes 優先用 LINE桌面備份
    notes_src = pathlib.Path("D:/LINE桌面備份/47234ddb9e79bc22b3074b31cb876d")
    if not notes_src.exists():
        notes_src = acct_dir

    print(f"來源：{chats_dir}")
    resolver = NameResolver(notes_src)
    print(f"名稱庫：群組 {len(resolver.group_names)}  用戶 {len(resolver.user_names)}  自訂 {len(resolver.custom_names)}")

    if len(sys.argv) >= 2 and not pathlib.Path(sys.argv[1]).exists():
        query = sys.argv[1]
        chats = load_chats(chats_dir)
        results = search(chats, query, limit=100)
        print_results(results, query, resolver, chats)
    else:
        interactive_search(chats_dir, resolver)
