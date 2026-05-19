# v0.1.0 | 2026-05-20
# LINE 備份工具 — 互動式選單

import io
import os
import pathlib
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")

SCRIPT_DIR = pathlib.Path(__file__).parent
PYTHON = sys.executable


def run(args: list[str]):
    """執行子程序，繼承目前終端機。"""
    subprocess.run([PYTHON] + args, cwd=SCRIPT_DIR)


def open_html():
    """找最新備份的 index.html 並用瀏覽器開啟。"""
    backup_root = pathlib.Path("D:/LINE自製備份")
    html = None

    # html/ 在時間戳目錄下（如 20260519_215423/html/index.html）
    for ts_dir in sorted(backup_root.glob("20*"), reverse=True):
        candidate = ts_dir / "html" / "index.html"
        if candidate.exists():
            html = candidate
            break

    if html:
        print(f"開啟：{html}")
        os.startfile(str(html))
    else:
        print("[!] 找不到 HTML，請先選 [5] 產生 HTML。")
        input("按 Enter 繼續...")


def setup_schedule():
    """用系統管理員身分執行排程設定。"""
    ps1 = SCRIPT_DIR / "schedule_task.ps1"
    cmd = (
        f'powershell -Command "Start-Process powershell '
        f'-ArgumentList \'-ExecutionPolicy Bypass -File \\\"{ps1}\\\"\' -Verb RunAs"'
    )
    os.system(cmd)
    input("\n排程設定完成後按 Enter 繼續...")


MENU = """
================================================
  LINE 備份工具
================================================

  [1] 差異備份      （只複製新增的，幾秒完成）
  [2] 完整備份      （全部重備份，約 26 分鐘）
  [3] 刪除群組備份  （互動式選擇要刪哪個）
  [4] 搜尋訊息      （互動式全文搜尋）
  [5] 產生 HTML     （把聊天轉成網頁）
  [6] 開啟 HTML     （用瀏覽器瀏覽聊天記錄）
  [7] 設定定時排程  （每天 03:00 自動備份）
  [8] 離開
"""

ACTIONS = {
    "1": lambda: run(["backup.py", "--diff"]),
    "2": lambda: run(["backup.py"]),
    "3": lambda: run(["backup.py", "--delete"]),
    "4": lambda: run(["search.py"]),
    "5": lambda: run(["html_viewer.py"]),
    "6": open_html,
    "7": setup_schedule,
}

def main():
    while True:
        os.system("cls")
        print(MENU)
        try:
            choice = input("請選擇 [1-8]：").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if choice == "8":
            break

        action = ACTIONS.get(choice)
        if action:
            print()
            action()
        else:
            print("請輸入 1 到 8。")
            input("按 Enter 繼續...")

if __name__ == "__main__":
    main()
