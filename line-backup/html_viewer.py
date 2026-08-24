# v0.2.0 | 2026-05-20
# 變更：使用 name_resolver 四層命名；--serve 模式含重新命名 API

import http.server
import io
import json
import pathlib
import sys
import threading
import webbrowser
from datetime import datetime
from urllib.parse import parse_qs, urlparse

from name_resolver import NameResolver

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

TIER_BADGE = {
    "custom": ("✏️", "#06c755"),
    "group": ("📂", "#3498db"),
    "composite": ("👥", "#9b59b6"),
    "text": ("💬", "#e67e22"),
    "hash": ("🔑", "#999"),
}

INDEX_JS = """
<script>
const SERVE_MODE = {serve_mode};

function renameChat(mid, currentName) {{
  const newName = prompt('重新命名（輸入後儲存到 chat_names.json）：', currentName);
  if (!newName || newName.trim() === currentName) return;
  if (SERVE_MODE) {{
    fetch('/api/rename', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{mid, name: newName.trim()}})
    }}).then(r => r.json()).then(d => {{
      if (d.ok) {{
        const el = document.getElementById('name-' + mid);
        if (el) el.textContent = newName.trim();
        alert('已儲存！下次執行 html_viewer.py 產生的頁面會使用新名稱。');
      }}
    }});
  }} else {{
    alert('提示：在靜態模式下，請直接編輯 chat_names.json，' +
          '再重新執行 html_viewer.py。\\n\\n' +
          '要新增的設定：\\n"' + mid + '": "' + newName.trim() + '"');
  }}
}}
</script>
"""


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
                 resolver: NameResolver, my_mid: str = "") -> None:
    data = json.loads(chat_path.read_text(encoding="utf-8"))
    messages = data.get("messages", [])
    chat_mid = data.get("chat_mid", chat_path.stem)
    title, _ = resolver.resolve_chat(chat_mid, messages)

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
        raw_name = (msg.get("fromName") or "").strip()
        sender_name = resolver.resolve_sender(from_mid, raw_name)
        lines.append(render_bubble(msg, is_me, sender_name))

    count = len(messages)
    lines.append(f'<div class="stats">共 {count:,} 則訊息</div>')
    lines.append("</body></html>")
    out_path.write_text("".join(lines), encoding="utf-8")


def build_index(chats_dir: pathlib.Path, out_dir: pathlib.Path,
                resolver: NameResolver, serve_mode: bool = False) -> pathlib.Path:
    files = sorted(chats_dir.glob("*.json"))
    rows = []
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        mid = data.get("chat_mid", f.stem)
        messages = data.get("messages", [])
        count = len(messages)
        name, tier = resolver.resolve_chat(mid, messages)
        html_name = f.stem + ".html"
        badge_icon, badge_color = TIER_BADGE.get(tier, ("", "#999"))
        safe_name = name.replace('"', '&quot;').replace("'", "&#39;")
        rows.append(
            f'<tr data-mid="{mid}">'
            f'<td><a href="chats/{html_name}">'
            f'<span title="{tier}" style="color:{badge_color};margin-right:4px">{badge_icon}</span>'
            f'<span id="name-{mid}">{name}</span>'
            f'</a></td>'
            f'<td style="text-align:right;color:#888">{count:,}</td>'
            f'<td style="text-align:center">'
            f'<button onclick="renameChat(\'{mid}\',\'{safe_name}\')" '
            f'style="border:none;background:none;cursor:pointer;color:#aaa;font-size:14px" '
            f'title="重新命名">✏️</button>'
            f'</td>'
            f'</tr>'
        )

    serve_hint = (
        '<p style="color:#06c755;font-size:12px;margin-bottom:8px">🟢 Serve 模式：點 ✏️ 可直接重新命名</p>'
        if serve_mode else
        '<p style="color:#888;font-size:12px;margin-bottom:8px">💡 點 ✏️ 查看命名提示；或以 --serve 啟動支援即時重新命名</p>'
    )

    index_html = f"""<!DOCTYPE html>
<html lang="zh-TW"><head><meta charset="utf-8">
<title>LINE 備份</title>
<style>
body{{font-family:-apple-system,'Microsoft JhengHei',sans-serif;padding:20px;background:#f5f5f5}}
h1{{color:#06c755;margin-bottom:8px}}
table{{width:100%;max-width:680px;border-collapse:collapse;background:#fff;
       border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.1)}}
th{{background:#06c755;color:#fff;padding:10px 16px;text-align:left}}
td{{padding:8px 16px;border-bottom:1px solid #f0f0f0}}
a{{color:#222;text-decoration:none}}
a:hover{{color:#06c755}}
button:hover{{color:#06c755 !important}}
input#search{{width:100%;max-width:680px;padding:8px 12px;margin-bottom:12px;
              border:1px solid #ddd;border-radius:6px;font-size:14px}}
</style></head><body>
<h1>LINE 備份閱讀器</h1>
<p style="color:#888;margin-bottom:8px">共 {len(files)} 個聊天室</p>
{serve_hint}
<input id="search" type="text" placeholder="🔍 篩選群組名稱..." oninput="filterRows(this.value)">
<table><tr><th>聊天室</th><th>訊息數</th><th></th></tr>
{"".join(rows)}
</table>
{INDEX_JS.format(serve_mode="true" if serve_mode else "false")}
<script>
function filterRows(q) {{
  q = q.toLowerCase();
  document.querySelectorAll('tr[data-mid]').forEach(tr => {{
    const name = tr.querySelector('[id^="name-"]').textContent.toLowerCase();
    tr.style.display = name.includes(q) ? '' : 'none';
  }});
}}
</script>
</body></html>"""

    idx = out_dir / "index.html"
    idx.write_text(index_html, encoding="utf-8")
    return idx


