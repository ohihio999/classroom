"""
影音工具本機服務（影片轉 MP3 / 錄音檔合併）

v1.0 2026-07-31 初版：把 C:\\OBS 的 影片轉mp3.bat、錄音黨合併.bat、merge.ps1、convert_to_mp3.py
                 整合成單一本機服務，網頁 UI 內嵌在本檔，port 8767。
                 網頁操作、本機 ffmpeg 處理、檔案不上傳、輸出回原資料夾。

用法：雙擊 start.bat，或 python server.py
"""

import json
import os
import re
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8767
DEFAULT_VIDEO_DIR = r"C:\OBS\影片轉mp3"
DEFAULT_AUDIO_DIR = r"C:\OBS\影片檔合併"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma"}

FFMPEG_FALLBACK = (
    r"C:\Users\admin\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
)

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def find_tool(name: str) -> str:
    """先找 PATH，找不到就用 winget 安裝的絕對路徑。"""
    found = shutil.which(name)
    if found:
        return found
    fallback = Path(FFMPEG_FALLBACK) / f"{name}.exe"
    if fallback.exists():
        return str(fallback)
    return name


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


# ---------------------------------------------------------------- 工作管理

class Job:
    """一次轉檔或合併的工作，進度由網頁輪詢 /api/status 讀取。"""

    def __init__(self, kind: str, items: list, output_dir: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind                      # 'convert' | 'merge'
        self.output_dir = output_dir
        self.cancelled = False
        self.done = False
        self.started_at = time.time()
        self.finished_at = None
        self.message = ""
        self.outputs = []
        self.lock = threading.Lock()
        self.items = [
            {
                "name": Path(p).name,
                "path": p,
                "size": Path(p).stat().st_size if Path(p).exists() else 0,
                "status": "waiting",          # waiting | running | done | failed | cancelled
                "percent": 0.0,
                "duration": 0.0,
                "current": 0.0,
                "error": "",
                "output": "",
            }
            for p in items
        ]

    def snapshot(self) -> dict:
        with self.lock:
            total = len(self.items) or 1
            finished = sum(1 for i in self.items if i["status"] in ("done", "failed", "cancelled"))
            running = sum(i["percent"] for i in self.items if i["status"] == "running") / 100.0
            overall = min((finished + running) / total * 100.0, 100.0)
            elapsed = (self.finished_at or time.time()) - self.started_at
            return {
                "id": self.id,
                "kind": self.kind,
                "done": self.done,
                "cancelled": self.cancelled,
                "message": self.message,
                "overall": round(overall, 1),
                "elapsed": round(elapsed, 1),
                "outputs": list(self.outputs),
                "outputDir": self.output_dir,
                "items": [dict(i) for i in self.items],
            }


JOBS = {}
JOBS_LOCK = threading.Lock()


def probe_duration(path: str) -> float:
    """用 ffprobe 取媒體長度（秒），取不到回 0。"""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, creationflags=NO_WINDOW,
        )
        return float(result.stdout.strip())
    except (ValueError, OSError):
        return 0.0


TIME_RE = re.compile(r"out_time=(\d+):(\d+):([\d.]+)")


def run_ffmpeg(cmd: list, duration: float, on_progress) -> tuple:
    """跑 ffmpeg 並即時回報進度，回傳 (returncode, stderr_text)。"""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_file:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=err_file, text=True,
            encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW,
        )
        for line in process.stdout:
            match = TIME_RE.search(line)
            if match:
                current = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
                percent = min(current / duration * 100.0, 99.9) if duration > 0 else 0.0
                if on_progress(current, percent) is False:      # 回 False 代表要取消
                    process.terminate()
                    try:
                        process.wait(timeout=10)               # 等 ffmpeg 真的放掉輸出檔再回去刪
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    return -1, "已取消"
        process.wait()
        err_file.seek(0)
        return process.returncode, err_file.read().strip()


def remove_file(path) -> None:
    """刪半成品；ffmpeg 剛結束時檔案可能還被鎖住，重試幾次再放棄。"""
    for _ in range(10):
        try:
            Path(path).unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.3)


