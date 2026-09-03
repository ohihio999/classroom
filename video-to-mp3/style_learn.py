# -*- coding: utf-8 -*-
"""
style_learn.py — 從「人工校對版 vs 機器產出版」學斷句風格（掛在 8767 詞庫後台底下）

版本：v1.0.0
版更記錄：
  v1.0.0 (2026-08-26) by Claude (Opus 5)
    - 比對同一支影片的兩版字幕，學出四樣東西：尺度參數、子句起始詞、
      行尾懸空字、不可拆詞組，寫成 lexicon/style.json 供 refine_srt.py 讀取。

為什麼不是「教 Whisper 斷句」：Whisper 是固定模型，prompt 只影響用詞不影響斷句。
字幕的斷點實際上是 refine_srt.py 的 stage2_subtitle 決定的——上限字數、上限秒數、
CLAUSE_STARTERS（這些詞前面要斷）、TRAILING_HANGING（這些字後面不能斷）、
COMPOUND_SUFFIXES（這些詞不能拆）。所以「學斷句」＝把這六項從人工版反推出來。

比對的前提是兩份必須是同一支影片。相似度低於 0.7 就直接擋下來，
否則學到的是兩支不同影片的雜訊。
"""
import json
import re
from difflib import SequenceMatcher
from pathlib import Path

LEXICON_DIR = Path(r"C:\Users\admin\Desktop\classroom\transcribe-audio\lexicon")
STYLE_JSON = LEXICON_DIR / "style.json"

MARKS_RE = re.compile(r"[，。、？！,.?!；;：:「」『』…—\-（）()\s]")

# 與 refine_srt.py 內建表對齊，用來過濾「早就有了」的候選
BUILTIN_STARTERS = set([
    "到底", "究竟", "難道", "為什麼", "怎麼樣", "怎麼", "是不是", "能不能", "會不會", "可不可以", "要不要",
    "因為", "所以", "但是", "可是", "不過", "而且", "然後", "如果", "只要", "只有", "雖然", "儘管",
    "不管", "無論", "既然", "其實", "畢竟", "反正", "總之", "突然", "立刻", "馬上", "順便",
    "大概", "甚至", "千萬", "一定",
])
BUILTIN_HANGING = set("從在和跟與被把將向對比及或那是的了著過")

# 這些字自己成不了詞，出現在候選詞組裡多半是對齊雜訊
NOISE_CHARS = set("啊喔哦嗯呃欸唉呀哇嘿")


def _plain(t: str) -> str:
    return MARKS_RE.sub("", t)


