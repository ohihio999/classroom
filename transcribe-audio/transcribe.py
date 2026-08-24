# -*- coding: utf-8 -*-
"""
transcribe.py — 批次音檔轉繁體中文逐字稿（Groq Whisper / AssemblyAI）

版本：v2.2.0
版更記錄：
  v2.2.0 (2026-08-23) by Antigravity (Gemini 3.7 Flash)
    - 字幕輸出直接整合 refine_srt 的 word-level 智慧切條（預設每條 7~10 字 / 1.5 秒），
      徹底解決 Whisper 原生 segment 退化導致 90~200 字超長句問題，轉錄完成即為標準短字幕。
    - 整合分類詞庫 MAX_PROMPT_CHARS (240 字元 / ~340 bytes) 與 UTF-8 bytes <= 800 防護，
      修復因中文多位元組導致超出 Groq 896 bytes/chars 限制報錯之問題。
    - 歷史版本對照與合併過程記錄於 transcribe-audio/CHANGELOG.md 與 SOP.md。
  v2.1.0 (2026-08-22) by Claude (Opus 5)
    - 改要 word-level 時間戳，另存 <檔名>.words.json 供字幕切條使用
      （Whisper 的 segment 邊界偶發退化，最長曾出現 42 秒／221 字一條；
        word-level 讓字幕切分不再受它影響）
    - 小抄改讀分類詞庫 lexicon/lexicon.json，新增 --categories 選類別
    - 自動從同目錄 .info.json（YouTube 標題/簡介/標籤）抽術語
    - 轉錄後套用詞庫 fixes 做錯字替換
  v2.0.0 (2026-08-22) by Claude (Opus 5)
    - 新增 Whisper prompt 詞彙導引 + 前段上下文接續，temperature=0
      （修正 DGX Spark→DGS、inference→influence、VLM→VRM 類專名錯字）
    - 新增幻覺過濾：水印句黑名單、no_speech_prob/avg_logprob 判定、重複迴圈去重
      （清除「請不吝點贊…明鏡與點點欄目」「優優獨播劇場」等 Whisper 幻覺）
    - opencc s2twp → s2tw：停止把講者原話的「函數/參數」竄改成「函式/引數」
    - 壓縮位元率 32k → 64k（25MB 是每段上限，先壓後切每段僅約 1.2MB，32k 為不必要犧牲）
    - 新增 --vocab 參數；未指定時自動從同目錄術語檔/聊天室記錄抽術語
    - 修既有 bug：環境變數存有失效 GROQ key 時，會跳過改用 keyring 的有效 key
    - 依據：D:\本機MD檔 _研究\課程逐字稿整理            20260822_逐字稿生成流水線優化方案_Antigravity架構提案.md（Claude 評審段）
  v1.x 原版（使用者自撰）— 備份於 transcribe.py.bak-20260822
"""
import os
import sys
import re
import subprocess
import tempfile
import time
import shutil
import argparse
from pathlib import Path
from groq import Groq, RateLimitError
try:
    import opencc
except ImportError:
    import subprocess as _sp
    _sp.run([sys.executable, "-m", "pip", "install", "opencc-python-reimplemented", "-q"], check=True)
    import opencc
try:
    import assemblyai as aai
except ImportError:
    import subprocess as _sp
    _sp.run([sys.executable, "-m", "pip", "install", "assemblyai", "-q"], check=True)
    import assemblyai as aai
import requests as _requests

# v2.1.0: 分類詞庫（terms 餵小抄／fixes 事後替換），由 8767 後台維護
sys.path.insert(0, str(Path(__file__).parent / 'lexicon'))
try:
    import lexicon as LX
except Exception as _e:
    LX = None
    print(f'  詞庫模組載入失敗（{_e}），改用內建預設詞表')

# v2.2.0: 自動短字幕切條模組
try:
    import refine_srt
except Exception:
    refine_srt = None

FFMPEG = os.environ.get("FFMPEG_PATH") or "ffmpeg"
for _candidate in [
    r"C:\Users\admin\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe",
]:
    if Path(_candidate).exists():
        FFMPEG = _candidate
        break

