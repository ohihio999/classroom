# -*- coding: utf-8 -*-
"""
refine_srt.py — SRT 精修後處理器（階段 4 LLM 校對 + 階段 2 語意聚合）

版本：v1.0.0
版更記錄：
  v1.0.0 (2026-08-22) by Claude (Opus 5)
    - 階段 4：LLM 逐 cue 校對（補標點 + 修專有名詞），以「行數與編號」為硬約束，
      LLM 完全不碰時間軸；行數/編號對不上就整批退回重試，最後仍失敗則保留原文。
    - 階段 2：依標點聚合成長句，時間間隔與字數僅作輔助條件
      （順序刻意放在補標點之後——有了標點，句界是確定的，不必靠 0.8 秒門檻猜）。
    - 可單獨對既有 SRT 執行，不必重跑 Whisper。

用法：
  python refine_srt.py "某課程.srt"                      # 階段 4 + 階段 2，輸出 _精修.srt
  python refine_srt.py "某課程.srt" --llm none           # 只做階段 2 聚合（零成本、零 API）
  python refine_srt.py "某課程.srt" --range 55:65        # 只處理 55~65 分鐘（驗證用）
  python refine_srt.py "某課程.srt" --vocab chat.txt     # 給 LLM 一份術語表

背景：本腳本實作 Antigravity 架構提案的階段 2 與階段 4，順序依 Claude 評審意見對調。
      提案與評審全文見：
      D:\\本機MD檔\\30_研究\\課程逐字稿整理\\
      20260822_逐字稿生成流水線優化方案_Antigravity架構提案.md
"""
import argparse
import difflib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import tempfile
from pathlib import Path

AGY = r"C:\Users\admin\AppData\Local\agy\bin\agy.exe"

# v1.1.0: 分類詞庫（fixes 錯字替換）
sys.path.insert(0, str(Path(__file__).parent / 'lexicon'))
try:
    import lexicon as LX
except Exception:
    LX = None

SENTENCE_END = "。！？!?"
PAUSE_MARKS = "，、,;；:："

# 階段 2 聚合參數
MAX_CHARS = 45          # 一條字幕的字數上限
MAX_SECONDS = 8.0       # 一條字幕的時長上限
MAX_GAP = 0.8           # 超過這個間隔就一定斷開（秒）
MIN_CHARS_TO_CLOSE = 12 # 遇句號時，累積至少這麼多字才封閉（避免「好。」自成一條）

# 階段 4 批次參數
DEFAULT_BATCH = 120     # 每批送進 LLM 的 cue 數
MAX_RETRY = 2
DEFAULT_WORKERS = 4     # 同時派出的 agy 數（批次彼此獨立，可安全並行）
NL = chr(10)            # 避免字串轉義問題，換行一律用這個


# ---------------------------------------------------------------- SRT 讀寫

