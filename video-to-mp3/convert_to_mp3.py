# v1.1 2026-07-31 轉檔時顯示即時進度條（ffprobe 取總長度 + 解析 ffmpeg -progress），並顯示第幾支/共幾支
# v1.0 初版：批次把資料夾內影片轉成 MP3，無進度顯示
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".m4v",
    ".flv",
    ".wmv",
}

BAR_WIDTH = 30


def find_videos(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def get_duration(video_path: Path) -> float:
    """用 ffprobe 取影片總長度（秒）。取不到就回 0，進度條改成只顯示已處理時間。"""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def format_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def parse_out_time(value: str) -> float:
    """把 ffmpeg 的 out_time=00:01:23.456789 轉成秒。"""
    try:
        hours, minutes, secs = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(secs)
    except ValueError:
        return 0.0


def draw_bar(current: float, duration: float) -> str:
    if duration > 0:
        ratio = min(current / duration, 1.0)
        filled = int(BAR_WIDTH * ratio)
        bar = "█" * filled + " " * (BAR_WIDTH - filled)
        return f"  [{bar}] {ratio * 100:5.1f}%  {format_time(current)} / {format_time(duration)}"
    return f"  已處理 {format_time(current)}"


def convert_video(video_path: Path, index: int, total: int) -> bool:
    output_path = video_path.with_suffix(".mp3")
    duration = get_duration(video_path)

    print(f"[轉檔 {index}/{total}] {video_path.name} -> {output_path.name}")
    if duration > 0:
        print(f"  長度 {format_time(duration)}")

    cmd = [
        "ffmpeg",
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-ab",
        "192k",
        str(output_path),
    ]

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=err_file,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        for line in process.stdout:
            line = line.strip()
            if line.startswith("out_time="):
                current = parse_out_time(line.split("=", 1)[1])
                sys.stdout.write("\r" + draw_bar(current, duration))
                sys.stdout.flush()

        process.wait()

        if duration > 0:
            sys.stdout.write("\r" + draw_bar(duration, duration))
        sys.stdout.write("\n")
        sys.stdout.flush()

        if process.returncode == 0:
            print(f"[完成] {output_path}")
            return True

        print(f"[失敗] {video_path.name}")
        err_file.seek(0)
        stderr_text = err_file.read().strip()
        if stderr_text:
            print(stderr_text)
        return False


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="批次把資料夾內所有影片檔轉成同資料夾 MP3")
    parser.add_argument("folder", help="影片檔所在資料夾，例如 C:\\OBS\\影片轉mp3")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser()
    if not folder.exists() or not folder.is_dir():
        print(f"[錯誤] 找不到資料夾：{folder}")
        return 1

    print(f"[掃描] {folder}")
    videos = find_videos(folder)
    if not videos:
        print("[提示] 這個資料夾內沒有可處理的影片檔。")
        return 0

    print(f"[待轉] 共 {len(videos)} 支影片")
    print("")

    converted = 0
    failed = 0
    for index, video in enumerate(videos, start=1):
        if convert_video(video, index, len(videos)):
            converted += 1
        else:
            failed += 1
        print("")

    print(f"[摘要] 成功 {converted} 支，失敗 {failed} 支")
    print(f"[位置] 所有 MP3 都已輸出到原本影片所在資料夾：{folder}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FileNotFoundError:
        print("[錯誤] 找不到 ffmpeg。請先安裝 ffmpeg，或確認它已加入 PATH。")
        raise SystemExit(1)