def _candidate_keys() -> list[tuple[str, str]]:
    """回傳 (來源, key) 候選清單：環境變數優先，keyring 次之"""
    out = []
    env = os.environ.get("GROQ_API_KEY", "")
    if env:
        out.append(("環境變數", env))
    try:
        import keyring
        k = keyring.get_password("groq", "api_key") or ""
        if k and k != env:
            out.append(("keyring", k))
    except Exception:
        pass
    return out


def _make_client() -> Groq:
    """v2.0.0: 逐一驗證候選 key，跳過失效的那把

    （環境變數裡留著過期 key 時，舊版會直接拿它去撞 401，
      keyring 裡的有效 key 永遠用不到。）
    """
    cands = _candidate_keys()
    if not cands:
        msg = [
            "找不到 GROQ API Key。請執行一次：",
            "  pip install keyring",
            '''  python -c "import keyring; keyring.set_password('groq', 'api_key', '你的KEY')"''',
            "之後就不需要再設定。",
        ]
        raise SystemExit(chr(10).join(msg))
    last_err = None
    for src_name, key in cands:
        c = Groq(api_key=key, timeout=120.0)
        try:
            c.models.list()
            if src_name != cands[0][0]:
                print(f"  （{cands[0][0]}的 key 失效，改用 {src_name} 的 key）")
            return c
        except Exception as e:
            last_err = e
            print(f"  {src_name} 的 GROQ key 無法使用：{type(e).__name__}")
    raise SystemExit(f"所有 GROQ API Key 都無法使用，最後錯誤：{last_err}")


client = _make_client()
# s2tw：只做簡→繁字形轉換，不做台灣慣用詞替換
# （s2twp 會把講者原話「函數/參數」竄改成「函式/引數」，實測造成同一稿兩種用詞）
_cc = opencc.OpenCC('s2tw')

SUPPORTED = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm"}
CHUNK_SECONDS = 300

# ============================================================
# v2.0.0 新增：詞彙導引與幻覺過濾
# ============================================================

# Whisper prompt 上限約 224 tokens，Groq 要求 896 bytes/chars 以內，保守抓 240 字元（約 720 bytes）
MAX_PROMPT_CHARS = 240

VOCAB_DEFAULT = (
    "以下是一場繁體中文技術分享的逐字紀錄，內容包含："
    "LLM、Agent Harness、Claude Code、Codex、MCP、vLLM、SGLang、Ollama、"
    "DGX Spark、GB300、RTX Spark、Mac Studio、VLM、OCR、inference engine、"
    "sandbox、token、quantization、FP8、KV Cache、RAG。"
)

# Whisper 在音樂/長靜音處會吐出訓練資料裡的字幕組水印，整條都是捏造的
HALLUCINATION_RES = [
    re.compile(r"請不吝(點贊|點讚|点赞)"),
    re.compile(r"(明鏡|明镜)與(點點|点点)"),
    re.compile(r"(優優|优优)獨播劇場|YoYo Television Series", re.I),
    re.compile(r"字幕(由|製作).{0,12}(提供|製作|字幕組)"),
    re.compile(r"(訂閱|订阅).{0,6}(轉發|转发).{0,6}(打賞|打赏)"),
    re.compile(r"^(謝謝觀看|感謝觀看|下集再見|請訂閱)[。\s]*$"),
]

VOCAB_FILE = None        # 由 --vocab 指定；None 時自動偵測同目錄術語檔
CATEGORIES = None        # 由 --categories 指定要套用的詞庫類別；None = 全部
NO_SPEECH_MAX = 0.7      # 高於此值且 logprob 極低 -> 判定非語音
LOGPROB_MIN = -1.0
COMPRESSION_MAX = 2.4    # Whisper 官方重複偵測門檻


