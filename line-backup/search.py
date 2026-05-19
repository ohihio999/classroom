# v0.1.0 | 2026-05-19
# 全文搜尋工具 — 搜尋所有 LINE 聊天室的訊息

import io
import json
import pathlib
import sys
from datetime import datetime


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


def print_results(results: list[dict], query: str, name_map: dict | None = None):
    if not results:
        print(f'找不到包含「{query}」的訊息。')
        return

    print(f'\n找到 {len(results)} 筆結果：\n{"─"*60}')
    for r in results:
        chat = (name_map or {}).get(r["chat_mid"], r["chat_mid"][:20])
        sender = (name_map or {}).get(r["fromMid"], r["fromMid"][-8:])
        snippet = highlight(r["text"], query)
        print(f'[{r["time_str"]}] {chat}')
        print(f'  {sender}: {snippet}')
        print()


def interactive_search(chats_dir: pathlib.Path, name_map: dict | None = None):
    """互動式搜尋介面。"""
    print(f"載入聊天室...")
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
        print_results(results, query, name_map)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")

    # 找最新備份
    backup_root = pathlib.Path("D:/LINE自製備份")
    backups = sorted(backup_root.glob("*/47234ddb9e79bc22b3074b31cb876d"), reverse=True)
    if not backups:
        # 也接受命令列參數
        if len(sys.argv) > 1:
            chats_dir = pathlib.Path(sys.argv[1])
        else:
            print("[!] 找不到備份目錄，請先執行 backup.py")
            sys.exit(1)
    else:
        chats_dir = backups[0] / "chats"

    print(f"來源：{chats_dir}")

    # 命令列搜尋模式：python search.py "關鍵字"
    if len(sys.argv) >= 2 and not pathlib.Path(sys.argv[1]).exists():
        query = sys.argv[1]
        chats = load_chats(chats_dir)
        results = search(chats, query, limit=100)
        print_results(results, query)
    else:
        interactive_search(chats_dir)
