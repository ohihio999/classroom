# -*- coding: utf-8 -*-
"""
lexicon_admin.py — 詞庫管理與校正比對後台（掛在 8767 服務底下）

版本：v1.1.0
版更記錄：
  v1.1.0 (2026-08-26) by Claude (Opus 5)
    - 頁面最上面新增「斷句學習」：選「正確版（人工校對）」與「待校正版（機器產出）」
      兩個 srt，比對後學出斷句風格（尺度參數 / 子句起始詞 / 行尾懸空字 / 不可拆詞組），
      勾選後寫進 lexicon/style.json，refine_srt.py 下次轉字幕會讀它。
      學習引擎在 style_learn.py，載不到只是這一區不出現，不影響詞庫與比對。
    - 選檔走本機 tkinter 對話框（瀏覽器的 file input 拿不到完整路徑）
  v1.0.0 (2026-08-23) by Claude (Opus 5)
    - 詞庫管理：分類 CRUD、terms/fixes 增刪改
    - 校正比對：把「我們轉的稿」與「正確的稿」丟進來自動找差異，勾選後入庫
    - 三種比對來源：別人的字幕檔、自己改的、AI 幫挑可疑的

刻意做成獨立模組，server.py 只加幾行路由委派——那支檔案 108KB
且跑著影音轉檔與課程流水線，不該為了新功能去動它的內臟。
"""
import difflib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import style_learn as STYLE
except Exception as _style_err:      # 學習引擎壞掉不該拖垮詞庫管理
    STYLE = None
    print("[warn] 斷句學習模組未載入：%s" % _style_err)

LEXICON_DIR = Path(r"C:\Users\admin\Desktop\classroom\transcribe-audio\lexicon")
LEXICON_JSON = LEXICON_DIR / "lexicon.json"
AGY = Path(r"C:\Users\admin\AppData\Local\agy\bin\agy.exe")

MARKS = "，。、？！,.?!；;：:「」『』…—-（）()"


PICK_CODE = r"""
import sys, tkinter as tk
from tkinter import filedialog
root = tk.Tk(); root.withdraw(); root.attributes("-topmost", True)
picked = filedialog.askopenfilename(
    title=sys.argv[2] if len(sys.argv) > 2 else "選擇字幕檔",
    filetypes=[("字幕與文字檔", "*.srt *.txt *.md"), ("所有檔案", "*.*")])
open(sys.argv[1], "w", encoding="utf-8").write(picked or "")
root.destroy()
"""