def t2s(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.replace(".", ",").split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def s2t(x: float) -> str:
    if x < 0:
        x = 0
    ms = int(round(x * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    cues = []
    for block in re.split(r"\n{2,}", raw.strip()):
        lines = block.split("\n")
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        a, b = lines[1].split("-->")
        text = " ".join(x.strip() for x in lines[2:]).strip()
        if not text:
            continue
        cues.append({"start": t2s(a.strip()), "end": t2s(b.strip()), "text": text})
    return cues


def build_srt(cues: list[dict]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{s2t(c['start'])} --> {s2t(c['end'])}\n{c['text']}\n")
    return "\n".join(out)


# ------------------------------------------------- 階段 4：LLM 逐 cue 校對

PROMPT_HEAD = """你是繁體中文逐字稿校對員。以下每一行的格式是「編號|內容」。

【唯一任務】
1. 為內容補上繁體中文標點（，。？！、）。
2. 修正同音錯字與技術專有名詞的拼寫與大小寫。

【硬性規則】
- 輸出行數必須與輸入完全相同，編號必須原樣保留、順序不變。
- 每行只輸出「編號|校對後內容」，不要有任何說明、標題、程式碼框或空行。
- 嚴禁合併行、拆分行、刪除行、調換行序。
- 嚴禁摘要、改寫、潤飾、刪減語助詞（呃、啊、好、那、就）。
- 內容為空或無法判讀時，原樣輸出該行。
"""


def _call_agy(prompt_text: str, timeout_s: int) -> str:
    """把長文寫成檔案讓 agy 自己讀，避免 stdin 管線卡住"""
    tmp_in = Path(tempfile.mkstemp(suffix="_in.txt")[1])
    tmp_out = Path(tempfile.mkstemp(suffix="_out.txt")[1])
    tmp_in.write_text(prompt_text, encoding="utf-8")
    exe = AGY if Path(AGY).exists() else "agy"
    cmd = [exe, "-p",
           f"請讀取檔案 {tmp_in}，完全依照該檔案開頭的指示處理，"
           f"並且只把處理後的結果寫入檔案 {tmp_out}（不要在結果檔裡加任何說明文字）。",
           "--print-timeout", f"{max(1, timeout_s // 60)}m"]
    try:
        subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=timeout_s)
        return tmp_out.read_text(encoding="utf-8", errors="ignore") if tmp_out.exists() else ""
    except subprocess.TimeoutExpired:
        return ""
    finally:
        for p in (tmp_in, tmp_out):
            try:
                p.unlink()
            except Exception:
                pass


def _parse_numbered(raw: str, expect_ids: list[int]) -> dict[int, str] | None:
    """解析「編號|內容」；編號集合必須與預期完全一致，否則視為失敗"""
    got = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        head, _, body = line.partition("|")
        head = head.strip().lstrip("#").strip()
        if not head.isdigit():
            continue
        got[int(head)] = body.strip()
    if set(got.keys()) != set(expect_ids):
        return None
    return got


def stage4_proofread(cues: list[dict], vocab: str, batch_size: int,
                     backend: str, workers: int = DEFAULT_WORKERS) -> tuple[list[dict], dict]:
    """並行派多個 agy 處理各批次；每批以行數與編號為硬約束，失敗則保留原文"""
    if backend == "none":
        return cues, {"batches": 0, "ok": 0, "failed": 0, "skipped": True}

    vocab_line = f"{NL}【本場專有名詞參考】{NL}{vocab}{NL}" if vocab else ""
    jobs = []
    for start in range(0, len(cues), batch_size):
        chunk = cues[start:start + batch_size]
        ids = list(range(start + 1, start + 1 + len(chunk)))
        jobs.append((len(jobs) + 1, ids, chunk))

    stats = {"batches": len(jobs), "ok": 0, "failed": 0, "skipped": False}
    lock = threading.Lock()

    def work(job):
        n, ids, chunk = job
        body = NL.join(f"{i}|{c['text']}" for i, c in zip(ids, chunk))
        prompt = PROMPT_HEAD + vocab_line + NL + "【待校對內容】" + NL + body
        result = None
        for attempt in range(MAX_RETRY + 1):
            result = _parse_numbered(_call_agy(prompt, timeout_s=300), ids)
            if result is not None:
                break
        with lock:
            if result is None:
                stats["failed"] += 1
                print(f"    批次 {n}/{len(jobs)} 校對失敗，保留原文（{len(chunk)} 條）")
            else:
                for i, c in zip(ids, chunk):
                    new = result.get(i, "").strip()
                    if new:
                        c["text"] = new
                stats["ok"] += 1
                done = stats["ok"] + stats["failed"]
                print(f"    批次 {n}/{len(jobs)} 完成（{len(chunk)} 條）[累計 {done}/{len(jobs)}]")

    print(f"    派出 {workers} 個 agy 並行處理 {len(jobs)} 批")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, jobs))
    return cues, stats

# ------------------------------------------------------------ 共用小工具

def _norm_cmp(t: str) -> str:
    """比對用的正規化：去掉標點與空白"""
    return re.sub(r"[，。、？！,.?!\s]", "", t)

def _len_zh(s: str) -> int:
    return len(re.sub(r"[，。、？！,.?!\s]", "", s))


# --------------------------------------------- 階段 0：文字層幻覺清理

# 既有 SRT 沒有 Whisper 的 no_speech_prob 等欄位，只能從文字層下手。
# 新轉錄的檔案在 transcribe.py v2.0.0 已於來源端過濾，這裡是為舊檔補課。
HALLUCINATION_RES = [
    re.compile(r"請不吝(點贊|點讚|点赞)"),
    re.compile(r"(明鏡|明镜)與(點點|点点)"),
    re.compile(r"(優優|优优)獨播劇場|YoYo Television Series", re.I),
    re.compile(r"字幕(由|製作).{0,12}(提供|製作|字幕組)"),
    re.compile(r"(訂閱|订阅).{0,6}(轉發|转发).{0,6}(打賞|打赏)"),
]


def _dedup_repeat(t: str) -> str:
    """整句重複兩次以上的解碼迴圈，縮回一次"""
    n = len(t)
    if n < 8:
        return t
    if n % 2 == 0 and t[:n // 2] == t[n // 2:]:
        return t[:n // 2]
    for size in range(2, n // 2 + 1):
        if n % size == 0 and t[:size] * (n // size) == t:
            return t[:size]
    return t


def stage0_clean(cues: list[dict]) -> tuple[list[dict], dict]:
    st = {"watermark": 0, "dedup": 0, "adjacent": 0}
    out = []
    for c in cues:
        t = c["text"].strip()
        if any(rx.search(t) for rx in HALLUCINATION_RES):
            st["watermark"] += 1
            continue
        fixed = _dedup_repeat(t)
        if fixed != t:
            st["dedup"] += 1
            t = fixed
        if out and _len_zh(t) >= 8 and _norm_cmp(t) == _norm_cmp(out[-1]["text"]):
            st["adjacent"] += 1
            continue
        c["text"] = t
        out.append(c)
    return out, st

# --------------------------------------------------- 階段 2：依標點聚合

def stage2_aggregate(cues: list[dict]) -> list[dict]:
    """依標點聚合成長句；時間間隔與字數只當輔助條件

    順序上這一步在補標點之後，所以句界由標點決定，
    0.8 秒門檻只用來處理「標點沒補到」與「中間有長停頓」的情況。
    """
    out: list[dict] = []
    cur = None
    for c in cues:
        if cur is None:
            cur = dict(c)
            continue

        gap = c["start"] - cur["end"]
        merged_len = _len_zh(cur["text"]) + _len_zh(c["text"])
        merged_dur = c["end"] - cur["start"]
        ends_sentence = cur["text"].rstrip()[-1:] in SENTENCE_END

        close = False
        if gap >= MAX_GAP:
            close = True                                   # 停頓夠久，一定斷
        elif ends_sentence and _len_zh(cur["text"]) >= MIN_CHARS_TO_CLOSE:
            close = True                                   # 句末標點且長度足夠
        elif merged_len > MAX_CHARS or merged_dur > MAX_SECONDS:
            close = True                                   # 硬上限

        if close:
            out.append(cur)
            cur = dict(c)
        else:
            sep = "" if cur["text"].rstrip()[-1:] in SENTENCE_END + PAUSE_MARKS else ""
            cur["text"] = (cur["text"].rstrip() + sep + c["text"].lstrip()).strip()
            cur["end"] = c["end"]
    if cur:
        out.append(cur)
    return out


# ------------------------------------------- 字幕模式：用 word-level 切條

# 字幕切分參數（對齊標準影視與 YouTube 繁體字幕風格：平均 9~14 字 / 1.8~3.5 秒）
# v1.1.0 (2026-08-23): 從 12 字調至 16 字，加入數字+量詞黏著保護與行尾懸空字避讓，消除 1600 與 萬 斷裂
SUB_MAX_CHARS = 16      # 一條字幕的字數上限
SUB_MAX_SECS  = 4.0     # 一條字幕的時長上限
SUB_GAP_BREAK = 1.2     # 保留給純 word-level 切法的停頓門檻

STICKY_UNITS = set("萬億千萬百個歲分秒天年月日元塊倍點KMGBkmbg")
TRAILING_HANGING = set("從在和跟與被把將向對比及或那是的了著過")

COMPOUND_SUFFIXES = [
    "創辦人", "工程師", "設計師", "經理人", "投資人", "合夥人", "主持人", "製作人", "負責人", "開發者",
    "創作者", "上班族", "英文庫", "單字庫", "刷刷庫", "軟體業", "金融業", "教育榜", "基本上", "實際上",
    "直覺上", "市場上", "技術上", "商業上", "短時間", "長時間", "高難度", "低成本", "落點分析"
]

CLAUSE_STARTERS = [
    # 疑問與反詰
    "到底", "究竟", "難道", "為什麼", "怎麼樣", "怎麼", "是不是", "能不能", "會不會", "可不可以", "要不要",
    # 連詞與轉折
    "因為", "所以", "但是", "可是", "不過", "而且", "然後", "如果", "只要", "只有", "雖然", "儘管", "不管", "無論", "既然",
    # 副詞與語氣
    "其實", "畢竟", "反正", "總之", "突然", "立刻", "馬上", "順便", "大概", "甚至", "千萬", "一定"
]


def _strip_marks(t: str) -> str:
    """去標點與空白，用於文字對齊比對"""
    t = re.sub("[，。、？！,.?!；;：:]", "", t)
    return t.replace(" ", "").replace(chr(9), "").replace(chr(10), "")


def _align_to_words(seg_text: str, words: list):
    """把 segment 文字的每個字元，對應到 word-level 的時間

    words 與 segments 是 Whisper 各自產生的，實測相似度 99.8%（會差幾個字），
    所以用 difflib 對齊而不是假設完全一致。
    回傳 list[(start, end)]，長度等於去標點後的 seg_text。
    """
    wchars, wtimes = [], []
    for w in words:
        for ch in _strip_marks(w["word"]):
            wchars.append(ch)
            wtimes.append((w["start"], w["end"]))
    stxt = _strip_marks(seg_text)
    if not wchars or not stxt:
        return []
    sm = difflib.SequenceMatcher(None, stxt, "".join(wchars), autojunk=False)
    mapping = [None] * len(stxt)
    for i1, j1, n in sm.get_matching_blocks():
        for k in range(n):
            if i1 + k < len(mapping):
                mapping[i1 + k] = j1 + k
    # 沒對到的字元（兩邊不一致處）用前後最近的對應值補
    last = None
    for i in range(len(mapping)):
        if mapping[i] is None:
            mapping[i] = last
        else:
            last = mapping[i]
    nxt = None
    for i in range(len(mapping) - 1, -1, -1):
        if mapping[i] is None:
            mapping[i] = nxt
        else:
            nxt = mapping[i]
    return [wtimes[min(m, len(wtimes) - 1)] if m is not None else wtimes[0] for m in mapping]


def _cut_points(text: str, max_chars: int):
    """決定切點：優先句末標點，其次逗號頓號，最後才硬切

    回傳字元索引清單（相對於去標點後的文字），代表每一段的結束位置。
    """
    cuts, buf, pos = [], 0, 0
    for ch in text:
        if ch in "，。、？！,.?!；;：:":
            if ch in SENTENCE_END or buf >= max_chars * 0.5:
                cuts.append(pos)
                buf = 0
            continue
        pos += 1
        buf += 1
        if buf >= max_chars:
            cuts.append(pos)
            buf = 0
    if not cuts or cuts[-1] < pos:
        cuts.append(pos)
    return cuts


def _split_by_gap(a: int, b: int, times: list, max_chars: int) -> list:
    """把 [a,b) 切成每片約 max_chars 字，切點取語音停頓最大處

    中文沒有空格，數到第 N 個字就砍會切出「兩個箭」+「頭」。
    講話的停頓幾乎都落在詞與詞之間，拿停頓當切點比數字數安全得多。

    用貪婪往前推（不是遞迴對半切——那會越切越碎）：每次目標 max_chars 字，
    在 0.6~1.4 倍的窗內挑停頓最久的位置下刀。
    """
    out, cur = [], a
    while b - cur > max_chars:
        lo = cur + max(2, int(max_chars * 0.6))
        hi = min(b - 2, cur + int(max_chars * 1.4))
        if hi <= lo:
            out.append((cur, min(cur + max_chars, b)))
            cur = min(cur + max_chars, b)
            continue
        best_i, best_gap = lo, -1.0
        for i in range(lo, hi + 1):
            gap = times[i][0] - times[i - 1][1]
            if gap > best_gap:
                best_gap, best_i = gap, i
        out.append((cur, best_i))
        cur = best_i
    if cur < b:
        out.append((cur, b))
    return out

def _nudge_cut(plain: str, cut: int, window: int = 5) -> int:
    """智慧調整切點：引導詞吸附、複合詞保護、防英文斷裂、數字量詞黏著、懸空字避讓

    v1.2.0 (2026-08-23) 升級：
    1. 引導詞/副詞吸附（到底、為什麼、是不是、因為）：切點優先吸附至引導詞正前方
    2. 複合名詞保護（創辦人、工程師、上班族）：禁止將 人/師/者/庫/業 撕裂到下一行
    3. 英文/專名/縮寫保護（What's Next, Subby, YouTube, don't）
    4. 數字 + 單位量詞黏著保護（1600萬、80萬、100分、2年）
    5. 行尾懸空介詞/助詞自動避讓（他是Lily從 -> 他是Lily | 從臺大...）
    """
    if cut <= 0 or cut >= len(plain):
        return cut

    is_token_char = lambda ch: ch.isascii() and (ch.isalnum() or ch in "'’-_")

    # A. 優先檢查：切點前後 window 範圍內是否有 CLAUSE_STARTERS（例如 "到底"、"因為"、"然後"）
    # 如果有，且把引導詞前方當作切點時長度合理（>= 4 字），切點直接精準吸附到引導詞正前方！
    for starter in CLAUSE_STARTERS:
        s_len = len(starter)
        for offset in range(-window, window + 1):
            idx = cut + offset
            if 0 <= idx <= len(plain) - s_len:
                if plain[idx:idx + s_len] == starter:
                    if idx >= 4:
                        return idx

    # B. 檢查是否有複合詞被切碎（例如 "創辦" | "人" -> "創辦人"）
    for comp in COMPOUND_SUFFIXES:
        c_len = len(comp)
        for i in range(1, c_len):
            start_idx = cut - i
            end_idx = start_idx + c_len
            if 0 <= start_idx and end_idx <= len(plain):
                if plain[start_idx:end_idx] == comp:
                    return end_idx

    # C. 英文單字/縮寫保護
    if is_token_char(plain[cut - 1]) and is_token_char(plain[cut]):
        left = cut
        while left > 0 and is_token_char(plain[left - 1]):
            left -= 1
        right = cut
        while right < len(plain) and is_token_char(plain[right]):
            right += 1
        if (cut - left) <= (right - cut) and left > 0:
            cut = left
        else:
            cut = right

    # D. 數字 + 單位量詞黏著保護 (1600 + 萬 -> 1600萬, 80 + 萬 -> 80萬, 2 + 年 -> 2年)
    if cut > 0 and cut < len(plain):
        if plain[cut - 1].isdigit() and plain[cut] in STICKY_UNITS:
            while cut < len(plain) and plain[cut] in STICKY_UNITS:
                cut += 1

    # E. 防止行尾懸空介詞/助詞 (e.g. "他是Lily從" -> "他是Lily" | "從臺大中文系...")
    if cut > 1 and cut < len(plain) and plain[cut - 1] in TRAILING_HANGING and not plain[cut - 2] in TRAILING_HANGING:
        if cut - 1 >= 4:
            cut -= 1

    return cut

def stage2_subtitle(cues: list, words: list, max_chars: int = None,
                    max_secs: float = None) -> list:
    """產生播放用字幕：segment 給語意與標點，word 給精準時間

    為什麼要混合：中文的 word-level 最小單位常常是單字，只看停頓切會把
    「位置」切成「位」「置」兩條。標點才是語意邊界，時間則非 word 不可。

    對齊一定要「整份一次做」：逐條去 3000 多個詞裡找匹配，短句會對到錯的
    位置，切出「他」「就」「S」這種碎片。
    """
    max_chars = max_chars or SUB_MAX_CHARS
    max_secs = max_secs or SUB_MAX_SECS
    if not words or not cues:
        return []

    # 1) 全文一次對齊：記住每個 cue 在全文中的字元偏移
    plains, offsets, pos = [], [], 0
    for c in cues:
        p = _strip_marks(c["text"])
        plains.append(p)
        offsets.append(pos)
        pos += len(p)
    times = _align_to_words("".join(plains), words)
    if len(times) < pos:
        print(f"  對齊警告：字元 {pos} 個但只對到 {len(times)} 個，字幕時間可能不準")
        return []

    # 2) 逐條依標點切，時間從全域對齊表查
    out = []
    for c, plain, off in zip(cues, plains, offsets):
        if not plain:
            continue
        prev = 0
        for cut in _cut_points(c["text"], max_chars * 3):   # 先只依標點切
            a, b = prev, min(cut, len(plain))
            prev = b
            if b <= a:
                continue
            cur_x = a
            while cur_x < b:
                if b - cur_x <= max_chars:
                    next_y = b
                else:
                    lo = cur_x + max(2, int(max_chars * 0.6))
                    hi = min(b - 2, cur_x + int(max_chars * 1.4))
                    if hi <= lo:
                        next_y = min(cur_x + max_chars, b)
                    else:
                        best_i, best_gap = lo, -1.0
                        for i in range(lo, hi + 1):
                            gap = times[off + i][0] - times[off + i - 1][1]
                            if gap > best_gap:
                                best_gap, best_i = gap, i
                        next_y = best_i

                if next_y < b:
                    next_y = _nudge_cut(plain, next_y)
                if next_y <= cur_x:
                    next_y = min(cur_x + max_chars, b)

                st, en = times[off + cur_x][0], times[off + next_y - 1][1]
                if en <= st:
                    en = st + 0.4
                out.append({"start": st, "end": en, "text": plain[cur_x:next_y]})
                cur_x = next_y

    out.sort(key=lambda x: x["start"])
    # word 時間戳偶有小重疊，逐條壓平，避免播放器閃爍
    for i in range(len(out) - 1):
        if out[i]["end"] > out[i + 1]["start"]:
            out[i]["end"] = out[i + 1]["start"]
    return [c for c in out if c["end"] > c["start"] and c["text"].strip()]

# ------------------------------------------------------------------ 主流程

def load_vocab(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        print(f"  找不到術語檔：{p}")
        return ""
    text = re.sub(r"https?://\S+", " ", p.read_text(encoding="utf-8", errors="ignore"))
    freq = {}
    forms = {}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9._\-]{2,}", text):
        k = w.lower()
        freq[k] = freq.get(k, 0) + 1
        forms.setdefault(k, {})
        forms[k][w] = forms[k].get(w, 0) + 1
    picked = []
    for k, n in sorted(freq.items(), key=lambda x: -x[1]):
        if n < 3:
            continue
        cand = sorted(forms[k].items(), key=lambda x: (x[0].islower(), -x[1]))
        picked.append(cand[0][0])
        if len(picked) >= 40:
            break
    return "、".join(picked)


def main():
    ap = argparse.ArgumentParser(description="SRT 精修：階段 4 LLM 校對 + 階段 2 語意聚合")
    ap.add_argument("srt", help="輸入的 .srt")
    ap.add_argument("-o", "--output", default=None, help="輸出路徑（預設 <原檔名>_精修.srt）")
    ap.add_argument("--llm", choices=["agy", "none"], default="agy",
                    help="階段 4 後端；none = 跳過校對只做聚合")
    ap.add_argument("--batch", type=int, default=DEFAULT_BATCH, help="每批 cue 數")
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                    help="同時派出的 agy 數（預設 4）")
    ap.add_argument("--vocab", default=None, help="術語來源純文字檔（如聊天室記錄）")
    ap.add_argument("--mode", choices=["transcript", "subtitle"], default="transcript",
                    help="transcript=逐字稿（長句＋標點，給 AI 讀）；subtitle=字幕（短條、無標點，給人看）")
    ap.add_argument("--words", default=None,
                    help="word-level 時間戳 json（字幕模式用）；省略則自動找同名 .words.json")
    ap.add_argument("--categories", default=None, help="要套用的詞庫類別，逗號分隔；省略=全部")
    ap.add_argument("--max-chars", type=int, default=SUB_MAX_CHARS, help="字幕模式：每條字數上限")
    ap.add_argument("--max-secs", type=float, default=SUB_MAX_SECS, help="字幕模式：每條秒數上限")
    ap.add_argument("--no-clean", action="store_true", help="跳過階段 0 文字層幻覺清理")
    ap.add_argument("--no-aggregate", action="store_true", help="跳過階段 2 聚合")
    ap.add_argument("--range", dest="rng", default=None,
                    help="只處理某區間，格式 起分:迄分，例如 55:65")
    args = ap.parse_args()

    src = Path(args.srt)
    if not src.exists():
        sys.exit(f"找不到檔案：{src}")

    cues = parse_srt(src)
    print(f"讀入 {src.name}：{len(cues)} 條")

    if args.rng:
        a, b = (float(x) for x in args.rng.split(":"))
        cues = [c for c in cues if a * 60 <= c["start"] < b * 60]
        print(f"  區間過濾 {a}~{b} 分：剩 {len(cues)} 條")
    if not cues:
        sys.exit("沒有可處理的字幕條目")

    cats = [x.strip() for x in args.categories.split(",") if x.strip()] if args.categories else None

    # ---------------- 字幕模式：完全不看 srt 的斷句，改用 word-level ----------------
    if args.mode == "subtitle":
        wp = Path(args.words) if args.words else None
        if wp is None:
            stem = src.stem
            for suffix in ("_逐字稿", "_精修"):
                if stem.endswith(suffix):
                    stem = stem[: -len(suffix)]
            cand = [src.with_name(stem + ".words.json"), src.with_suffix(".words.json")]
            wp = next((c for c in cand if c.exists()), None)
        if wp is None or not wp.exists():
            sys.exit("字幕模式需要 word-level 時間戳。請先用 transcribe.py v2.1.0 轉檔"
                     "（會產生 <檔名>.words.json），或用 --words 指定。")
        words = json.loads(Path(wp).read_text(encoding="utf-8"))
        print(f"  word-level 來源：{Path(wp).name}（{len(words)} 個）")
        if args.rng:
            a, b = (float(x) for x in args.rng.split(":"))
            words = [w for w in words if a * 60 <= w["start"] < b * 60]
            print(f"  區間過濾 {a}~{b} 分：剩 {len(words)} 個 word")
        if LX:
            fixes = LX.collect_fixes(cats)
            if fixes:
                hit_total = 0
                for w in words:
                    w["word"], hit = LX.apply_fixes(w["word"], fixes)
                    hit_total += sum(hit.values())
                if hit_total:
                    print(f"  詞庫替換 {hit_total} 處")
        subs = stage2_subtitle(cues, words, args.max_chars, args.max_secs)
        avg_c = sum(_len_zh(c["text"]) for c in subs) / max(1, len(subs))
        avg_s = sum(c["end"] - c["start"] for c in subs) / max(1, len(subs))
        longest = max((c["end"] - c["start"] for c in subs), default=0)
        print(f"[字幕模式] 切出 {len(subs)} 條；平均 {avg_c:.1f} 字 / {avg_s:.2f} 秒；最長 {longest:.2f} 秒")
        out = Path(args.output) if args.output else src.with_name(src.stem + "_字幕.srt")
        out.write_text(build_srt(subs), encoding="utf-8")
        print("")
        print(f"完成：{out}")
        return

    # ---------------- 逐字稿模式 ----------------
    vocab = load_vocab(args.vocab)
    if vocab:
        print(f"  術語表：{vocab[:80]}...")

    if not args.no_clean:
        print("[階段 0] 文字層幻覺清理")
        before0 = len(cues)
        cues, st0 = stage0_clean(cues)
        print(f"  水印句丟棄 {st0['watermark']}、句內重複修復 {st0['dedup']}、相鄰重複丟棄 {st0['adjacent']}；{before0} 條 → {len(cues)} 條")

    print(f"[階段 4] LLM 校對（後端 {args.llm}，每批 {args.batch} 條，並行 {args.workers}）")
    cues, st = stage4_proofread(cues, vocab, args.batch, args.llm, args.workers)
    if st["skipped"]:
        print("  已跳過")
    else:
        print(f"  批次 {st['batches']}，成功 {st['ok']}，失敗保留原文 {st['failed']}")

    before = len(cues)
    if args.no_aggregate:
        print("[階段 2] 已跳過")
    else:
        print("[階段 2] 依標點聚合")
        cues = stage2_aggregate(cues)
    marks = sum(len(re.findall(r"[，。、？！]", c["text"])) for c in cues)
    avg = sum(_len_zh(c["text"]) for c in cues) / max(1, len(cues))
    print(f"  {before} 條 → {len(cues)} 條；標點 {marks} 個；平均每條 {avg:.1f} 字")

    out = Path(args.output) if args.output else src.with_name(src.stem + "_精修.srt")
    out.write_text(build_srt(cues), encoding="utf-8")
    print(f"\n完成：{out}")


if __name__ == "__main__":
    main()
