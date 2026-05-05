import argparse
import subprocess
import sys
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


def find_videos(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )


def convert_video(video_path: Path) -> bool:
    output_path = video_path.with_suffix(".mp3")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "libmp3lame",
        "-ab",
        "192k",
        str(output_path),
    ]
    print(f"[轉檔] {video_path.name} -> {output_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"[完成] {output_path}")
        return True

    print(f"[失敗] {video_path.name}")
    if result.stderr:
        print(result.stderr.strip())
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="批次把資料夾內所有影片檔轉成同資料夾 MP3")
    parser.add_argument("folder", help="影片檔所在資料夾，例如 C:\\OBS")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser()
    if not folder.exists() or not folder.is_dir():
        print(f"[錯誤] 找不到資料夾：{folder}")
        return 1

    videos = find_videos(folder)
    if not videos:
        print("[提示] 這個資料夾內沒有可處理的影片檔。")
        return 0

    converted = 0
    failed = 0
    for video in videos:
        if convert_video(video):
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