def _dedup_repeat(t: str) -> str:
    """把整句重複兩次以上的解碼迴圈縮回一次"""
    n = len(t)
    if n < 8:
        return t
    if n % 2 == 0 and t[:n // 2] == t[n // 2:]:
        return t[:n // 2]
    for size in range(2, n // 2 + 1):
        if n % size == 0 and t[:size] * (n // size) == t:
            return t[:size]
    return t


def _filter_block(b: dict):
    """回傳 (是否丟棄, 原因)；就地修正重複迴圈"""
    t = (b.get("text") or "").strip()
    if not t:
        return True, "空白"
    for rx in HALLUCINATION_RES:
        if rx.search(t):
            return True, "水印幻覺"
    nsp, lp = b.get("no_speech_prob"), b.get("avg_logprob")
    if nsp is not None and lp is not None and nsp > NO_SPEECH_MAX and lp < LOGPROB_MIN:
        return True, f"非語音(no_speech={nsp:.2f})"
    cr = b.get("compression_ratio")
    if cr is not None and cr > COMPRESSION_MAX:
        fixed = _dedup_repeat(t)
        if fixed != t:
            b["text"] = fixed
            return False, f"去重複(cr={cr:.2f})"
    return False, ""


_EN_STOP = {
    "the", "and", "for", "you", "that", "this", "with", "have", "not", "but", "can",
    "are", "was", "all", "your", "our", "one", "two", "get", "use", "com", "www",
    "ok", "okay", "yeah", "yes", "sec", "min", "amp", "http", "https", "code",
}
# 聊天室記錄的發言者表頭行（Zoom/Teams 匯出格式）：日期 + 名字 + 「對 所有人:」
_CHAT_HEADER = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}.*?[對对].*?:\s*$")


def compress(audio_path: Path) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    compressed = Path(tmp.name)
    print(f"  壓縮中（mono 16kHz 64k）...")
    subprocess.run(
        [FFMPEG, "-y", "-i", str(audio_path),
         "-ac", "1", "-ar", "16000", "-b:a", "64k", str(compressed)],
        check=True, capture_output=True
    )
    print(f"  壓縮完成：{compressed.stat().st_size / 1024 / 1024:.1f} MB")
    return compressed


def split_audio(audio_path: Path) -> list[Path]:
    tmp_dir = Path(tempfile.mkdtemp())
    pattern = str(tmp_dir / "chunk_%03d.mp3")
    subprocess.run(
        [FFMPEG, "-y", "-i", str(audio_path),
         "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
         "-c", "copy", pattern],
        check=True, capture_output=True
    )
    chunks = sorted(tmp_dir.glob("chunk_*.mp3"))
    print(f"  切成 {len(chunks)} 段（每段 {CHUNK_SECONDS//60} 分鐘）")
    return chunks


def time_to_ms(t: str) -> int:
    h, m, rest = t.split(':')
    s, ms = rest.split(',')
    return int(h)*3600000 + int(m)*60000 + int(s)*1000 + int(ms)


def ms_to_time(ms: int) -> str:
    h = ms // 3600000; ms %= 3600000
    m = ms // 60000; ms %= 60000
    s = ms // 1000; ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(srt_text: str) -> list[dict]:
    blocks = []
    for block in re.split(r'\n{2,}', srt_text.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 3 or '-->' not in lines[1]:
            continue
        start, end = lines[1].split(' --> ')
        blocks.append({
            'start': start.strip(),
            'end': end.strip(),
            'text': '\n'.join(lines[2:]).strip()
        })
    return blocks


def offset_blocks(blocks: list[dict], offset_ms: int) -> list[dict]:
    return [{
        'start': ms_to_time(time_to_ms(b['start']) + offset_ms),
        'end': ms_to_time(time_to_ms(b['end']) + offset_ms),
        'text': b['text']
    } for b in blocks]


def blocks_to_srt(all_blocks: list[dict]) -> str:
    parts = []
    for i, b in enumerate(all_blocks, 1):
        parts.append(f"{i}\n{b['start']} --> {b['end']}\n{b['text']}")
    return "\n\n".join(parts) + "\n"


def _parse_retry_after(err: Exception) -> int:
    m = re.search(r'try again in (\d+)m(\d+)s', str(err))
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + 5
    m = re.search(r'try again in ([\d.]+)s', str(err))
    if m:
        return int(float(m.group(1))) + 5
    return 120


def build_vocab(audio_path: Path, vocab_arg: str | None) -> str:
    """組出 Whisper 的 initial prompt

    來源三合一：
      1. 分類詞庫 lexicon.json 的 terms（由 --categories 選類別）
      2. 同目錄 .info.json（YouTube 標題／簡介／標籤）自動抽的專名
      3. 同目錄術語檔或聊天室記錄（--vocab 指定，或自動偵測）
    """
    extra = []

    # 來源 2：yt-dlp 的 info.json
    if LX:
        for info in sorted(audio_path.parent.glob(f"{audio_path.stem}*.info.json")):
            got = LX.extract_terms_from_info_json(info)
            if got:
                extra.extend(got)
                print(f"  info.json 抽出 {len(got)} 個專名：{info.name}")
            break

    # 來源 3：術語檔／聊天室記錄
    src_file = Path(vocab_arg) if vocab_arg else None
    if src_file is None:
        for pat in (f"{audio_path.stem}_terms.txt", "meeting_saved*.txt", "*_聊天室*.txt"):
            hit = sorted(audio_path.parent.glob(pat))
            if hit:
                src_file = hit[0]
                break
    if src_file and src_file.exists() and LX:
        try:
            got = LX.extract_terms_from_text(src_file.read_text(encoding="utf-8", errors="ignore"))
            if got:
                extra.extend(got)
                print(f"  術語來源：{src_file.name}（抽出 {len(got)} 個專名）")
        except Exception as e:
            print(f"  術語抽取略過：{e}")

    if LX:
        cats = CATEGORIES if CATEGORIES else None
        if cats:
            print(f"  套用詞庫類別：{chr(12289).join(cats)}")
        return LX.build_prompt(cats, extra_terms=extra)

    # 詞庫模組載不到時的退路
    base = VOCAB_DEFAULT
    if extra:
        base += "另包含：" + chr(12289).join(extra[:30]) + chr(12290)
    return base[:MAX_PROMPT_CHARS]

def transcribe_chunk(audio_path: Path, index: int, total: int,
                     prompt: str = "") -> tuple:
    """回傳 (segments, words)
    v2.0.0: 加入 initial prompt（術語導引 + 前段上下文）與 temperature=0
    v2.1.0: 同時取回 word-level 時間戳，供字幕切條使用"""
    print(f"    段落 {index}/{total} 轉錄中...")
    for attempt in range(5):
        try:
            eff_prompt = prompt if prompt else VOCAB_DEFAULT
            eff_bytes = eff_prompt.encode("utf-8")
            if len(eff_bytes) > 800:
                eff_prompt = eff_bytes[:800].decode("utf-8", errors="ignore")
            with open(audio_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    file=(audio_path.name, f.read()),
                    model="whisper-large-v3",
                    language="zh",
                    response_format="verbose_json",
                    prompt=eff_prompt,
                    temperature=0,
                    timestamp_granularities=["word", "segment"],
                )
            d = result.model_dump() if hasattr(result, "model_dump") else dict(result)
            return d.get("segments") or [], d.get("words") or []
        except RateLimitError as e:
            wait = _parse_retry_after(e)
            print(f"    速率限制，等待 {wait} 秒後重試...")
            time.sleep(wait)
        except Exception as e:
            if attempt < 4:
                print(f"    重試（{attempt+1}/5）：{e}")
                time.sleep(5)
            else:
                raise


def process_file_diarize(audio_path: Path, speakers: int):
    output_md = audio_path.parent / f"{audio_path.stem}_逐字稿.md"
    if output_md.exists():
        print(f"  跳過（逐字稿已存在）：{output_md.name}")
        return

    key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if not key:
        raise SystemExit("找不到 ASSEMBLYAI_API_KEY 環境變數")

    print(f"  檔案大小：{audio_path.stat().st_size / 1024 / 1024:.1f} MB")
    compressed = compress(audio_path)
    try:
        print(f"  上傳至 AssemblyAI（說話者辨識模式）...")
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        with open(compressed, "rb") as _fh:
            upload_resp = _requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers={"authorization": key},
                data=_fh,
                timeout=300,
                verify=False,
            )
        upload_resp.raise_for_status()
        upload_url = upload_resp.json()["upload_url"]
        print(f"  上傳完成，送出轉錄請求...")
        aai.settings.api_key = key
        config = aai.TranscriptionConfig(
            language_code="zh",
            speaker_labels=True,
            speakers_expected=speakers if speakers > 0 else None
        )
        transcript = aai.Transcriber().transcribe(upload_url, config)
    finally:
        if compressed.exists():
            compressed.unlink()

    if transcript.error:
        raise RuntimeError(f"AssemblyAI 錯誤：{transcript.error}")

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(f"# {audio_path.stem} 逐字稿\n\n")
        current_speaker = None
        for utterance in transcript.utterances:
            text = _cc.convert(utterance.text)
            if utterance.speaker != current_speaker:
                current_speaker = utterance.speaker
                f.write(f"\n**[說話者{utterance.speaker}]**\n\n")
            f.write(f"{text}\n")

    print(f"  完成：{output_md.name}")


def process_file(audio_path: Path):
    import json
    output_md = audio_path.parent / f"{audio_path.stem}_逐字稿.md"
    output_srt = audio_path.parent / f"{audio_path.stem}_逐字稿.srt"
    cache_dir = audio_path.parent / f".{audio_path.stem}_cache"

    if output_md.exists():
        print(f"  跳過（逐字稿已存在）：{output_md.name}")
        return

    cache_dir.mkdir(exist_ok=True)
    print(f"  檔案大小：{audio_path.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"  中斷點暫存：{cache_dir}")

    tmp_compressed = None
    tmp_dir = None
    try:
        compressed = compress(audio_path)
        tmp_compressed = compressed

        chunks = split_audio(compressed)
        tmp_dir = chunks[0].parent if chunks else None

        vocab = build_vocab(audio_path, VOCAB_FILE)
        all_blocks: list[dict] = []
        all_words: list[dict] = []   # v2.1.0: word-level 時間戳，供字幕切條
        prev_tail = ""          # 前一段結尾，接在 prompt 後面提供上下文
        for i, chunk in enumerate(chunks, 1):
            cache_file = cache_dir / f"chunk_{i:03d}.json"
            offset_sec = (i - 1) * CHUNK_SECONDS

            if cache_file.exists():
                print(f"    段落 {i}/{len(chunks)} 從暫存載入（略過 API 呼叫）")
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                # 舊版快取是 list（只有 blocks），新版是 dict（blocks + words）
                if isinstance(cached, dict):
                    blocks, words = cached.get("blocks") or [], cached.get("words") or []
                else:
                    blocks, words = cached, []
            else:
                seg_prompt = f"{vocab}{prev_tail}" if prev_tail else vocab
                segments, raw_words = transcribe_chunk(chunk, i, len(chunks), seg_prompt)
                words = []
                for w in raw_words:
                    x = w if isinstance(w, dict) else vars(w)
                    words.append({
                        'start': round(x['start'] + offset_sec, 3),
                        'end':   round(x['end'] + offset_sec, 3),
                        'word':  x.get('word', ''),
                    })
                blocks = []
                for seg in segments:
                    s = seg if isinstance(seg, dict) else vars(seg)
                    blocks.append({
                        'start': ms_to_time(int((s['start'] + offset_sec) * 1000)),
                        'end':   ms_to_time(int((s['end']   + offset_sec) * 1000)),
                        'text':  s['text'].strip(),
                        # v2.0.0: 保留 Whisper 品質欄位供幻覺過濾使用
                        'no_speech_prob':    s.get('no_speech_prob'),
                        'avg_logprob':       s.get('avg_logprob'),
                        'compression_ratio': s.get('compression_ratio'),
                    })
                cache_file.write_text(
                    json.dumps({"blocks": blocks, "words": words}, ensure_ascii=False),
                    encoding="utf-8")

            # 取本段結尾當作下一段的上下文（Whisper 會延續前綴的用詞與風格）
            if blocks:
                prev_tail = "".join(b['text'] for b in blocks[-6:])[-200:]
            all_blocks.extend(blocks)
            all_words.extend(words)

    finally:
        if tmp_compressed and tmp_compressed.exists():
            tmp_compressed.unlink()
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # 簡體 → 繁體（words 也要轉，否則字幕軌會殘留簡體）
    for b in all_blocks:
        b['text'] = _cc.convert(b['text'])
    for w in all_words:
        w['word'] = _cc.convert(w['word'])

    # v2.1.0: 套用詞庫 fixes 錯字替換
    if LX:
        fixes = LX.collect_fixes(CATEGORIES if CATEGORIES else None)
        if fixes:
            total_hit = {}
            for b in all_blocks:
                b['text'], hit = LX.apply_fixes(b['text'], fixes)
                for k, n in hit.items():
                    total_hit[k] = total_hit.get(k, 0) + n
            for w in all_words:
                w['word'], _ = LX.apply_fixes(w['word'], fixes)
            if total_hit:
                shown = "、".join(f"{k}→{fixes[k]}×{n}" for k, n in
                                  sorted(total_hit.items(), key=lambda x: -x[1])[:8])
                print(f"  詞庫替換 {sum(total_hit.values())} 處：{shown}")

    # v2.0.0: 幻覺過濾（水印句 / 非語音 / 重複迴圈），在轉繁之後才比對黑名單
    kept, dropped, fixed = [], [], 0
    _norm = lambda t: re.sub(r'[，。、？！,.?!\s]', '', t)
    for b in all_blocks:
        drop, reason = _filter_block(b)
        # 相鄰條目整句重複（跨 cue 的幻覺迴圈，compression_ratio 抓不到）
        # 只對 8 字以上出手，避免誤殺「對啊」「好」這類正常的連續短應答
        if not drop and kept and len(_norm(b['text'])) >= 8                 and _norm(b['text']) == _norm(kept[-1]['text']):
            drop, reason = True, "相鄰重複"
        if drop:
            dropped.append((b['start'], reason, b['text'][:30]))
        else:
            if reason:
                fixed += 1
            kept.append(b)
    if dropped or fixed:
        print(f"  幻覺過濾：丟棄 {len(dropped)} 條、修復重複 {fixed} 條")
        for st, reason, tx in dropped[:10]:
            print(f"    - [{st}] {reason}：{tx}")
        if len(dropped) > 10:
            print(f"    - ...另有 {len(dropped) - 10} 條")
    all_blocks = kept

    # v2.1.0: 另存 word-level 時間戳，供 refine_srt.py 切字幕用
    if all_words:
        words_file = audio_path.parent / f"{audio_path.stem}.words.json"
        words_file.write_text(json.dumps(all_words, ensure_ascii=False), encoding="utf-8")
        print(f"  word-level 時間戳：{len(all_words)} 個 → {words_file.name}")

    # v2.2.0: 產出 SRT 字幕檔（若有 word-level 時間戳，自動調用 refine_srt 進行短句智慧切分）
    srt_content = None
    if refine_srt and all_words:
        try:
            subs = refine_srt.stage2_subtitle(all_blocks, all_words)
            if subs:
                srt_content = refine_srt.build_srt(subs)
                avg_c = sum(refine_srt._len_zh(c["text"]) for c in subs) / max(1, len(subs))
                avg_s = sum(c["end"] - c["start"] for c in subs) / max(1, len(subs))
                print(f"  已自動產生精修短字幕：{len(subs)} 條（平均 {avg_c:.1f} 字 / {avg_s:.2f} 秒）")
        except Exception as e:
            print(f"  短字幕切條失敗，改用原始 Segment 輸出：{e}")

    if srt_content is None:
        srt_content = blocks_to_srt(all_blocks)

    with open(output_srt, "w", encoding="utf-8") as f:
        f.write(srt_content)

    with open(output_md, "w", encoding="utf-8") as f:
        f.write(f"# {audio_path.stem} 逐字稿\n\n")
        f.write("\n".join(b['text'] for b in all_blocks))

    # 完成後清掉暫存
    shutil.rmtree(cache_dir, ignore_errors=True)
    print(f"  完成：{output_md.name}  +  {output_srt.name}")


DEFAULT_FOLDER = "C:/OBS"
DEFAULT_DONE_FOLDER = r"C:\OBS\轉檔完成"


def main():
    parser = argparse.ArgumentParser(description='音檔轉逐字稿')
    parser.add_argument('path', nargs='?', help='音檔或資料夾路徑（省略則互動輸入）')
    parser.add_argument('--mode', choices=['standard', 'diarize'], default='standard',
                        help='standard=Groq標準, diarize=AssemblyAI說話者辨識')
    parser.add_argument('--speakers', type=int, default=0,
                        help='說話者人數，0=自動偵測（僅 diarize 模式有效）')
    parser.add_argument('--dest', default=None,
                        help='轉完後搬移目的地資料夾（省略則互動輸入）')
    parser.add_argument('--categories', default=None,
                        help='要套用的詞庫類別，逗號分隔（例如 AI,字幕工具）；省略=全部類別')
    parser.add_argument('--vocab', default=None,
                        help='術語檔（純文字），內容會抽成 Whisper prompt 專名清單；'
                             '省略則自動找同目錄 <檔名>_terms.txt 或 meeting_saved*.txt')
    args = parser.parse_args()

    global VOCAB_FILE, CATEGORIES
    VOCAB_FILE = args.vocab
    CATEGORIES = [x.strip() for x in args.categories.split(',') if x.strip()]         if args.categories else None

    if args.path:
        input_path = args.path
        dest_path = args.dest
    else:
        raw = input(f"請貼上資料夾路徑（直接按 Enter 使用預設 {DEFAULT_FOLDER}）：\n> ").strip()
        input_path = raw if raw else DEFAULT_FOLDER

        raw2 = input(f"目的地資料夾（轉完後搬移，直接按 Enter 使用預設 {DEFAULT_DONE_FOLDER}）：\n> ").strip()
        dest_path = raw2 if raw2 else DEFAULT_DONE_FOLDER

    target = Path(input_path)

    if not target.exists():
        print(f"找不到：{target}")
        sys.exit(1)

    def run(f):
        if args.mode == 'diarize':
            process_file_diarize(f, args.speakers)
        else:
            process_file(f)

    if target.is_dir():
        files = sorted(f for f in target.iterdir() if f.suffix.lower() in SUPPORTED)
        if not files:
            print(f"資料夾內沒有音檔（支援：{', '.join(SUPPORTED)}）")
            sys.exit(1)
        mode_label = '說話者辨識' if args.mode == 'diarize' else '標準'
        print(f"找到 {len(files)} 個音檔，模式：{mode_label}，開始批次轉錄\n")
        for i, f in enumerate(files, 1):
            print(f"[{i}/{len(files)}] {f.name}")
            run(f)
        print(f"\n全部完成，逐字稿存在：{target}")

        if dest_path:
            dest = Path(dest_path)
            dest.mkdir(parents=True, exist_ok=True)
            print(f"\n搬移檔案到：{dest}")
            for f in files:
                for candidate in [
                    f,
                    f.parent / f"{f.stem}_逐字稿.md",
                    f.parent / f"{f.stem}_逐字稿.srt",
                ]:
                    if candidate.exists():
                        shutil.move(str(candidate), str(dest / candidate.name))
                        print(f"  ✓ {candidate.name}")
            print("搬移完成。")

        print(f"\n下一步：告訴 Claude「用 transcript-training-pack 處理 {dest_path or target}\\<檔名>_逐字稿.md」")
    else:
        if target.suffix.lower() not in SUPPORTED:
            print(f"不支援的格式：{target.suffix}，支援：{', '.join(SUPPORTED)}")
            sys.exit(1)
        print(f"[1/1] {target.name}")
        run(target)

if __name__ == "__main__":
    main()
