# v0.1.0 | 2026-05-19
# 將 LINE 聊天 JSON 轉成可閱讀的 HTML 網頁

import io
import json
import pathlib
import sys
from datetime import datetime

CONTENT_TYPE = {
    0: "文字", 1: "圖片", 2: "影片", 3: "語音",
    7: "貼圖", 14: "檔案", 15: "位置", 16: "檔案",
}

HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, 'Microsoft JhengHei', sans-serif;
       background: #f0f0f0; color: #222; }}
header {{ background: #06c755; color: #fff; padding: 12px 20px;
          font-size: 18px; font-weight: bold; position: sticky; top: 0; z-index: 10; }}
.day {{ text-align: center; margin: 16px 0 8px;
        font-size: 12px; color: #999; }}
.msg {{ display: flex; margin: 4px 16px; gap: 8px; }}
.msg.me {{ flex-direction: row-reverse; }}
.avatar {{ width: 36px; height: 36px; border-radius: 50%;
           display: flex; align-items: center; justify-content: center;
           font-size: 12px; color: #fff; flex-shrink: 0; font-weight: bold; }}
.bubble {{ max-width: 70%; }}
.sender {{ font-size: 11px; color: #888; margin-bottom: 2px; }}
.me .sender {{ text-align: right; }}
.text {{ background: #fff; border-radius: 12px; padding: 8px 12px;
         font-size: 14px; line-height: 1.5; word-break: break-word;
         white-space: pre-wrap; box-shadow: 0 1px 2px rgba(0,0,0,.08); }}
.me .text {{ background: #c8f7d0; }}
.media {{ font-size: 12px; color: #888; background: #fff;
          border-radius: 8px; padding: 6px 10px; border: 1px solid #ddd; }}
.time {{ font-size: 10px; color: #bbb; align-self: flex-end; margin: 0 4px; }}
.stats {{ padding: 8px 20px; font-size: 12px; color: #888;
          background: #fff; border-top: 1px solid #eee; text-align: center; }}
</style>
</head>
<body>
<header>{title}</header>
"""

AVATAR_COLORS = [
    "#e74c3c","#e67e22","#f1c40f","#2ecc71","#1abc9c",
    "#3498db","#9b59b6","#34495e","#16a085","#c0392b",
]


def mid_to_color(mid: str) -> str:
    h = sum(ord(c) for c in mid) % len(AVATAR_COLORS)
    return AVATAR_COLORS[h]


def mid_to_initial(mid: str) -> str:
    return mid[-2:].upper() if len(mid) >= 2 else "??"


def ts_to_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%H:%M")


def ts_to_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y年%m月%d日 %A")


def render_bubble(msg: dict, is_me: bool, sender_name: str) -> str:
    ct = msg.get("contentType", 0)
    ts = msg.get("createdTime", 0)
    text = msg.get("text", "").strip()

    cls = "msg me" if is_me else "msg"
    color = mid_to_color(msg.get("fromMid", ""))
    initial = mid_to_initial(msg.get("fromMid", ""))
    time_str = ts_to_str(ts)

    if ct == 0 and text:
        content = f'<div class="text">{text}</div>'
    elif ct in (1, 2, 3, 7, 14, 16):
        label = CONTENT_TYPE.get(ct, f"類型{ct}")
        content = f'<div class="media">[{label}]</div>'
    else:
        label = text or CONTENT_TYPE.get(ct, f"類型{ct}")
        content = f'<div class="media">[{label}]</div>'

    sender_div = "" if is_me else f'<div class="sender">{sender_name}</div>'
    time_div = f'<div class="time">{time_str}</div>'

    if is_me:
        return (f'<div class="{cls}">'
                f'{time_div}'
                f'<div class="bubble">{content}</div>'
                f'<div class="avatar" style="background:{color}">{initial}</div>'
                f'</div>\n')
    else:
        return (f'<div class="{cls}">'
                f'<div class="avatar" style="background:{color}">{initial}</div>'
                f'<div class="bubble">{sender_div}{content}</div>'
                f'{time_div}'
                f'</div>\n')


def chat_to_html(chat_path: pathlib.Path, out_path: pathlib.Path,
                 my_mid: str = "", name_map: dict | None = None):
    data = json.loads(chat_path.read_text(encoding="utf-8"))
    messages = data.get("messages", [])
    chat_mid = data.get("chat_mid", chat_path.stem)
    title = (name_map or {}).get(chat_mid, chat_mid[:16] + "...")

    lines = [HTML_HEAD.format(title=title)]
    last_date = ""
    for msg in messages:
        ts = msg.get("createdTime", 0)
        date_str = ts_to_date(ts)
        if date_str != last_date:
            lines.append(f'<div class="day">── {date_str} ──</div>\n')
            last_date = date_str

        from_mid = msg.get("fromMid", "")
        is_me = bool(my_mid and from_mid == my_mid)
        sender_name = (name_map or {}).get(from_mid, from_mid[-8:])
        lines.append(render_bubble(msg, is_me, sender_name))

    count = len(messages)
    lines.append(f'<div class="stats">共 {count:,} 則訊息</div>')
    lines.append("</body></html>")

    out_path.write_text("".join(lines), encoding="utf-8")


def build_index(chats_dir: pathlib.Path, out_dir: pathlib.Path,
                name_map: dict | None = None) -> pathlib.Path:
    """產生 index.html 列出所有聊天室。"""
    files = sorted(chats_dir.glob("*.json"))
    rows = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        mid = data.get("chat_mid", f.stem)
        count = len(data.get("messages", []))
        name = (name_map or {}).get(mid, mid[:20] + "...")
        html_name = f.stem + ".html"
        rows.append(f'<tr><td><a href="chats/{html_name}">{name}</a></td>'
                    f'<td style="text-align:right;color:#888">{count:,}</td></tr>')

    index_html = f"""<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<title>LINE 備份</title>
<style>
body{{font-family:-apple-system,'Microsoft JhengHei',sans-serif;padding:20px;background:#f5f5f5}}
h1{{color:#06c755;margin-bottom:16px}}
table{{width:100%;max-width:600px;border-collapse:collapse;background:#fff;
       border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
th{{background:#06c755;color:#fff;padding:10px 16px;text-align:left}}
td{{padding:8px 16px;border-bottom:1px solid #f0f0f0}}
a{{color:#06c755;text-decoration:none}}
a:hover{{text-decoration:underline}}
</style></head><body>
<h1>LINE 備份閱讀器</h1>
<p style="color:#888;margin-bottom:12px">共 {len(files)} 個聊天室</p>
<table><tr><th>聊天室</th><th>訊息數</th></tr>
{"".join(rows)}
</table></body></html>"""

    idx = out_dir / "index.html"
    idx.write_text(index_html, encoding="utf-8")
    return idx


def generate_all(backup_dir: pathlib.Path, out_dir: pathlib.Path,
                 my_mid: str = "", name_map: dict | None = None):
    chats_src = backup_dir / "chats"
    chats_out = out_dir / "chats"
    chats_out.mkdir(parents=True, exist_ok=True)

    files = list(chats_src.glob("*.json"))
    print(f"共 {len(files)} 個聊天室，開始轉換...")
    for i, f in enumerate(files, 1):
        out = chats_out / (f.stem + ".html")
        chat_to_html(f, out, my_mid, name_map)
        if i % 50 == 0:
            print(f"  {i}/{len(files)}...")

    idx = build_index(chats_src, out_dir, name_map)
    print(f"完成！開啟：{idx}")
    return idx


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # 預設使用最新備份
    backup_root = pathlib.Path("D:/LINE自製備份")
    backups = sorted(backup_root.glob("*/47234ddb9e79bc22b3074b31cb876d"), reverse=True)
    if not backups:
        print("[!] 找不到備份目錄，請先執行 backup.py")
        sys.exit(1)

    acct_dir = backups[0]
    out_dir = acct_dir.parent / "html"
    print(f"來源：{acct_dir}")
    print(f"輸出：{out_dir}")

    generate_all(acct_dir, out_dir)
    print("用瀏覽器開啟 index.html 即可瀏覽")