def parse_cues(path: str) -> list:
    """SRT → [{'text','start','end'}]；純文字檔則整段當一條（沒有時間軸）"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"找不到檔案：{path}")
    raw = p.read_text(encoding="utf-8-sig", errors="ignore").replace("\r\n", "\n")
    if "-->" not in raw:
        return [{"text": line.strip(), "start": None, "end": None}
                for line in raw.split("\n") if line.strip()]

    def t2s(t):
        t = t.strip().replace(",", ".")
        h, m, s = t.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    out = []
    for block in re.split(r"\n{2,}", raw.strip()):
        lines = [x for x in block.split("\n") if x.strip()]
        if len(lines) >= 2 and "-->" in lines[1]:
            a, b = lines[1].split("-->")
            text = " ".join(x.strip() for x in lines[2:])
            if text:
                out.append({"text": text, "start": t2s(a), "end": t2s(b)})
        elif len(lines) >= 1 and "-->" in lines[0]:
            a, b = lines[0].split("-->")
            text = " ".join(x.strip() for x in lines[1:])
            if text:
                out.append({"text": text, "start": t2s(a), "end": t2s(b)})
    return out


def _percentile(xs: list, q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round((len(xs) - 1) * q))))
    return float(xs[i])


def describe(cues: list) -> dict:
    """一份字幕的尺度統計"""
    chars = [len(_plain(c["text"])) for c in cues if _plain(c["text"])]
    durs = [round(c["end"] - c["start"], 2) for c in cues
            if c.get("start") is not None and c.get("end") is not None and c["end"] > c["start"]]
    return {
        "cues": len(cues),
        "chars_avg": round(sum(chars) / len(chars), 1) if chars else 0,
        "chars_p50": _percentile(chars, 0.50),
        "chars_p90": _percentile(chars, 0.90),
        "chars_p95": _percentile(chars, 0.95),
        "chars_max": max(chars) if chars else 0,
        "secs_avg": round(sum(durs) / len(durs), 2) if durs else 0,
        "secs_p90": _percentile(durs, 0.90),
        "secs_p95": _percentile(durs, 0.95),
        "secs_max": max(durs) if durs else 0,
    }


def _flatten(cues: list):
    """把每條的純文字接成一長串，並記錄每條結尾在串裡的位置（＝斷點）"""
    parts, breaks, pos = [], [], 0
    for c in cues:
        p = _plain(c["text"])
        if not p:
            continue
        parts.append(p)
        pos += len(p)
        breaks.append(pos)
    return "".join(parts), set(breaks[:-1])   # 最後一條的結尾不算斷點


def _map_index(text_from: str, text_to: str):
    """建 from → to 的字元位置對照表，用 difflib 對齊（兩版有錯字差異也能對上）"""
    sm = SequenceMatcher(None, text_from, text_to, autojunk=False)
    mapping = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                mapping[i1 + k] = j1 + k
        elif tag == "replace":
            span_a, span_b = i2 - i1, j2 - j1
            for k in range(span_a):
                mapping[i1 + k] = j1 + min(span_b - 1, int(k * span_b / max(1, span_a))) if span_b else j1
        else:                                   # delete / insert
            for k in range(i1, i2):
                mapping[k] = j1
    return mapping, sm.ratio()


def learn(right_path: str, wrong_path: str, min_hits: int = 3) -> dict:
    """比對兩版字幕，回傳可勾選的建議清單（本函式不寫任何檔案）"""
    cues_r = parse_cues(right_path)
    cues_w = parse_cues(wrong_path)
    if not cues_r or not cues_w:
        raise ValueError("有一邊讀不到任何字幕條目")

    text_r, breaks_r = _flatten(cues_r)
    text_w, breaks_w = _flatten(cues_w)
    mapping, ratio = _map_index(text_w, text_r)
    if ratio < 0.70:
        raise ValueError(
            f"兩份內容相似度只有 {ratio:.0%}，看起來不是同一支影片的兩個版本。"
            "請確認「正確版」與「待校正版」對應同一段錄音。")

    # 機器版的斷點，換算到正確版的座標
    breaks_w_in_r = {mapping[b - 1] + 1 for b in breaks_w if (b - 1) in mapping}

    # ---- 1) 子句起始詞：正確版習慣在哪些詞「之前」斷開
    starters = {}
    for b in breaks_r:
        for n in (2, 3):
            w = text_r[b:b + n]
            if len(w) == n and not (set(w) & NOISE_CHARS) and not re.search(r"[a-zA-Z0-9]", w):
                starters.setdefault(w, 0)
                starters[w] += 1
    keep = {}
    for w, n in starters.items():
        total = text_r.count(w)
        if n >= min_hits and total and n / total >= 0.6 and w not in BUILTIN_STARTERS:
            keep[w] = n
    starter_out = []
    for w, n in keep.items():
        # 「你也」已經收了就不必再收「你也可」——同一個斷點位置，短的規則更泛用
        if len(w) == 3 and w[:2] in keep:
            continue
        missed = sum(1 for b in breaks_r if text_r[b:b + len(w)] == w and b not in breaks_w_in_r)
        starter_out.append({"w": w, "n": n, "total": text_r.count(w),
                            "ratio": round(n / text_r.count(w), 2), "missed": missed})
    starter_out.sort(key=lambda x: (-x["missed"], -x["n"]))

    # ---- 2) 行尾懸空字：機器版在這個字後面斷了，正確版從不這樣斷
    hang_bad, hang_ok = {}, {}
    for b in breaks_w_in_r:
        if 0 < b <= len(text_r) and b not in breaks_r:
            ch = text_r[b - 1]
            if ch not in NOISE_CHARS and not re.match(r"[a-zA-Z0-9]", ch):
                hang_bad[ch] = hang_bad.get(ch, 0) + 1
    for b in breaks_r:
        if 0 < b <= len(text_r):
            hang_ok[text_r[b - 1]] = hang_ok.get(text_r[b - 1], 0) + 1
    hang_out = [{"w": ch, "n": n, "ok": hang_ok.get(ch, 0)}
                for ch, n in hang_bad.items()
                if n >= min_hits and hang_ok.get(ch, 0) <= n * 0.25 and ch not in BUILTIN_HANGING]
    hang_out.sort(key=lambda x: -x["n"])

    # ---- 3) 不可拆詞組：機器版把它切成兩半，正確版整組不斷
    comp = {}
    for b in breaks_w_in_r:
        if 1 < b < len(text_r) and b not in breaks_r:
            for left, right in ((1, 1), (2, 1), (1, 2)):
                w = text_r[b - left:b + right]
                if len(w) == left + right and not (set(w) & NOISE_CHARS) \
                        and not re.search(r"[，。、？！\s]", w):
                    comp[w] = comp.get(w, 0) + 1
    # 首字已在內建懸空字表的（在你、的話…），內建規則本來就不會在那裡斷，不必再收
    comp_out = [{"w": w, "n": n, "total": text_r.count(w)}
                for w, n in comp.items()
                if n >= min_hits and len(w) >= 2 and w[0] not in BUILTIN_HANGING]
    # 同一處會同時產生 2 字與 3 字候選，長的排前面讓人先看見完整詞
    comp_out.sort(key=lambda x: (-x["n"], -len(x["w"])))

    st_r, st_w = describe(cues_r), describe(cues_w)
    # 上限取 p95：p50/p90 是「平常長度」，拿它當上限會逼程式切得比人工還碎；
    # 最大值又常是一兩條特例。p95 才是「你偶爾允許到這麼長」。
    chars95 = _percentile([len(_plain(c["text"])) for c in cues_r if _plain(c["text"])], 0.95)
    secs95 = _percentile([round(c["end"] - c["start"], 2) for c in cues_r
                          if c.get("start") is not None and c.get("end") is not None
                          and c["end"] > c["start"]], 0.95)
    suggest = {
        "max_chars": int(max(8, min(30, round(chars95 or st_r["chars_p90"])))),
        "max_secs": round(max(1.5, min(8.0, secs95 or st_r["secs_p90"] or 4.0)) * 2) / 2,
    }

    return {
        "ok": True,
        "similarity": round(ratio, 3),
        "right": st_r,
        "wrong": st_w,
        "suggest": suggest,
        "starters": starter_out[:40],
        "hanging": hang_out[:30],
        "compounds": comp_out[:40],
        "source": {"right": str(right_path), "wrong": str(wrong_path)},
    }


# ------------------------------------------------------------ style.json

def load_style() -> dict:
    if not STYLE_JSON.exists():
        return {"max_chars": None, "max_secs": None,
                "clause_starters": [], "trailing_hanging": [], "compound_suffixes": [],
                "learned": []}
    try:
        return json.loads(STYLE_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": str(e), "clause_starters": [], "trailing_hanging": [],
                "compound_suffixes": [], "learned": []}


def apply_style(picked: dict) -> dict:
    """把使用者勾選的項目併進 style.json（累積，不覆蓋既有的）"""
    from datetime import datetime
    cur = load_style()
    cur.pop("_error", None)

    if picked.get("max_chars"):
        cur["max_chars"] = int(picked["max_chars"])
    if picked.get("max_secs"):
        cur["max_secs"] = float(picked["max_secs"])

    added = {}
    for key, field in (("starters", "clause_starters"),
                       ("hanging", "trailing_hanging"),
                       ("compounds", "compound_suffixes")):
        old = list(cur.get(field) or [])
        new = [w for w in (picked.get(key) or []) if w and w not in old]
        cur[field] = old + new
        added[field] = len(new)

    cur.setdefault("learned", []).append({
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "right": picked.get("source", {}).get("right", ""),
        "wrong": picked.get("source", {}).get("wrong", ""),
        "added": added,
    })
    cur["_版本"] = "1.0.0"

    LEXICON_DIR.mkdir(parents=True, exist_ok=True)
    if STYLE_JSON.exists():
        (LEXICON_DIR / "style.bak.json").write_text(
            STYLE_JSON.read_text(encoding="utf-8"), encoding="utf-8")
    STYLE_JSON.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "added": added, "style": cur}