def pick_file_dialog(title: str = "選擇字幕檔") -> str:
    """開 Windows 選檔框回傳完整路徑；取消或出錯都回空字串

    跟 server.py 的選圖框同一套路：瀏覽器的 file input 只給檔名不給路徑，
    選檔一定得由本機開；tkinter 丟在 HTTP 執行緒上會出事，所以跑獨立子進程。
    """
    work = Path(tempfile.mkdtemp(prefix="lexpick_"))
    try:
        script, out = work / "pick.py", work / "picked.txt"
        script.write_text(PICK_CODE, encoding="utf-8")
        subprocess.run([sys.executable, str(script), str(out), title],
                       capture_output=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.read_text(encoding="utf-8").strip() if out.exists() else ""
    except Exception:
        return ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ------------------------------------------------------------ 詞庫存取

def load_lexicon() -> dict:
    if not LEXICON_JSON.exists():
        return {"categories": {}}
    try:
        return json.loads(LEXICON_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        return {"categories": {}, "_error": str(e)}


def save_lexicon(data: dict) -> dict:
    LEXICON_DIR.mkdir(parents=True, exist_ok=True)
    if LEXICON_JSON.exists():            # 存檔前先留一份，改壞了還救得回來
        bak = LEXICON_DIR / "lexicon.bak.json"
        bak.write_text(LEXICON_JSON.read_text(encoding="utf-8"), encoding="utf-8")
    data.setdefault("_版本", "1.0.0")
    LEXICON_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    cats = data.get("categories") or {}
    return {"ok": True,
            "categories": len(cats),
            "terms": sum(len(c.get("terms") or []) for c in cats.values()),
            "fixes": sum(len(c.get("fixes") or []) for c in cats.values())}


# ------------------------------------------------------------ 讀取字幕

def _strip(t: str) -> str:
    for ch in MARKS:
        t = t.replace(ch, "")
    return t.replace(" ", "").replace("\t", "")


def srt_to_text(raw: str) -> str:
    """SRT / 純文字都吃；SRT 會剝掉序號與時間軸"""
    raw = raw.replace("\r\n", "\n").lstrip("\ufeff")
    if "-->" not in raw:
        return raw.strip()
    out = []
    for block in re.split(r"\n{2,}", raw.strip()):
        lines = block.split("\n")
        if len(lines) >= 3 and "-->" in lines[1]:
            out.append(" ".join(x.strip() for x in lines[2:]))
        elif len(lines) == 1 and "-->" not in lines[0]:
            out.append(lines[0])
    return "".join(out)


def read_any(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到檔案：{path}")
    return srt_to_text(p.read_text(encoding="utf-8-sig", errors="ignore"))


# ------------------------------------------------------------ 差異比對

# 口語轉寫的常見虛詞差異。這些進了詞庫做全域替換會災難性誤傷，一律排除。
_FUNCTION_WORDS = set("的地得你我他她它了啦喔哦嗎呢吧在再和跟與而就都也很是有個這那")


def _worth_learning(wrong: str, right: str) -> bool:
    """判斷這組差異值不值得進詞庫

    兩份稿子的語氣詞與斷句本來就不同，difflib 會在字母層級產生大量偽差異
    （ski→Sca、Da→docke）。這裡把那些擋掉，只留真正像「辨識錯誤」的。
    """
    if not wrong or not right or wrong == right:
        return False

    same_word = wrong.lower() == right.lower()
    # 純大小寫差異是有價值的（BUG→bug、kv→KV），直接收
    if same_word:
        return len(wrong) >= 2

    # 一邊是另一邊的一部分 -> 幾乎都是對齊錯位的碎片（V→vL、a→ocke）
    lw, lr = wrong.lower(), right.lower()
    if lw in lr or lr in lw:
        return False

    has_en_w = bool(re.search(r"[A-Za-z]", wrong))
    has_en_r = bool(re.search(r"[A-Za-z]", right))

    # 兩邊都是英文：要夠長才算數，短的多半是切碎的字母
    if has_en_w and has_en_r:
        return len(wrong) >= 3 and len(right) >= 3

    # 中文聽成英文（尤拉瑪→Ollama、派→Pi）或反之，是最典型的專名辨識錯誤
    if has_en_w != has_en_r:
        zh_side = wrong if not has_en_w else right
        en_side = right if not has_en_w else wrong
        return len(zh_side) >= 2 and len(en_side) >= 2

    # 以下都是純中文
    if len(wrong) <= 1 or len(right) <= 1:
        return False
    if all(ch in _FUNCTION_WORDS for ch in wrong) or all(ch in _FUNCTION_WORDS for ch in right):
        return False
    # 兩邊都夠長卻一個字都不重疊，多半是對齊錯位
    # （門檻 5：「傻逼傻逼→Subby」這種真案例本來就沒共同字元，不能誤殺）
    if len(wrong) >= 5 and len(right) >= 5 and not (set(wrong) & set(right)):
        return False
    return True

# 高頻中文詞。這些如果被當成「錯字」做全域替換，整份稿子會爛掉。
_COMMON_WORDS = set("好的 能力 變得 這麼 那麼 可以 就是 然後 時候 已經 什麼 怎麼 因為 所以 但是 如果 還有 他們 我們 你們 自己 現在 開始 結束 問題 方式 地方 東西 事情 知道 覺得 看到 想要 需要 可能 應該 一定 真的 其實 不過 而且 或者 只是 有點 非常 比較 直接 當然 反正 找到 出來 起來 下去 上去 過來 回去 一下 一些 很多 這樣".split())
_COMMON_EN = set("growth dance contact core common space cloud master".split())


def risk_of(wrong: str, right: str) -> tuple:
    """評估把這組寫進 fixes 的風險，回傳 (level, reason)

    fixes 是無條件全域替換。錯字本身若是常用詞，每支影片都會被改壞，
    所以寧可標高風險讓人自己判斷，也不要靜靜收下。
    """
    if len(wrong) <= 1:
        return "high", "單字全域替換，一定誤傷"
    if len(right) > len(wrong) * 3 + 2:
        return "high", "一個詞換成一整句"
    if wrong in _COMMON_WORDS:
        return "high", "錯字本身是常用詞"
    if wrong.lower() in _COMMON_EN:
        return "high", "錯字本身是常用英文字"
    if not re.search(r"[A-Za-z0-9]", wrong) and len(wrong) <= 2:
        return "medium", "短中文詞，可能誤傷"
    return "low", ""

def diff_pairs(ours: str, truth: str, max_len: int = 12) -> list:
    """比對兩份稿子，抽出值得進詞庫的「錯→對」候選

    長段落差異多半是句子重組或口誤刪除，不是錯字；單字虛詞差異是口語轉寫
    的自然落差。兩者都過濾掉，只留真正的辨識錯誤。
    """
    a, b = _strip(ours), _strip(truth)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    agg = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace":
            continue
        wrong, right = a[i1:i2], b[j1:j2]
        if len(wrong) > max_len or len(right) > max_len:
            continue
        if not _worth_learning(wrong, right):
            continue
        key = (wrong, right)
        if key in agg:
            agg[key]["count"] += 1
        else:
            lvl, why = risk_of(wrong, right)
            agg[key] = {"wrong": wrong, "right": right, "count": 1,
                        "risk": lvl, "why": why,
                        "context": a[max(0, i1 - 12):i2 + 12]}
    pairs = list(agg.values())
    # 出現越多次、詞越長的排前面（越可能是真正該學的專名）
    pairs.sort(key=lambda x: (-x["count"], -len(x["wrong"])))
    return pairs

# ------------------------------------------------- AI 挑可疑（無對照組時）

AI_PROMPT = """你是繁體中文逐字稿校對員。以下是語音辨識產生的逐字稿，可能有同音錯字或專有名詞聽錯。

請只挑出「明顯是辨識錯誤」的詞，每行一筆，格式嚴格為：
錯誤詞|建議正確詞|理由(10字內)

規則：
- 只挑專有名詞、品牌名、技術術語、明顯同音錯字。
- 不要挑語助詞、口語贅字、講者口誤。
- 不確定的不要挑。最多 30 筆。
- 除了這些行以外不要輸出任何其他文字。

逐字稿：
"""


def ai_suspects(text: str, timeout_s: int = 300) -> list:
    """沒有對照組時，請 agy 讀一遍逐字稿挑可疑詞"""
    tmp_in = Path(tempfile.mkstemp(suffix="_ai_in.txt")[1])
    tmp_out = Path(tempfile.mkstemp(suffix="_ai_out.txt")[1])
    tmp_in.write_text(AI_PROMPT + text[:12000], encoding="utf-8")
    exe = str(AGY) if AGY.exists() else "agy"
    cmd = [exe, "-p",
           f"請讀取檔案 {tmp_in}，完全依照該檔案開頭的指示處理，"
           f"並且只把結果寫入檔案 {tmp_out}（不要加任何說明文字）。",
           "--print-timeout", f"{max(1, timeout_s // 60)}m"]
    try:
        subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=timeout_s)
        raw = tmp_out.read_text(encoding="utf-8", errors="ignore") if tmp_out.exists() else ""
    except subprocess.TimeoutExpired:
        raw = ""
    finally:
        for p in (tmp_in, tmp_out):
            try:
                p.unlink()
            except Exception:
                pass
    out = []
    for line in raw.splitlines():
        parts = [x.strip() for x in line.strip().split("|")]
        if len(parts) >= 2 and parts[0] and parts[1] and parts[0] != parts[1]:
            lvl, why = risk_of(parts[0], parts[1])
            out.append({"wrong": parts[0], "right": parts[1],
                        "count": text.count(parts[0]), "risk": lvl,
                        "why": why, "context": parts[2] if len(parts) > 2 else ""})
    return out


# ------------------------------------------------------------ 寫入詞庫

def add_to_lexicon(category: str, pairs: list, terms: list = None) -> dict:
    data = load_lexicon()
    cats = data.setdefault("categories", {})
    cat = cats.setdefault(category, {"描述": "", "terms": [], "fixes": {}})
    cat.setdefault("terms", [])
    cat.setdefault("fixes", {})
    nf = nt = 0
    skipped = []
    for p in pairs or []:
        w, r = (p.get("wrong") or "").strip(), (p.get("right") or "").strip()
        if not w or not r or w == r:
            continue
        lvl, _why = risk_of(w, r)
        if lvl == "high" and not p.get("force"):
            # 高風險只收進小抄（安全，只是提示模型），不做全域替換
            if r not in cat["terms"]:
                cat["terms"].append(r)
                nt += 1
            skipped.append({"wrong": w, "right": r, "why": _why})
            continue
        if cat["fixes"].get(w) != r:
            cat["fixes"][w] = r
            nf += 1
        if r not in cat["terms"] and re.search(r"[A-Za-z]", r):
            cat["terms"].append(r)      # 英文專名同時進小抄，下次辨識就不會錯
            nt += 1
    for t in terms or []:
        t = (t or "").strip()
        if t and t not in cat["terms"]:
            cat["terms"].append(t)
            nt += 1
    save_lexicon(data)
    return {"ok": True, "category": category, "fixes_added": nf,
            "terms_added": nt, "skipped": skipped}


# ------------------------------------------------------------ 頁面

PAGE = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>詞庫管理與校正比對</title>
<style>
:root{--bg:#0f1115;--panel:#171a21;--line:#272b35;--fg:#e6e8ee;--dim:#9aa1ae;
--acc:#4c8dff;--ok:#3fb950;--warn:#d29922;--bad:#f85149;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.6 "Microsoft JhengHei","Segoe UI",system-ui,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;position:sticky;top:0;z-index:9;
align-items:center;gap:14px;flex-wrap:wrap;background:var(--panel)}
h1{font-size:17px;margin:0}
.tag{font-size:12px;color:var(--dim)}
.tabs{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap;flex-shrink:0}
button{background:#222732;color:var(--fg);border:1px solid var(--line);
border-radius:6px;padding:7px 13px;cursor:pointer;font-family:inherit;font-size:13px}
button:hover{border-color:var(--acc)}
button.on{background:var(--acc);border-color:var(--acc);color:#fff}
button.go{background:var(--ok);border-color:var(--ok);color:#04260f;font-weight:700}
button.del{background:transparent;border-color:#3a2226;color:var(--bad);padding:3px 9px}
main{padding:18px 20px;max-width:1180px}
section{display:none} section.on{display:block}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
input[type=text],textarea,select{background:#10131a;color:var(--fg);
border:1px solid var(--line);border-radius:6px;padding:7px 10px;
font-family:inherit;font-size:13px}
input[type=text]{min-width:180px} textarea{width:100%;min-height:120px;resize:vertical}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12px}
td.w{color:var(--bad)} td.r{color:var(--ok)} td.ctx{color:var(--dim);font-size:12px}
.pill{display:inline-block;background:#222732;border:1px solid var(--line);
border-radius:20px;padding:3px 10px;margin:3px 4px 3px 0;font-size:12px}
.pill b{cursor:pointer;color:var(--bad);margin-left:6px}
.muted{color:var(--dim);font-size:12px}
.msg{padding:8px 12px;border-radius:6px;margin:10px 0;display:none}
.msg.ok{display:block;background:#0d2a14;border:1px solid var(--ok)}
.msg.err{display:block;background:#2a1214;border:1px solid var(--bad)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:820px){.two{grid-template-columns:1fr}}
</style></head><body>
<header>
  <h1>詞庫管理與校正比對</h1>
  <span class="tag" id="stat">載入中…</span>
  <div class="tabs">
    <button id="t1" class="on" onclick="tab(1)">詞庫</button>
    <button id="t2" onclick="tab(2)">校正比對</button>
  </div>
</header>
<main>
<div class="msg" id="msg"></div>

<div class="card" id="learnCard" style="border-color:#2f4a7a">
  <div class="row">
    <b>斷句學習</b>
    <span class="muted">拿同一支影片的兩版字幕比對，學出你的斷句習慣，下次轉字幕直接套用</span>
    <button style="margin-left:auto" onclick="lrStyle()">看目前風格</button>
  </div>
  <div class="row">
    <span style="width:96px;color:var(--ok)">✅ 正確版</span>
    <input type="text" id="lrRight" style="flex:1" spellcheck="false"
           placeholder="人工校對過的字幕（例：Arctime 校對版 .srt）">
    <button onclick="lrPick('lrRight')">選檔</button>
  </div>
  <div class="row">
    <span style="width:96px;color:var(--warn)">⏳ 待校正版</span>
    <input type="text" id="lrWrong" style="flex:1" spellcheck="false"
           placeholder="機器產出的字幕（同一支影片的 .srt）">
    <button onclick="lrPick('lrWrong')">選檔</button>
  </div>
  <div class="row">
    <button class="go" onclick="lrLearn()">開始學習</button>
    <span class="muted" id="lrHint"></span>
  </div>
  <div id="lrOut"></div>
</div>

<section id="s1" class="on">
  <div class="card">
    <div class="row">
      <select id="cat" onchange="renderCat()"></select>
      <input type="text" id="newcat" placeholder="新類別名稱（例：佛教）">
      <button onclick="addCat()">＋ 新增類別</button>
      <button class="go" style="margin-left:auto" onclick="saveAll()">儲存詞庫</button>
    </div>
    <div class="muted" id="catdesc"></div>
  </div>

  <div class="card">
    <h3 style="margin:0 0 8px">小抄詞（terms）<span class="muted">　辨識前餵給 Whisper，讓它先知道會出現哪些名字</span></h3>
    <div class="row">
      <input type="text" id="newterm" placeholder="新增詞，例：What'Sub" onkeydown="if(event.key=='Enter')addTerm()">
      <button onclick="addTerm()">＋ 加入</button>
    </div>
    <div id="terms"></div>
  </div>

  <div class="card">
    <h3 style="margin:0 0 8px">錯字對照（fixes）<span class="muted">　辨識後直接替換，救小抄救不回來的</span></h3>
    <div class="row">
      <input type="text" id="fw" placeholder="錯的（傻逼傻逼）">
      <span>→</span>
      <input type="text" id="fr" placeholder="對的（Subby）">
      <button onclick="addFix()">＋ 加入</button>
    </div>
    <table><thead><tr><th style="width:34%">錯</th><th style="width:34%">對</th><th>操作</th></tr></thead>
    <tbody id="fixes"></tbody></table>
  </div>
</section>

<section id="s2">
  <div class="card">
    <div class="row">
      <b>比對來源</b>
      <button id="m1" class="on" onclick="mode('file')">別人的字幕檔</button>
      <button id="m2" onclick="mode('paste')">我自己改的</button>
      <button id="m3" onclick="mode('ai')">AI 幫我挑可疑的</button>
    </div>
    <div id="src-file">
      <div class="two">
        <div><div class="muted">我們轉出來的稿（srt 或 txt 路徑）</div>
          <input type="text" id="p1" style="width:100%" placeholder="D:\...\xxx_逐字稿.srt"></div>
        <div><div class="muted">正確的稿（作者字幕、付費版⋯）</div>
          <input type="text" id="p2" style="width:100%" placeholder="D:\...\xxx.srt"></div>
      </div>
    </div>
    <div id="src-paste" style="display:none">
      <div class="two">
        <div><div class="muted">我們轉出來的</div><textarea id="t_ours"></textarea></div>
        <div><div class="muted">我改好的</div><textarea id="t_truth"></textarea></div>
      </div>
    </div>
    <div id="src-ai" style="display:none">
      <div class="muted">貼上逐字稿，或填檔案路徑，讓 AI 挑出可疑的專有名詞（會花 1~3 分鐘）</div>
      <input type="text" id="p_ai" style="width:100%;margin:6px 0" placeholder="D:\...\xxx_逐字稿.srt（留空則用下面貼上的內容）">
      <textarea id="t_ai"></textarea>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="go" onclick="runDiff()">開始比對</button>
      <span class="muted" id="diffstat"></span>
    </div>
  </div>

  <div class="card" id="resultcard" style="display:none">
    <div class="row">
      <b>比對結果</b>
      <span class="muted" id="cnt"></span>
      <span style="margin-left:auto"></span>
      <label class="muted">歸到哪一類：</label>
      <select id="tocat"></select>
      <span class="muted" title="通用類會套用到每一支影片">（通用類請謹慎）</span>
      <button class="go" onclick="commit()">把勾選的存進詞庫</button>
    </div>
    <table><thead><tr><th style="width:40px"><input type="checkbox" id="all" onchange="toggleAll()"></th>
      <th>錯</th><th>對</th><th style="width:60px">次數</th>
      <th style="width:130px">風險</th><th>上下文</th></tr></thead>
    <tbody id="rows"></tbody></table>
  </div>
</section>
</main>

<script>
let LEX={categories:{}}, CUR="", PAIRS=[], MODE="file";

function say(t,ok){const m=document.getElementById('msg');
  m.textContent=t;m.className='msg '+(ok?'ok':'err');
  setTimeout(()=>{m.className='msg'},5000);}
// ---------------- 斷句學習
let LR=null;
function lrHint(t){document.getElementById('lrHint').textContent=t||'';}

async function lrPick(id){
  const title=(id=='lrRight')?'選「正確版」字幕（人工校對過的）':'選「待校正版」字幕（機器產出的）';
  lrHint('選檔視窗已開啟，在工作列找一下…');
  try{
    const r=await fetch('/api/lexicon/pick',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});
    const j=await r.json(); lrHint('');
    if(j.path)document.getElementById(id).value=j.path;
  }catch(e){lrHint('');say('選檔失敗：'+e,0);}
}

async function lrLearn(){
  const right=document.getElementById('lrRight').value.trim();
  const wrong=document.getElementById('lrWrong').value.trim();
  if(!right||!wrong){say('兩個檔案都要選：一個正確版、一個待校正版',0);return;}
  lrHint('比對中…'); document.getElementById('lrOut').innerHTML='';
  const r=await fetch('/api/lexicon/style/learn',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify({right,wrong})});
  const j=await r.json();
  if(j.error){lrHint('');say(j.error,0);return;}
  LR=j; lrHint('兩份內容相似度 '+Math.round(j.similarity*100)+'%'); lrRender(j);
}

function lrRender(j){
  j.starters.forEach(x=>x._on=(x.missed>0));
  j.hanging.forEach(x=>x._on=true);
  j.compounds.forEach((x,i)=>x._on=(i<15));
  const pills=(arr,cls,fmt)=>arr.map(x=>
    '<label class="pill"><input type="checkbox" class="'+cls+'" value="'+esc(x.w)+'"'+
    (x._on?' checked':'')+'> '+esc(x.w)+' <span class="muted">'+fmt(x)+'</span></label>').join('');
  const blk=(title,note,arr,cls,fmt)=>'<div style="margin-top:14px"><b>'+title+'</b> '+
    '<span class="muted">'+note+'</span><div style="margin-top:6px">'+
    (arr.length?pills(arr,cls,fmt):'<span class="muted">（這次沒學到新的）</span>')+'</div></div>';
  const r=j.right, w=j.wrong;
  document.getElementById('lrOut').innerHTML=
    '<div class="card" style="background:#10131a;margin-top:12px">'+
    '<div class="muted">正確版 '+r.cues+' 條 · 平均 '+r.chars_avg+' 字 / '+r.secs_avg+' 秒（九成在 '+
    r.chars_p90+' 字 '+r.secs_p90+' 秒以內）　｜　待校正版 '+w.cues+' 條 · 平均 '+
    w.chars_avg+' 字 / '+w.secs_avg+' 秒</div>'+
    '<div class="row" style="margin-top:12px">'+
    '<label><input type="checkbox" id="lrUseSize" checked> 套用尺度</label>'+
    '每條上限 <input type="text" id="lrMaxChars" style="width:64px;min-width:0" value="'+j.suggest.max_chars+'"> 字'+
    '<input type="text" id="lrMaxSecs" style="width:64px;min-width:0" value="'+j.suggest.max_secs+'"> 秒'+
    '<span class="muted">（程式現在寫死 16 字 / 4 秒）</span></div>'+
    blk('這些詞前面要斷開','＝你習慣在這裡起新的一條，機器沒學到的排前面',j.starters,'lr-st',
        x=>x.n+' 次'+(x.missed?'／機器漏了 '+x.missed:'')) +
    blk('這些字後面不能斷','＝機器把它留在行尾，你從不這樣斷',j.hanging,'lr-hg',x=>'機器犯 '+x.n+' 次') +
    blk('這些詞不能拆開','＝機器從中間切斷，你整組保留',j.compounds,'lr-cp',x=>'被切 '+x.n+' 次') +
    '<div class="row" style="margin-top:16px">'+
    '<button class="go" onclick="lrApply()">套用勾選項目</button>'+
    '<span class="muted">寫進 lexicon/style.json，下次跑 refine_srt.py 產字幕就會用它。'+
    '錯字對照請走上面的「校正比對」分頁。</span></div></div>';
}

async function lrApply(){
  if(!LR){say('先按開始學習',0);return;}
  const grab=c=>[...document.querySelectorAll('.'+c+':checked')].map(x=>x.value);
  const body={starters:grab('lr-st'),hanging:grab('lr-hg'),compounds:grab('lr-cp'),
              source:LR.source};
  if(document.getElementById('lrUseSize').checked){
    body.max_chars=document.getElementById('lrMaxChars').value;
    body.max_secs=document.getElementById('lrMaxSecs').value;
  }
  const r=await fetch('/api/lexicon/style/apply',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json();
  if(j.error){say(j.error,0);return;}
  const a=j.added;
  say('已套用：斷點詞 +'+a.clause_starters+'、懸空字 +'+a.trailing_hanging+
      '、不可拆詞 +'+a.compound_suffixes,1);
}

async function lrStyle(){
  const r=await fetch('/api/lexicon/style'); const j=await r.json();
  const n=x=>(j[x]||[]).length;
  document.getElementById('lrOut').innerHTML='<div class="card" style="background:#10131a;margin-top:12px">'+
    '<b>目前的斷句風格</b><div class="muted" style="margin-top:6px">'+
    '每條上限：'+(j.max_chars||'（未設定，用程式預設 16）')+' 字 / '+
    (j.max_secs||'（未設定，用程式預設 4）')+' 秒<br>'+
    '斷點詞 '+n('clause_starters')+' 個 · 懸空字 '+n('trailing_hanging')+' 個 · 不可拆詞 '+
    n('compound_suffixes')+' 個 · 學習紀錄 '+n('learned')+' 次</div>'+
    '<div class="muted" style="margin-top:8px">檔案：transcribe-audio\\lexicon\\style.json</div></div>';
}

function tab(n){for(const i of [1,2]){
  document.getElementById('s'+i).className=(i==n?'section on':'section');
  document.getElementById('s'+i).style.display=(i==n?'block':'none');
  document.getElementById('t'+i).className=(i==n?'on':'');}}
function mode(m){MODE=m;
  document.getElementById('src-file').style.display=(m=='file'?'block':'none');
  document.getElementById('src-paste').style.display=(m=='paste'?'block':'none');
  document.getElementById('src-ai').style.display=(m=='ai'?'block':'none');
  document.getElementById('m1').className=(m=='file'?'on':'');
  document.getElementById('m2').className=(m=='paste'?'on':'');
  document.getElementById('m3').className=(m=='ai'?'on':'');}

async function load(){
  const r=await fetch('/api/lexicon'); LEX=await r.json();
  if(!LEX.categories)LEX.categories={};
  const names=Object.keys(LEX.categories);
  CUR=names[0]||"";
  for(const id of ['cat','tocat']){
    const s=document.getElementById(id); s.innerHTML='';
    names.forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n;s.appendChild(o);});
  }
  // 入庫目標預設避開「通用」——那一類會套用到每支影片
  const tc=document.getElementById('tocat');
  const nonGeneral=names.find(n=>n!=='通用');
  if(nonGeneral)tc.value=nonGeneral;
  renderCat(); stat();
}
function stat(){
  let t=0,f=0; for(const k in LEX.categories){
    t+=(LEX.categories[k].terms||[]).length; f+=Object.keys(LEX.categories[k].fixes||{}).length;}
  document.getElementById('stat').textContent=
    Object.keys(LEX.categories).length+' 類 ・ 小抄 '+t+' 詞 ・ 錯字對照 '+f+' 條';
}
function renderCat(){
  CUR=document.getElementById('cat').value||CUR;
  const c=LEX.categories[CUR]||{terms:[],fixes:{}};
  document.getElementById('catdesc').textContent=c['描述']||'';
  const box=document.getElementById('terms'); box.innerHTML='';
  (c.terms||[]).forEach((t,i)=>{const s=document.createElement('span');s.className='pill';
    s.innerHTML=esc(t)+'<b onclick="delTerm('+i+')">×</b>';box.appendChild(s);});
  if(!(c.terms||[]).length)box.innerHTML='<span class="muted">（還沒有詞）</span>';
  const tb=document.getElementById('fixes'); tb.innerHTML='';
  const f=c.fixes||{};
  Object.keys(f).forEach(k=>{const tr=document.createElement('tr');
    tr.innerHTML='<td class="w">'+esc(k)+'</td><td class="r">'+esc(f[k])+
      '</td><td><button class="del" onclick="delFix('+JSON.stringify(k)+')">刪除</button></td>';
    tb.appendChild(tr);});
  if(!Object.keys(f).length)tb.innerHTML='<tr><td colspan="3" class="muted">（還沒有對照）</td></tr>';
  stat();
}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function addCat(){const n=document.getElementById('newcat').value.trim(); if(!n)return;
  if(LEX.categories[n]){say('這個類別已經有了',false);return;}
  LEX.categories[n]={'描述':'','terms':[],'fixes':{}};
  document.getElementById('newcat').value='';
  const names=Object.keys(LEX.categories);
  for(const id of ['cat','tocat']){const s=document.getElementById(id);s.innerHTML='';
    names.forEach(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;s.appendChild(o);});}
  document.getElementById('cat').value=n; renderCat();}
function addTerm(){const v=document.getElementById('newterm').value.trim(); if(!v)return;
  const c=LEX.categories[CUR]; c.terms=c.terms||[];
  if(!c.terms.includes(v))c.terms.push(v);
  document.getElementById('newterm').value=''; renderCat();}
function delTerm(i){LEX.categories[CUR].terms.splice(i,1); renderCat();}
function addFix(){const w=document.getElementById('fw').value.trim(),
  r=document.getElementById('fr').value.trim(); if(!w||!r)return;
  LEX.categories[CUR].fixes=LEX.categories[CUR].fixes||{};
  LEX.categories[CUR].fixes[w]=r;
  document.getElementById('fw').value='';document.getElementById('fr').value='';renderCat();}
function delFix(k){delete LEX.categories[CUR].fixes[k]; renderCat();}
async function saveAll(){
  const r=await fetch('/api/lexicon/save',{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(LEX)});
  const j=await r.json();
  if(j.ok)say('已儲存：'+j.categories+' 類 / 小抄 '+j.terms+' 詞 / 對照 '+j.fixes+' 條',true);
  else say('儲存失敗：'+(j.error||'未知'),false);}

async function runDiff(){
  const btn=event.target; btn.disabled=true;
  document.getElementById('diffstat').textContent=(MODE=='ai'?'AI 分析中，請等 1~3 分鐘…':'比對中…');
  let body={mode:MODE};
  if(MODE=='file'){body.ourPath=document.getElementById('p1').value.trim();
                   body.truthPath=document.getElementById('p2').value.trim();}
  else if(MODE=='paste'){body.ours=document.getElementById('t_ours').value;
                         body.truth=document.getElementById('t_truth').value;}
  else{body.path=document.getElementById('p_ai').value.trim();
       body.text=document.getElementById('t_ai').value;}
  try{
    const r=await fetch('/api/lexicon/diff',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json();
    if(j.error){say(j.error,false);document.getElementById('diffstat').textContent='';btn.disabled=false;return;}
    PAIRS=j.pairs||[]; renderRows();
    document.getElementById('diffstat').textContent='找到 '+PAIRS.length+' 筆候選';
  }catch(e){say('比對失敗：'+e,false);}
  btn.disabled=false;
}
function renderRows(){
  document.getElementById('resultcard').style.display=PAIRS.length?'block':'none';
  document.getElementById('cnt').textContent='共 '+PAIRS.length+' 筆，勾選要進詞庫的';
  const tb=document.getElementById('rows'); tb.innerHTML='';
  let nlow=0;
  PAIRS.forEach((p,i)=>{const tr=document.createElement('tr');
    const rk=p.risk||'low'; if(rk=='low')nlow++;
    const badge=rk=='high'?'<span style="color:#f85149">⚠ 高</span>':
                (rk=='medium'?'<span style="color:#d29922">△ 中</span>':
                 '<span style="color:#3fb950">低</span>');
    tr.innerHTML='<td><input type="checkbox" class="ck" data-i="'+i+'" data-risk="'+rk+'"'+
      (rk=='low'?' checked':'')+'></td>'+
      '<td class="w">'+esc(p.wrong)+'</td><td class="r">'+esc(p.right)+'</td>'+
      '<td>'+(p.count||0)+'</td>'+
      '<td>'+badge+'<div class="muted">'+esc(p.why||'')+'</div></td>'+
      '<td class="ctx">'+esc(p.context||'')+'</td>';
    tb.appendChild(tr);});
  document.getElementById('cnt').textContent='共 '+PAIRS.length+' 筆，已自動勾選 '+nlow+
    ' 筆低風險；高風險那幾筆的「錯字」本身是常用詞，勾了會讓每支影片都被改壞';
}
function toggleAll(){const on=document.getElementById('all').checked;
  let risky=0;
  document.querySelectorAll('.ck').forEach(c=>{
    if(on&&c.dataset.risk=='high'){risky++;return;}   // 全選不碰高風險
    c.checked=on;});
  if(on&&risky)say('已跳過 '+risky+' 筆高風險，要用請個別勾選',true);}
async function commit(){
  const picked=[]; document.querySelectorAll('.ck').forEach(c=>{if(c.checked)picked.push(PAIRS[+c.dataset.i]);});
  if(!picked.length){say('沒有勾選任何一筆',false);return;}
  const cat=document.getElementById('tocat').value;
  const r=await fetch('/api/lexicon/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({category:cat,pairs:picked})});
  const j=await r.json();
  if(j.ok){let m='已存進「'+j.category+'」：對照 +'+j.fixes_added+'、小抄 +'+j.terms_added;
           if((j.skipped||[]).length)m+='；'+j.skipped.length+' 筆高風險只收進小抄、不做替換';
           say(m,true); await load();}
  else say('存入失敗：'+(j.error||'未知'),false);
}
load();
</script></body></html>
"""


# ------------------------------------------------------------ 路由入口

def handle(method: str, path: str, body: bytes = b"") -> tuple:
    """回傳 (status, content_type, payload_bytes)；不屬於本模組的路徑回傳 None"""
    if method == "GET" and path in ("/lexicon", "/lexicon/"):
        return 200, "text/html; charset=utf-8", PAGE.encode("utf-8")

    if method == "GET" and path == "/api/lexicon":
        return _json(load_lexicon())

    if method == "POST" and path == "/api/lexicon/save":
        try:
            data = json.loads(body or b"{}")
            if not isinstance(data, dict) or "categories" not in data:
                raise ValueError("格式不對：需要 categories")
            return _json(save_lexicon(data))
        except Exception as e:
            return _json({"error": str(e)}, 400)

    if method == "POST" and path == "/api/lexicon/diff":
        try:
            p = json.loads(body or b"{}")
            mode = p.get("mode", "file")
            if mode == "ai":
                text = p.get("text") or ""
                if p.get("path"):
                    text = read_any(p["path"])
                if not text.strip():
                    raise ValueError("沒有可分析的內容")
                return _json({"pairs": ai_suspects(text), "mode": "ai"})
            if mode == "file":
                ours = read_any(p.get("ourPath", ""))
                truth = read_any(p.get("truthPath", ""))
            else:
                ours, truth = srt_to_text(p.get("ours") or ""), srt_to_text(p.get("truth") or "")
            if not ours.strip() or not truth.strip():
                raise ValueError("兩邊都要有內容才能比對")
            return _json({"pairs": diff_pairs(ours, truth), "mode": mode})
        except Exception as e:
            return _json({"error": str(e)}, 400)

    if method == "POST" and path == "/api/lexicon/pick":
        try:
            title = (json.loads(body or b"{}").get("title") or "選擇字幕檔")
        except Exception:
            title = "選擇字幕檔"
        return _json({"path": pick_file_dialog(title)})

    if method == "GET" and path == "/api/lexicon/style":
        if STYLE is None:
            return _json({"error": "斷句學習模組未載入"}, 500)
        return _json(STYLE.load_style())

    if method == "POST" and path == "/api/lexicon/style/learn":
        if STYLE is None:
            return _json({"error": "斷句學習模組未載入"}, 500)
        try:
            p = json.loads(body or b"{}")
            right, wrong = (p.get("right") or "").strip(), (p.get("wrong") or "").strip()
            if not right or not wrong:
                raise ValueError("正確版與待校正版都要指定")
            return _json(STYLE.learn(right, wrong))
        except Exception as e:
            return _json({"error": str(e)}, 400)

    if method == "POST" and path == "/api/lexicon/style/apply":
        if STYLE is None:
            return _json({"error": "斷句學習模組未載入"}, 500)
        try:
            return _json(STYLE.apply_style(json.loads(body or b"{}")))
        except Exception as e:
            return _json({"error": str(e)}, 400)

    if method == "POST" and path == "/api/lexicon/add":
        try:
            p = json.loads(body or b"{}")
            cat = (p.get("category") or "").strip()
            if not cat:
                raise ValueError("要指定類別")
            return _json(add_to_lexicon(cat, p.get("pairs") or [], p.get("terms") or []))
        except Exception as e:
            return _json({"error": str(e)}, 400)

    return None


def _json(obj, status: int = 200) -> tuple:
    return status, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode("utf-8")
