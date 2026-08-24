# -*- coding: utf-8 -*-
"""
lexicon.py — 分類詞庫的讀寫與套用

版本：v1.0.0
版更記錄：
  v1.0.0 (2026-08-22) by Claude (Opus 5)
    - 分類詞庫：terms（辨識前餵 Whisper 當小抄）／fixes（辨識後錯字替換）
    - 供 transcribe.py 與 refine_srt.py 共用，並由 8767 後台維護

設計要點：
  - fixes 一律「長詞優先」替換，否則「傻逼傻逼→Subby」會被「傻逼→Subby」拆成 SubbySubby
  - build_prompt 會截斷到 Whisper 的 prompt 上限（約 224 tokens，中文保守抓 600 字元）
"""
import json
import re
from pathlib import Path

LEXICON_PATH = Path(__file__).with_name("lexicon.json")
MAX_PROMPT_CHARS = 240

# 抽詞時要濾掉的通用英文字
_EN_STOP = {
    "the", "and", "for", "you", "that", "this", "with", "have", "not", "but", "can",
    "are", "was", "all", "your", "our", "one", "two", "get", "use", "com", "www",
    "ok", "okay", "yeah", "yes", "sec", "min", "amp", "http", "https", "code",
}
# 聊天室記錄的發言者表頭（Zoom/Teams 匯出格式）
_CHAT_HEADER = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}.*?[對对].*?:\s*$")


# ------------------------------------------------------------------ 讀寫

def load(path: Path = None) -> dict:
    p = Path(path) if path else LEXICON_PATH
    if not p.exists():
        return {"categories": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  詞庫讀取失敗（{e}），視為空詞庫")
        return {"categories": {}}


def save(data: dict, path: Path = None) -> None:
    p = Path(path) if path else LEXICON_PATH
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def category_names(data: dict = None) -> list:
    d = data or load()
    return [k for k in (d.get("categories") or {}).keys()]


# ------------------------------------------------------- 組小抄（terms）

def collect_terms(categories: list = None, data: dict = None) -> list:
    """取出指定類別的 terms；categories 為 None 代表全部類別"""
    d = data or load()
    cats = d.get("categories") or {}
    names = categories if categories else list(cats.keys())
    out = []
    for n in names:
        for t in (cats.get(n) or {}).get("terms") or []:
            if t not in out:
                out.append(t)
    return out


def collect_fixes(categories: list = None, data: dict = None) -> dict:
    """取出指定類別的 fixes 合併字典"""
    d = data or load()
    cats = d.get("categories") or {}
    names = categories if categories else list(cats.keys())
    out = {}
    for n in names:
        out.update((cats.get(n) or {}).get("fixes") or {})
    return out


def extract_terms_from_text(text: str, limit: int = 30, min_count: int = 2) -> list:
    """從影片簡介／聊天室記錄等自由文字抽高頻英文專名

    會先移除 URL 與聊天室發言者表頭，避免把與會者人名當成術語。
    """
    lines = [ln for ln in text.splitlines() if not _CHAT_HEADER.match(ln.strip())]
    body = re.sub(r"https?://\S+", " ", "\n".join(lines))
    freq, forms = {}, {}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9._'\-]{2,}", body):
        k = w.lower()
        if k in _EN_STOP or len(k) < 3:
            continue
        freq[k] = freq.get(k, 0) + 1
        forms.setdefault(k, {})
        forms[k][w] = forms[k].get(w, 0) + 1
    picked = []
    for k, n in sorted(freq.items(), key=lambda x: -x[1]):
        if n < min_count:
            continue
        # 同字不同大小寫時優先取含大寫的寫法（Qwen 而非 qwen）
        cand = sorted(forms[k].items(), key=lambda x: (x[0].islower(), -x[1]))
        picked.append(cand[0][0])
        if len(picked) >= limit:
            break
    return picked


def extract_terms_from_info_json(info_path: Path) -> list:
    """從 yt-dlp 的 .info.json 抽詞：標題 + 簡介 + 標籤"""
    try:
        d = json.loads(Path(info_path).read_text(encoding="utf-8"))
    except Exception:
        return []
    blob = " ".join([
        d.get("title") or "",
        d.get("description") or "",
        " ".join(d.get("tags") or []),
        d.get("channel") or "",
    ])
    return extract_terms_from_text(blob, limit=25, min_count=1)


def build_prompt(categories: list = None, extra_terms: list = None,
                 context_tail: str = "", data: dict = None) -> str:
    """組出要餵給 Whisper 的 initial prompt

    刻意寫成帶標點的完整句子——Whisper 會延續前綴風格，直出就自帶標點。
    """
    terms = collect_terms(categories, data)
    for t in (extra_terms or []):
        if t not in terms:
            terms.append(t)
    head = "以下是一場繁體中文的內容，"
    if terms:
        head += "會提到這些名稱：" + "、".join(terms) + "。"
    else:
        head += "請正確辨識專有名詞。"
    return (head + (context_tail or ""))[:MAX_PROMPT_CHARS]


# ------------------------------------------------------ 套用 fixes 替換

def apply_fixes(text: str, fixes: dict) -> tuple:
    """套用錯字替換，回傳 (新文字, {替換詞: 次數})

    長詞優先，避免「傻逼傻逼」被「傻逼」拆成 SubbySubby。
    """
    if not fixes or not text:
        return text, {}
    hit = {}
    for wrong in sorted(fixes.keys(), key=len, reverse=True):
        if wrong and wrong in text:
            n = text.count(wrong)
            text = text.replace(wrong, fixes[wrong])
            hit[wrong] = hit.get(wrong, 0) + n
    return text, hit


# --------------------------------------------- 後台用：新增詞條進詞庫

def add_terms(category: str, terms: list = None, fixes: dict = None,
              description: str = "", path: Path = None) -> dict:
    """把新詞寫進指定類別（類別不存在就建立），回傳異動統計"""
    d = load(path)
    cats = d.setdefault("categories", {})
    cat = cats.setdefault(category, {"描述": description or "", "terms": [], "fixes": {}})
    if description and not cat.get("描述"):
        cat["描述"] = description
    added_t, added_f = 0, 0
    for t in (terms or []):
        if t and t not in cat["terms"]:
            cat["terms"].append(t)
            added_t += 1
    for k, v in (fixes or {}).items():
        if k and v and cat["fixes"].get(k) != v:
            cat["fixes"][k] = v
            added_f += 1
    save(d, path)
    return {"category": category, "terms_added": added_t, "fixes_added": added_f}
