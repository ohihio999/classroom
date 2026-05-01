import os
import sys
import re
import subprocess
import tempfile
import time
import shutil
from pathlib import Path
from groq import Groq
try:
    import opencc
except ImportError:
    import subprocess as _sp
    _sp.run([sys.executable, "-m", "pip", "install", "opencc-python-reimplemented", "-q"], check=True)
    import opencc

FFMPEG = os.environ.get("FFMPEG_PATH") or "ffmpeg"
for _candidate in [
    r"C:\Users\admin\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin\ffmpeg.exe",
]:
    if Path(_candidate).exists():
        FFMPEG = _candidate
        break

client = Groq(api_key=os.environ.get("GROQ_API_KEY"), timeout=120.0)
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


def transcribe_chunk(audio_path: Path, index: int, total: int) -> list[dict]:
    """回傳 segments list，每項含 start/end（秒）和 text"""
    print(f"    段落 {index}/{total} 轉錄中...")
    for attempt in range(3):
        try:
            with open(audio_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    file=(audio_path.name, f.read()),
                    model="whisper-large-v3",
                    language="zh",
                    response_format="verbose_json",
                )
            return result.segments
        except Exception as e:
            if attempt < 2:
                print(f"    重試（{attempt+1}/3）：{e}")
                time.sleep(5)
            else:
                raise


def process_file(audio_path: Path):
    output_md = audio_path.parent / f"{audio_path.stem}_逐字稿.md"
    output_srt = audio_path.parent / f"{audio_path.stem}_逐字稿.srt"
    if output_md.exists():
        print(f"  跳過（逐字稿已存在）：{output_md.name}")
        return

    print(f"  檔案大小：{audio_path.stat().st_size / 1024 / 1024:.1f} MB")

    tmp_compressed = None
    tmp_dir = None
    try:
        compressed = compress(audio_path)
        tmp_compressed = compressed

        chunks = split_audio(compressed)
        tmp_dir = chunks[0].parent if chunks else None

        all_blocks: list[dict] = []
        for i, chunk in enumerate(chunks, 1):
            offset_sec = (i - 1) * CHUNK_SECONDS
            segments = transcribe_chunk(chunk, i, len(chunks))
            for seg in segments:
                s = seg if isinstance(seg, dict) else vars(seg)
                all_blocks.append({
                    'start': ms_to_time(int((s['start'] + offset_sec) * 1000)),
                    'end':   ms_to_time(int((s['end']   + offset_sec) * 1000)),
                    'text':  s['text'].strip(),
                })

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

    print(f"  完成：{output_md.name}  +  {output_srt.name}")


def main():
    if len(sys.argv) < 2:
        print("用法：python transcribe.py <資料夾路徑 或 音檔路徑>")
        print("範例：python transcribe.py C:\\OBS")
        sys.exit(1)

    target = Path(sys.argv[1])

    if not target.exists():
        print(f"找不到：{target}")
        sys.exit(1)

    if target.is_dir():
        files = sorted(f for f in target.iterdir() if f.suffix.lower() in SUPPORTED)
        if not files:
            print(f"資料夾內沒有音檔（支援：{', '.join(SUPPORTED)}）")
            sys.exit(1)
        print(f"找到 {len(files)} 個音檔，開始批次轉錄\n")
        for i, f in enumerate(files, 1):
            print(f"[{i}/{len(files)}] {f.name}")
            process_file(f)
        print(f"\n全部完成，逐字稿存在：{target}")
        print(f"\n下一步：告訴 Claude「用 transcript-training-pack 處理 {target}\\<檔名>_逐字稿.md」")
    else:
        if target.suffix.lower() not in SUPPORTED:
            print(f"不支援的格式：{target.suffix}，支援：{', '.join(SUPPORTED)}")
            sys.exit(1)
        print(f"[1/1] {target.name}")
        process_file(target)

if __name__ == "__main__":
    main()
