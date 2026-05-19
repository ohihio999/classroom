# v0.1.0 | 2026-05-19
# 開啟 LINE 加密資料庫並讀取訊息
#
# LINE 使用 SQLCipher 加密 SQLite。
# 需要先用 key_extractor.py 取得金鑰，再用此模組開啟資料庫。
# 安裝 SQLCipher: pip install pysqlcipher3
# 若安裝困難，可改用 sqlcipher CLI 工具先匯出明文再讀取。

import pathlib
import sqlite3
from datetime import datetime


def _try_open_sqlcipher(db_path: pathlib.Path, key_hex: str):
    """嘗試用 pysqlcipher3 開啟加密資料庫。"""
    try:
        import pysqlcipher3.dbapi2 as sqlcipher
    except ImportError:
        raise ImportError("請安裝 pysqlcipher3: pip install pysqlcipher3")

    for compat in [3, 4]:
        try:
            conn = sqlcipher.connect(str(db_path))
            conn.execute(f"PRAGMA key=\"x'{key_hex}'\"")
            conn.execute(f"PRAGMA cipher_compatibility = {compat}")
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            print(f"[OK] 資料庫開啟成功 (SQLCipher v{compat})")
            return conn
        except Exception as e:
            continue

    raise ValueError(f"無法開啟資料庫：金鑰可能錯誤或 SQLCipher 參數不符。")


def open_db(db_path: pathlib.Path, key_hex: str):
    """開啟 LINE 資料庫，回傳 connection。"""
    if not db_path.exists():
        raise FileNotFoundError(f"資料庫不存在：{db_path}")
    return _try_open_sqlcipher(db_path, key_hex)


def list_tables(conn) -> list[str]:
    """列出所有資料表。"""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return [row[0] for row in cur.fetchall()]


def get_chats(conn) -> list[dict]:
    """取得所有聊天室清單（群組 + 個人）。"""
    try:
        cur = conn.execute("""
            SELECT chat_id, name, type, last_message_at
            FROM chat
            ORDER BY last_message_at DESC
        """)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as e:
        print(f"[!] 讀取聊天室失敗（表名可能不同）: {e}")
        return []


def get_messages(conn, chat_id: str, limit: int = 1000, offset: int = 0) -> list[dict]:
    """取得指定聊天室的訊息。"""
    try:
        cur = conn.execute("""
            SELECT id, from_id, text, created_time, content_type
            FROM message
            WHERE chat_id = ?
            ORDER BY created_time DESC
            LIMIT ? OFFSET ?
        """, (chat_id, limit, offset))
        cols = [d[0] for d in cur.description]
        rows = []
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            # 將 ms timestamp 轉成可讀時間
            if d.get("created_time"):
                d["created_time_str"] = datetime.fromtimestamp(
                    d["created_time"] / 1000
                ).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(d)
        return rows
    except Exception as e:
        print(f"[!] 讀取訊息失敗（表名可能不同）: {e}")
        return []


def probe_schema(conn) -> dict:
    """
    探索資料庫 schema，印出所有表和欄位。
    用於在不知道 LINE 資料庫結構時，先偵察再設計查詢。
    """
    tables = list_tables(conn)
    schema = {}
    for table in tables:
        cur = conn.execute(f"PRAGMA table_info({table})")
        cols = [row[1] for row in cur.fetchall()]
        # 取前幾筆資料看看
        try:
            cur2 = conn.execute(f"SELECT * FROM {table} LIMIT 3")
            sample = cur2.fetchall()
        except Exception:
            sample = []
        schema[table] = {"columns": cols, "sample_rows": len(sample)}
    return schema


if __name__ == "__main__":
    import json
    from detector import find_edb_files

    edb_files = find_edb_files()
    if not edb_files:
        print("[!] 找不到 .edb 資料庫")
        exit(1)

    # 從命令列讀金鑰（測試用）
    import sys
    if len(sys.argv) < 2:
        print("用法: python db_reader.py <key_hex>")
        print("範例: python db_reader.py ea903f04b93c5ff2165724d5604bbd18")
        exit(1)

    key = sys.argv[1]
    db = edb_files[0]
    print(f"嘗試開啟：{db}")
    print(f"使用金鑰：{key[:8]}...")

    conn = open_db(db, key)

    print("\n=== 資料庫表結構 ===")
    schema = probe_schema(conn)
    for table, info in schema.items():
        print(f"  {table}: {info['columns']}")

    conn.close()
