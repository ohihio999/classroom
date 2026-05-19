# v0.4.0 | 2026-05-20
# LINE 備份主程式
#
# 資料來源（雙軌）：
#   1. LINE Data Master 的備份目錄（主要，完整 297 個聊天室）
#   2. MCP API（補充，取最近 30 個聊天室的即時資料）
#
# 差異備份：--diff 模式只複製新增或修改的檔案（以 mtime+size 判斷）
# 用法：python backup.py [--output D:\my-backup] [--diff]

import argparse
import hashlib
import io
import json
import pathlib
import shutil
import sys
import time
from datetime import datetime


# LINE Data Master 的備份根目錄
DEFAULT_LDM_BACKUP = pathlib.Path("D:/LINE桌面備份")
DEFAULT_OUTPUT = pathlib.Path("D:/LINE自製備份")


MANIFEST_NAME = ".backup_manifest.json"


def parse_args():
    p = argparse.ArgumentParser(description="LINE 備份工具 v0.3")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT),
                   help=f"備份輸出目錄（預設 {DEFAULT_OUTPUT}）")
    p.add_argument("--source-dir", default=str(DEFAULT_LDM_BACKUP),
                   help=f"LINE Data Master 備份來源（預設 {DEFAULT_LDM_BACKUP}）")
    p.add_argument("--mcp", action="store_true",
                   help="同時從 MCP API 補充最近 30 個聊天的即時資料")
    p.add_argument("--diff", action="store_true",
                   help="差異備份：只複製新增或修改的檔案（加快速度）")
    p.add_argument("--limit", type=int, default=0,
                   help="只備份前 N 個項目（測試用）")
    p.add_argument("--delete", action="store_true",
                   help="互動式刪除單一聊天室的備份")
    return p.parse_args()


def file_sig(path: pathlib.Path) -> str:
    """用 mtime + size 當作檔案簽章，快速判斷是否有變動。"""
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def load_manifest(manifest_path: pathlib.Path) -> dict:
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_manifest(manifest_path: pathlib.Path, manifest: dict):
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def find_account_dirs(source_root: pathlib.Path) -> list[pathlib.Path]:
    """找出 LINE Data Master 備份下的所有帳號目錄。"""
    return [d for d in source_root.iterdir()
            if d.is_dir() and not d.name.startswith("_")]


def backup_from_ldm(source_root: pathlib.Path, out_dir: pathlib.Path,
                    limit: int, diff: bool = False) -> tuple[int, int]:
    """將 LINE Data Master 的備份複製到輸出目錄。"""
    accounts = find_account_dirs(source_root)
    if not accounts:
        print(f"[!] 在 {source_root} 找不到帳號目錄")
        return 0, 0

    success = fail = 0
    for acct_dir in accounts:
        acct_id = acct_dir.name
        print(f"\n帳號：{acct_id}")
        out_acct = out_dir / acct_id
        out_acct.mkdir(parents=True, exist_ok=True)

        # 差異備份：載入或建立 manifest
        manifest_path = out_acct / MANIFEST_NAME
        manifest = load_manifest(manifest_path) if diff else {}

        # 複製各類型資料（整個子目錄用 copytree，單層檔案用 copy2）
        SUBDIRS = ["chats", "files", "threads", "notes", "albums", "originals"]
        for subdir in SUBDIRS:
            src = acct_dir / subdir
            if not src.exists():
                continue

            dst_dir = out_acct / subdir

            # 有 limit 時只複製前 N 個頂層項目
            dst_dir.mkdir(exist_ok=True)
            items = sorted(src.rglob("*") if not limit else src.iterdir())
            if limit:
                items = list(items)[:limit]

            copied = skipped = 0
            for item in items:
                if item.is_dir():
                    (dst_dir / item.relative_to(src)).mkdir(parents=True, exist_ok=True)
                    continue
                rel = str(item.relative_to(src))
                dst = dst_dir / item.relative_to(src)
                sig = file_sig(item)

                # 差異備份：簽章相同就跳過
                if diff and manifest.get(rel) == sig and dst.exists():
                    skipped += 1
                    continue

                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dst)
                    manifest[rel] = sig
                    copied += 1
                    success += 1
                except Exception as e:
                    print(f"  [!] 複製失敗 {item.name}: {e}")
                    fail += 1

            skip_msg = f"，跳過 {skipped} 個（未變動）" if diff and skipped else ""
            print(f"  {subdir}/: 複製 {copied} 個{skip_msg}")

        # 複製 metadata 檔
        for fname in ["auto_backup.json", "recovered_unsent.json",
                      "recovered_unsent_v2.json", "file_failed_manifest.json"]:
            src = acct_dir / fname
            if src.exists():
                shutil.copy2(src, out_acct / fname)

        # 儲存 manifest（差異備份用）
        if diff:
            save_manifest(manifest_path, manifest)

    return success, fail