def worker_convert(job: Job):
    """逐支影片轉成同名 MP3，放回原資料夾。"""
    for item in job.items:
        if job.cancelled:
            item["status"] = "cancelled"
            continue

        source = Path(item["path"])
        target = source.with_suffix(".mp3")
        item["duration"] = probe_duration(item["path"])
        item["status"] = "running"

        def on_progress(current, percent, _item=item):
            _item["current"] = current
            _item["percent"] = round(percent, 1)
            return not job.cancelled

        code, stderr = run_ffmpeg(
            [FFMPEG, "-y", "-nostdin", "-loglevel", "error", "-progress", "pipe:1",
             "-i", str(source), "-vn", "-acodec", "libmp3lame", "-ab", "192k", str(target)],
            item["duration"], on_progress,
        )

        if job.cancelled:
            item["status"] = "cancelled"
            remove_file(target)
        elif code == 0:
            item["status"] = "done"
            item["percent"] = 100.0
            item["output"] = str(target)
            job.outputs.append(str(target))
        else:
            item["status"] = "failed"
            item["error"] = stderr[-500:] or f"ffmpeg exit {code}"

    job.done = True
    job.finished_at = time.time()
    job.message = "已取消" if job.cancelled else "轉檔完成"


def worker_merge(job: Job):
    """把選到的音檔依清單順序 concat 成一個檔（-c copy，不重新編碼）。"""
    total = sum(probe_duration(i["path"]) for i in job.items)
    for item in job.items:
        item["status"] = "running"

    ext = Path(job.items[0]["path"]).suffix
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = Path(job.output_dir) / f"merged_{stamp}{ext}"

    list_path = Path(tempfile.gettempdir()) / f"_merge_{job.id}.txt"
    lines = [f"file '{Path(i['path']).as_posix()}'" for i in job.items]
    list_path.write_text("\n".join(lines), encoding="utf-8")

    def on_progress(current, percent):
        for i in job.items:
            i["current"] = current
            i["percent"] = round(percent, 1)
        return not job.cancelled

    code, stderr = run_ffmpeg(
        [FFMPEG, "-y", "-nostdin", "-loglevel", "error", "-progress", "pipe:1",
         "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(target)],
        total, on_progress,
    )
    list_path.unlink(missing_ok=True)

    if job.cancelled:
        remove_file(target)
        for i in job.items:
            i["status"] = "cancelled"
        job.message = "已取消"
    elif code == 0:
        for i in job.items:
            i["status"] = "done"
            i["percent"] = 100.0
        job.outputs.append(str(target))
        job.message = "合併完成"
    else:
        for i in job.items:
            i["status"] = "failed"
            i["error"] = stderr[-500:] or f"ffmpeg exit {code}"
        job.message = "合併失敗"

    job.done = True
    job.finished_at = time.time()


def start_job(kind: str, paths: list, output_dir: str) -> Job:
    job = Job(kind, paths, output_dir)
    with JOBS_LOCK:
        JOBS[job.id] = job
    worker = worker_convert if kind == "convert" else worker_merge

    def runner():
        """工作緒若炸掉也要把 job 收乾淨，否則網頁會一直停在處理中。"""
        try:
            worker(job)
        except Exception as exc:                              # noqa: BLE001
            for item in job.items:
                if item["status"] in ("waiting", "running"):
                    item["status"] = "failed"
                    item["error"] = str(exc)
            job.message = f"發生錯誤：{exc}"
        finally:
            job.done = True
            if job.finished_at is None:
                job.finished_at = time.time()

    threading.Thread(target=runner, daemon=True).start()
    return job


# ---------------------------------------------------------------- 檔案瀏覽

def list_drives() -> list:
    return [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]


def list_folder(path: str, kind: str) -> dict:
    folder = Path(path)
    if not folder.exists() or not folder.is_dir():
        return {"error": f"找不到資料夾：{path}"}

    wanted = VIDEO_EXTENSIONS if kind == "video" else AUDIO_EXTENSIONS
    dirs, files = [], []
    try:
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            try:
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.suffix.lower() in wanted and not entry.name.startswith("merged_"):
                    files.append({
                        "name": entry.name,
                        "path": str(entry),
                        "size": entry.stat().st_size,
                        "mtime": entry.stat().st_mtime,
                    })
            except OSError:
                continue
    except PermissionError:
        return {"error": f"沒有權限讀取：{path}"}

    parent = str(folder.parent) if folder.parent != folder else ""
    return {"path": str(folder), "parent": parent, "dirs": dirs, "files": files, "drives": list_drives()}


# ---------------------------------------------------------------- HTTP

PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>影音工具 — 影片轉MP3 / 錄音合併</title>
<style>
  :root {
    --bg: #0f1216; --card: #171c23; --line: #262d38; --text: #e6edf3;
    --muted: #8b98a8; --accent: #4d9fff; --ok: #3fb950; --bad: #f85149;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: "Segoe UI", "Microsoft JhengHei", system-ui, sans-serif; }
  header { padding: 20px 24px 0; max-width: 980px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
  main { max-width: 980px; margin: 0 auto; padding: 0 24px 40px; }
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
  .tab { padding: 8px 16px; border-radius: 8px 8px 0 0; background: var(--card);
         border: 1px solid var(--line); border-bottom: none; cursor: pointer; font-size: 14px; }
  .tab.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 16px; margin-bottom: 16px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  input[type=text] { flex: 1; min-width: 240px; padding: 9px 12px; border-radius: 8px;
                     border: 1px solid var(--line); background: #0d1117; color: var(--text);
                     font-family: inherit; font-size: 13px; }
  button { padding: 9px 16px; border-radius: 8px; border: 1px solid var(--line);
           background: #212832; color: var(--text); cursor: pointer; font-size: 13px;
           font-family: inherit; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.go { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  button.stop { background: #3a2226; border-color: #6b2f35; color: #ffb3b3; }
  .crumbs { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0 8px; }
  .chip { padding: 4px 10px; border-radius: 999px; background: #0d1117; border: 1px solid var(--line);
          font-size: 12px; cursor: pointer; color: var(--muted); }
  .chip:hover { color: var(--text); border-color: var(--accent); }
  ul.files { list-style: none; margin: 0; padding: 0; max-height: 320px; overflow-y: auto; }
  ul.files li { display: flex; align-items: center; gap: 10px; padding: 8px 10px;
                border-bottom: 1px solid var(--line); font-size: 13px; }
  ul.files li:last-child { border-bottom: none; }
  ul.files li:hover { background: #1c222b; }
  .fname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fsize { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .empty { color: var(--muted); font-size: 13px; padding: 16px 4px; }
  .bar { height: 8px; border-radius: 999px; background: #0d1117; overflow: hidden; }
  .bar > i { display: block; height: 100%; width: 0; background: var(--accent);
             transition: width .3s ease; }
  .bar.ok > i { background: var(--ok); }
  .bar.bad > i { background: var(--bad); }
  .job-item { margin-bottom: 12px; }
  .job-head { display: flex; justify-content: space-between; gap: 12px; font-size: 13px;
              margin-bottom: 5px; }
  .job-head b { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pct { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .err { color: var(--bad); font-size: 12px; margin-top: 4px; word-break: break-all; }
  .note { color: var(--muted); font-size: 12px; margin-top: 10px; }
  .hide { display: none; }
  .out { font-size: 12px; color: var(--ok); word-break: break-all; margin-top: 3px; }
</style>
</head>
<body>
<header>
  <h1>🎬 影音工具</h1>
  <div class="sub">網頁操作、本機轉檔。檔案不上傳，輸出直接寫回原資料夾。</div>
  <div class="tabs">
    <div class="tab on" data-mode="video">影片轉 MP3</div>
    <div class="tab" data-mode="audio">錄音檔合併</div>
  </div>
</header>
<main>
  <div class="card">
    <div class="row">
      <input type="text" id="path" spellcheck="false">
      <button onclick="go(document.getElementById('path').value)">前往</button>
      <button onclick="load()">重新整理</button>
    </div>
    <div class="crumbs" id="drives"></div>
    <div class="crumbs" id="dirs"></div>
    <div class="row" style="margin:10px 0 6px">
      <button onclick="pick(true)">全選</button>
      <button onclick="pick(false)">全不選</button>
      <span class="fsize" id="count"></span>
    </div>
    <ul class="files" id="files"></ul>
    <div class="row" style="margin-top:14px">
      <button class="go" id="run" onclick="start()">開始</button>
      <button class="stop hide" id="cancel" onclick="cancel()">取消</button>
      <span class="fsize" id="hint"></span>
    </div>
  </div>

  <div class="card hide" id="progress">
    <div class="job-head"><b id="jobTitle">處理中</b><span class="pct" id="jobPct">0%</span></div>
    <div class="bar" id="jobBar"><i></i></div>
    <div id="jobItems" style="margin-top:16px"></div>
    <div class="note" id="jobNote"></div>
  </div>
</main>

<script>
let mode = 'video';
let cur = '';
let files = [];
let checked = new Set();
let jobId = null;
let timer = null;

const DEFAULTS = { video: %DEFAULT_VIDEO%, audio: %DEFAULT_AUDIO% };
const $ = id => document.getElementById(id);
const mb = n => n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' : (n / 1048576).toFixed(1) + ' MB';
const hhmmss = s => [3600, 60, 1].map((u, i) => String(Math.floor(s / u) % (i ? 60 : 999)).padStart(2, '0')).join(':');

document.querySelectorAll('.tab').forEach(tab => tab.onclick = () => {
  if (jobId) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  tab.classList.add('on');
  mode = tab.dataset.mode;
  $('run').textContent = mode === 'video' ? '開始轉檔' : '開始合併';
  go(DEFAULTS[mode]);
});

function go(path) { cur = path; load(); }

async function load() {
  $('path').value = cur;
  const res = await fetch(`/api/list?path=${encodeURIComponent(cur)}&kind=${mode}`);
  const data = await res.json();
  if (data.error) {
    $('files').innerHTML = `<li class="empty">${data.error}</li>`;
    files = []; checked.clear(); render(); return;
  }
  cur = data.path;
  $('path').value = cur;
  $('drives').innerHTML = data.drives.map(d => `<span class="chip" onclick="go('${d.replace(/\\/g, '\\\\')}')">${d}</span>`).join('');
  const up = data.parent ? `<span class="chip" onclick="go(${JSON.stringify(data.parent).replace(/"/g, '&quot;')})">⬆ 上一層</span>` : '';
  $('dirs').innerHTML = up + data.dirs.map(d =>
    `<span class="chip" onclick="go(${JSON.stringify(cur.replace(/\\+$/, '') + '\\' + d).replace(/"/g, '&quot;')})">📁 ${d}</span>`).join('');
  files = data.files;
  checked = new Set(files.map(f => f.path));
  render();
}

function render() {
  $('files').innerHTML = files.length ? files.map((f, i) => `
    <li>
      <input type="checkbox" ${checked.has(f.path) ? 'checked' : ''} onchange="toggle(${i}, this.checked)">
      <span class="fname" title="${f.name}">${f.name}</span>
      <span class="fsize">${mb(f.size)}</span>
    </li>`).join('')
    : `<li class="empty">這個資料夾沒有${mode === 'video' ? '影片' : '音訊'}檔。</li>`;
  const n = checked.size;
  $('count').textContent = `已選 ${n} / ${files.length} 個`;
  $('run').textContent = mode === 'video' ? '開始轉檔' : '開始合併';
  $('run').disabled = !!jobId || n === 0 || (mode === 'audio' && n < 2);
  $('hint').textContent = mode === 'audio' && n === 1 ? '合併至少要選 2 個檔案' : '';
}

function toggle(i, on) { on ? checked.add(files[i].path) : checked.delete(files[i].path); render(); }
function pick(all) { checked = all ? new Set(files.map(f => f.path)) : new Set(); render(); }

async function start() {
  const list = files.filter(f => checked.has(f.path)).map(f => f.path);
  const res = await fetch('/api/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: mode === 'video' ? 'convert' : 'merge', files: list, outputDir: cur })
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  jobId = data.id;
  $('progress').classList.remove('hide');
  $('cancel').classList.remove('hide');
  render();
  timer = setInterval(poll, 500);
  poll();
}

async function cancel() {
  if (!jobId) return;
  await fetch(`/api/cancel?id=${jobId}`, { method: 'POST' });
}

async function poll() {
  if (!jobId) return;
  const data = await (await fetch(`/api/status?id=${jobId}`)).json();
  $('jobTitle').textContent = data.kind === 'convert'
    ? `轉檔中（${data.items.filter(i => i.status === 'done').length}/${data.items.length}）`
    : '合併中';
  $('jobPct').textContent = `${data.overall}%　已用 ${hhmmss(data.elapsed)}`;
  $('jobBar').firstElementChild.style.width = data.overall + '%';

  $('jobItems').innerHTML = data.items.map(i => {
    const cls = i.status === 'failed' ? 'bad' : i.status === 'done' ? 'ok' : '';
    const tail = i.duration > 0 && i.status === 'running'
      ? `${hhmmss(i.current)} / ${hhmmss(i.duration)}　${i.percent}%`
      : ({ waiting: '等待中', done: '完成', failed: '失敗', cancelled: '已取消', running: i.percent + '%' })[i.status];
    return `<div class="job-item">
      <div class="job-head"><b title="${i.name}">${i.name}</b><span class="pct">${tail}</span></div>
      <div class="bar ${cls}"><i style="width:${i.status === 'done' ? 100 : i.percent}%"></i></div>
      ${i.error ? `<div class="err">${i.error}</div>` : ''}
      ${i.output ? `<div class="out">→ ${i.output}</div>` : ''}
    </div>`;
  }).join('');

  if (data.done) {
    clearInterval(timer);
    jobId = null;
    $('cancel').classList.add('hide');
    $('jobTitle').textContent = data.message;
    $('jobNote').innerHTML = data.outputs.length
      ? `輸出位置：${data.outputDir}<br>` + data.outputs.map(o => `✅ ${o}`).join('<br>')
      : '';
    render();
    load();
  }
}

go(DEFAULTS.video);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass                                  # 不要洗畫面

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path in ("/", "/index.html"):
            page = (PAGE
                    .replace("%DEFAULT_VIDEO%", json.dumps(DEFAULT_VIDEO_DIR))
                    .replace("%DEFAULT_AUDIO%", json.dumps(DEFAULT_AUDIO_DIR)))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        elif url.path == "/api/list":
            path = query.get("path", [DEFAULT_VIDEO_DIR])[0]
            kind = query.get("kind", ["video"])[0]
            self._json(list_folder(path, kind))

        elif url.path == "/api/status":
            job = JOBS.get(query.get("id", [""])[0])
            self._json(job.snapshot() if job else {"error": "找不到這個工作"}, 200 if job else 404)

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/api/start":
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            kind = payload.get("kind", "convert")
            paths = [p for p in payload.get("files", []) if Path(p).is_file()]
            output_dir = payload.get("outputDir") or (str(Path(paths[0]).parent) if paths else "")

            if not paths:
                self._json({"error": "沒有選到有效的檔案"}, 400)
                return
            if kind == "merge" and len(paths) < 2:
                self._json({"error": "合併至少需要 2 個檔案"}, 400)
                return

            job = start_job(kind, paths, output_dir)
            self._json({"id": job.id})

        elif url.path == "/api/cancel":
            job = JOBS.get(query.get("id", [""])[0])
            if job:
                job.cancelled = True
                self._json({"ok": True})
            else:
                self._json({"error": "找不到這個工作"}, 404)

        else:
            self._json({"error": "not found"}, 404)


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not Path(FFMPEG).exists() and not shutil.which("ffmpeg"):
        print("[錯誤] 找不到 ffmpeg，請先安裝或加入 PATH。")
        return 1

    if port_in_use(PORT):
        print(f"[提示] 127.0.0.1:{PORT} 已經有服務在跑，直接開瀏覽器。")
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return 0

    for folder in (DEFAULT_VIDEO_DIR, DEFAULT_AUDIO_DIR):
        Path(folder).mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 52)
    print("  🎬 影音工具（影片轉MP3 / 錄音合併）")
    print(f"  網址：http://127.0.0.1:{PORT}/")
    print("  關閉這個視窗就會停止服務。")
    print("=" * 52)
    webbrowser.open(f"http://127.0.0.1:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[結束] 服務已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