def generate_all(backup_acct_dir: pathlib.Path, out_dir: pathlib.Path,
                 resolver: NameResolver, my_mid: str = "",
                 serve_mode: bool = False) -> pathlib.Path:
    chats_src = backup_acct_dir / "chats"
    chats_out = out_dir / "chats"
    chats_out.mkdir(parents=True, exist_ok=True)

    files = list(chats_src.glob("*.json"))
    print(f"共 {len(files)} 個聊天室，開始轉換...")
    for i, f in enumerate(files, 1):
        out = chats_out / (f.stem + ".html")
        chat_to_html(f, out, resolver, my_mid)
        if i % 50 == 0:
            print(f"  {i}/{len(files)}...")

    idx = build_index(chats_src, out_dir, resolver, serve_mode)
    print(f"完成！index: {idx}")
    return idx


# ── Serve 模式 ──────────────────────────────────────────────────────────────


class _Handler(http.server.SimpleHTTPRequestHandler):
    resolver: NameResolver = None  # set by serve()
    out_dir: pathlib.Path = None

    def do_POST(self):
        if self.path == "/api/rename":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            mid = (body.get("mid") or "").strip()
            name = (body.get("name") or "").strip()
            if mid and name:
                self.resolver.set_custom(mid, name)
                resp = json.dumps({"ok": True}).encode("utf-8")
            else:
                resp = json.dumps({"ok": False, "error": "missing fields"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        pass  # 靜音


def serve(out_dir: pathlib.Path, resolver: NameResolver, port: int = 5578) -> None:
    _Handler.resolver = resolver
    _Handler.out_dir = out_dir
    server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
    server.socket.settimeout(1)
    print(f"🌐 瀏覽器：http://127.0.0.1:{port}/index.html")
    print("Ctrl+C 停止")
    import os
    os.chdir(out_dir)
    webbrowser.open(f"http://127.0.0.1:{port}/index.html")
    try:
        while True:
            server.handle_request()
    except KeyboardInterrupt:
        print("\n停止")


# ── 主程式 ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description="LINE HTML 閱讀器")
    parser.add_argument("--serve", action="store_true", help="啟動本地伺服器（支援即時重新命名）")
    parser.add_argument("--port", type=int, default=5578)
    parser.add_argument("--regen", action="store_true", help="重新產生所有 HTML（搭配 --serve 使用）")
    args = parser.parse_args()

    backup_root = pathlib.Path("D:/LINE自製備份")
    # 只取時間戳備份（排除 latest diff 目錄），選最新的
    timestamped = sorted(
        [p for p in backup_root.glob("*/47234ddb9e79bc22b3074b31cb876d")
         if p.parent.name.startswith("2")],
        reverse=True,
    )
    if timestamped:
        acct_dir = timestamped[0]
    else:
        # 退路：用桌面備份
        acct_dir = pathlib.Path("D:/LINE桌面備份/47234ddb9e79bc22b3074b31cb876d")
        if not acct_dir.exists():
            print("[!] 找不到備份目錄")
            sys.exit(1)

    # notes 優先用 LINE桌面備份（資料最完整）
    notes_src = pathlib.Path("D:/LINE桌面備份/47234ddb9e79bc22b3074b31cb876d")
    if not notes_src.exists():
        notes_src = acct_dir

    out_dir = pathlib.Path("D:/LINE自製備份/latest") / "html"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"來源：{acct_dir}")
    print(f"Notes 來源：{notes_src}")
    print(f"輸出：{out_dir}")

    resolver = NameResolver(notes_src)
    gn = len(resolver.group_names)
    un = len(resolver.user_names)
    cn = len(resolver.custom_names)
    print(f"名稱庫：群組 {gn}  用戶 {un}  自訂 {cn}")

    if args.serve and not args.regen:
        # serve 模式且不重生：直接確認 index 存在
        idx = out_dir / "index.html"
        if not idx.exists():
            print("找不到 index.html，先重新產生...")
            generate_all(acct_dir, out_dir, resolver, serve_mode=True)
        else:
            # 重建 index 以套用最新名稱（快，只改 index）
            chats_src = acct_dir / "chats"
            build_index(chats_src, out_dir, resolver, serve_mode=True)
    else:
        generate_all(acct_dir, out_dir, resolver, serve_mode=args.serve)

    if args.serve:
        serve(out_dir, resolver, args.port)
    else:
        print("用瀏覽器開啟 index.html 即可瀏覽")
        print("提示：加 --serve 啟動本地伺服器，支援即時重新命名")
