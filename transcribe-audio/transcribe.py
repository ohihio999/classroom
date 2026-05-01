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

FFMPEG = os.environ.get("FFMPEG_PATH") or "ffmpeg"
for _candidate in [
    r"C:\Users\admin\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe",
]:
    if Path(_candidate).exists():
        FFMPEG = _candidate
        break

def _load_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        try:
            import keyring
            key = keyring.get_password("groq", "api_key") or ""
        except Exception:
            pass
    if not key:
        raise SystemExit(
            "找不到 GROQ API Key。請執行一次：\n"
            "  pip install keyring\n"
            "  python -c \"import keyring; keyring.set_password('groq', 'api_key', '你的KEY')\"\n"
            "之後就不需要再設定。"
        )
    return key

client = Groq(api_key=_load_api_key(), timeout=120.0)
_cc = opencc.OpenCC('s2twp')

SUPPORTED = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm"}
CHUNK_SECONDS = 300


def compress(audio_path: Path) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    compressed = Path(tmp.name)
    print(f"  壓縮中（mono 16kHz 32k）...")
    subprocess.run(
        [FFMPEG, "-y", "-i", str(audio_path),
         "-ac", "1", "-ar", "16000", "-b:a", "32k", str(compressed)],
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


def transcribe_chunk(audio_path: Path, index: int, total: int) -> list[dict]:
    """回傳 segments list，每項含 start/end（秒）和 text"""
    print(f"    段落 {index}/{total} 轉錄中...")
    for attempt in range(5):
        try:
            with open(audio_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    file=(audio_path.name, f.read()),
                    model="whisper-large-v3",
                    language="zh",
                    response_format="verbose_json",
                )
            return result.segments
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

    print(f"  上傳至 AssemblyAI（說話者辨識模式）...")
    aai.settings.api_key = key
    config = aai.TranscriptionConfig(
        language_code="zh",
        speaker_labels=True,
        speakers_expected=speakers if speakers > 0 else None
    )
    transcript = aai.Transcriber().transcribe(str(audio_path), config)

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

        all_blocks: list[dict] = []
        for i, chunk in enumerate(chunks, 1):
            cache_file = cache_dir / f"chunk_{i:03d}.json"
            offset_sec = (i - 1) * CHUNK_SECONDS

            if cache_file.exists():
                print(f"    段落 {i}/{len(chunks)} 從暫存載入（略過 API 呼叫）")
                blocks = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                segments = transcribe_chunk(chunk, i, len(chunks))
                blocks = []
                for seg in segments:
                    s = seg if isinstance(seg, dict) else vars(seg)
                    blocks.append({
                        'start': ms_to_time(int((s['start'] + offset_sec) * 1000)),
                        'end':   ms_to_time(int((s['end']   + offset_sec) * 1000)),
                        'text':  s['text'].strip(),
                    })
                cache_file.write_text(json.dumps(blocks, ensure_ascii=False), encoding="utf-8")

            all_blocks.extend(blocks)

    finally:
        if tmp_compressed and tmp_compressed.exists():
            tmp_compressed.unlink()
        if tmp_dir and tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # 簡體 → 繁體
    for b in all_blocks:
        b['text'] = _cc.convert(b['text'])

    with open(output_srt, "w", encoding="utf-8") as f:
        f.write(blocks_to_srt(all_blocks))

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
    args = parser.parse_args()

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
