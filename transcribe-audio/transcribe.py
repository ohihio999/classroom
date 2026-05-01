import os
import sys
import subprocess
import tempfile
from pathlib import Path
from groq import Groq

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SUPPORTED = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm"}
SIZE_LIMIT_MB = 25

def compress(audio_path: Path) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    compressed = Path(tmp.name)
    print(f"  檔案 {audio_path.stat().st_size / 1024 / 1024:.1f} MB，自動壓縮中...")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio_path),
         "-ac", "1", "-ar", "16000", "-b:a", "32k", str(compressed)],
        check=True, capture_output=True
    )
    print(f"  壓縮完成：{compressed.stat().st_size / 1024 / 1024:.1f} MB")
    return compressed

def transcribe(audio_path: Path) -> str:
    with open(audio_path, "rb") as f:
        result = client.audio.transcriptions.create(
            file=(audio_path.name, f.read()),
            model="whisper-large-v3",
            language="zh",
            response_format="text",
        )
    return result

def process_file(audio_path: Path):
    output_path = audio_path.parent / f"{audio_path.stem}_逐字稿.md"
    if output_path.exists():
        print(f"  跳過（逐字稿已存在）：{output_path.name}")
        return

    tmp_file = None
    work_path = audio_path

    if audio_path.stat().st_size / 1024 / 1024 > SIZE_LIMIT_MB:
        work_path = compress(audio_path)
        tmp_file = work_path

    try:
        print(f"  轉錄中...")
        text = transcribe(work_path)
    finally:
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# {audio_path.stem} 逐字稿\n\n")
        f.write(text)

    print(f"  完成：{output_path.name}")

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