def backup_from_mcp(out_dir: pathlib.Path) -> tuple[int, int]:
    """透過 MCP API 取得最近 30 個聊天的即時資料。"""
    try:
        from mcp_client import MCPClient
    except ImportError:
        print("[!] 找不到 mcp_client.py，跳過 MCP 備份")
        return 0, 0

    client = MCPClient()
    try:
        client.connect()
    except Exception as e:
        print(f"[!] MCP 連線失敗：{e}，跳過 MCP 備份")
        return 0, 0

    chats = client.list_chats()
    if not chats:
        print("[!] MCP 未回傳聊天室")
        return 0, 0

    print(f"MCP 取得 {len(chats)} 個最新聊天室")
    mcp_dir = out_dir / "_mcp_recent"
    mcp_dir.mkdir(exist_ok=True)

    success = fail = 0
    for i, chat in enumerate(chats, 1):
        chat_id = chat.get("id", "")
        name = chat.get("display_name", chat_id)
        try:
            result = client.export_chat(chat_id)
            if isinstance(result, dict) and "error" not in result:
                out_file = mcp_dir / f"{chat_id}.json"
                out_file.write_text(
                    json.dumps({"meta": chat, "data": result}, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                success += 1
                print(f"  [{i}] {name} -> OK")
            else:
                fail += 1
        except Exception as e:
            fail += 1
            print(f"  [{i}] {name} -> [!] {e}")
        time.sleep(0.05)

    return success, fail


def write_summary(out_dir: pathlib.Path, stats: dict):
    """寫備份摘要。"""
    summary = {
        "backup_time": datetime.now().isoformat(),
        "generator": "LINE Backup v0.4.0",
        **stats,
    }
    (out_dir / "backup_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def find_latest_backup(output_root: pathlib.Path) -> pathlib.Path | None:
    """找最新的帳號備份目錄（latest/ 優先，否則取最新時間戳）。"""
    latest = output_root / "latest"
    if latest.exists():
        accts = [d for d in latest.iterdir() if d.is_dir() and not d.name.startswith("_")]
        if accts:
            return accts[0]
    # 找時間戳目錄中最新的
    ts_dirs = sorted(output_root.glob("20*"), reverse=True)
    for d in ts_dirs:
        accts = [a for a in d.iterdir() if a.is_dir() and not a.name.startswith("_")]
        if accts:
            return accts[0]
    return None


def delete_chat(output_root: pathlib.Path):
    """互動式刪除單一聊天室備份。"""
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")

    acct_dir = find_latest_backup(output_root)
    if not acct_dir:
        print("[!] 找不到備份，請先執行備份。")
        return

    chats_dir = acct_dir / "chats"
    if not chats_dir.exists():
        print(f"[!] 找不到 chats 目錄：{chats_dir}")
        return

    # 讀取所有聊天室
    files = sorted(chats_dir.glob("*.json"))
    if not files:
        print("[!] 備份目錄內沒有聊天室檔案。")
        return

    chats = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            count = len(data.get("messages", []))
            chats.append({"file": f, "chat_mid": f.stem, "count": count})
        except Exception:
            chats.append({"file": f, "chat_mid": f.stem, "count": 0})

    # 顯示清單（支援關鍵字過濾）
    print(f"\n備份來源：{acct_dir}")
    print(f"共 {len(chats)} 個聊天室\n")

    keyword = input("輸入關鍵字過濾（直接 Enter 顯示全部）：").strip()
    if keyword:
        filtered = [c for c in chats if keyword.lower() in c["chat_mid"].lower()]
        if not filtered:
            print(f"找不到含「{keyword}」的聊天室。")
            return
    else:
        filtered = chats

    print(f"\n{'編號':<5} {'訊息數':>7}  聊天室 ID")
    print("─" * 50)
    for i, c in enumerate(filtered, 1):
        print(f"{i:<5} {c['count']:>7,}  {c['chat_mid']}")

    # 讓使用者輸入編號
    print()
    try:
        choice = input("輸入要刪除的編號（輸入 0 取消）：").strip()
        idx = int(choice)
    except (ValueError, EOFError):
        print("取消。")
        return

    if idx == 0:
        print("取消。")
        return
    if not (1 <= idx <= len(filtered)):
        print("編號超出範圍。")
        return

    target = filtered[idx - 1]
    print(f"\n將刪除：{target['chat_mid']}（{target['count']:,} 則訊息）")
    print(f"  檔案：{target['file']}")

    # 同時找對應的 HTML 檔（若存在）
    html_file = acct_dir.parent / "html" / "chats" / (target["chat_mid"] + ".html")
    if html_file.exists():
        print(f"  HTML：{html_file}")

    confirm = input("\n確認刪除？輸入 YES 確認，其他取消：").strip()
    if confirm != "YES":
        print("取消。")
        return

    # 執行刪除
    deleted = []
    target["file"].unlink()
    deleted.append(str(target["file"]))

    if html_file.exists():
        html_file.unlink()
        deleted.append(str(html_file))

    # 從 manifest 移除（如存在）
    manifest_path = acct_dir / MANIFEST_NAME
    if manifest_path.exists():
        manifest = load_manifest(manifest_path)
        key = f"chats/{target['chat_mid']}.json"
        if key in manifest:
            del manifest[key]
            save_manifest(manifest_path, manifest)

    print(f"\n已刪除：")
    for d in deleted:
        print(f"  {d}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    args = parse_args()

    print("=" * 50)
    print("LINE 備份工具 v0.4.0")
    print("=" * 50)

    # 刪除模式
    if args.delete:
        delete_chat(pathlib.Path(args.output))
        return

    source_root = pathlib.Path(args.source_dir)
    if not source_root.exists():
        print(f"[!] 找不到 LINE Data Master 備份目錄：{source_root}")
        print("請先開啟 LINE Data Master 並完成至少一次備份。")
        sys.exit(1)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = pathlib.Path(args.output) / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"輸出目錄：{out_dir}\n")

    if args.diff:
        # 差異備份：輸出到固定目錄（不加時間戳），以便 manifest 持續累積
        out_dir = pathlib.Path(args.output) / "latest"
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[差異備份模式] 輸出目錄：{out_dir}")

    start = time.time()

    # 主要：從 LINE Data Master 備份目錄複製
    mode = "差異" if args.diff else "完整"
    print(f"[1] {mode}備份從 LINE Data Master 目錄複製...")
    s1, f1 = backup_from_ldm(source_root, out_dir, args.limit, diff=args.diff)
    print(f"    完成：成功 {s1}，失敗 {f1}")

    # 選用：MCP 即時補充
    s2 = f2 = 0
    if args.mcp:
        print("\n[2] 從 MCP API 補充最新資料...")
        s2, f2 = backup_from_mcp(out_dir)
        print(f"    完成：成功 {s2}，失敗 {f2}")

    elapsed = time.time() - start
    write_summary(out_dir, {
        "ldm_success": s1, "ldm_fail": f1,
        "mcp_success": s2, "mcp_fail": f2,
        "elapsed_sec": round(elapsed, 1),
    })

    print(f"\n{'='*50}")
    print(f"備份完成！耗時 {elapsed:.1f} 秒")
    print(f"位置：{out_dir}")


if __name__ == "__main__":
    main()
