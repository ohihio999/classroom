# v0.1.0 | 2026-05-19
# 偵測 LINE 安裝位置與資料庫檔案

import pathlib
import psutil


LINE_DB_PATH = pathlib.Path.home() / "AppData" / "Local" / "LINE" / "Data" / "db"


def find_line_pid() -> int | None:
    """找 LINE.exe 的 PID，LINE 必須在執行中才能提取金鑰。"""
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if proc.info["name"] and proc.info["name"].lower() == "line.exe":
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


def find_edb_files() -> list[pathlib.Path]:
    """找主資料庫 .edb 檔（格式：qw<帳號ID>.edb）。"""
    if not LINE_DB_PATH.exists():
        return []
    return [p for p in LINE_DB_PATH.glob("qw*.edb") if not p.name.endswith(("-shm", "-wal"))]


def get_account_id(edb_path: pathlib.Path) -> str:
    """從 .edb 檔名取出帳號 ID（去掉 qw 前綴）。"""
    return edb_path.stem.removeprefix("qw")


def detect() -> dict:
    """執行完整偵測，回傳狀態資訊。"""
    pid = find_line_pid()
    edb_files = find_edb_files()

    return {
        "line_running": pid is not None,
        "line_pid": pid,
        "db_path": str(LINE_DB_PATH),
        "accounts": [
            {"edb": str(f), "account_id": get_account_id(f)}
            for f in edb_files
        ],
    }


if __name__ == "__main__":
    import json
    result = detect()
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result["line_running"]:
        print("\n[!] LINE 未執行，無法提取金鑰。請先開啟 LINE。")
    elif not result["accounts"]:
        print("\n[!] 找不到 .edb 資料庫檔案。")
    else:
        print(f"\n[OK] LINE 執行中 (PID: {result['line_pid']})")
        print(f"[OK] 找到 {len(result['accounts'])} 個帳號資料庫")
