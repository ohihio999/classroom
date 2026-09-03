"""
影音工具本機服務（YouTube 下載 / 影片轉 MP3 / 錄音檔合併 / 圖檔轉 PDF）

v1.11 2026-08-27 archive 的 vault 副本條件改成收「courseDir 底下全部 .md（含子資料夾，
                 保留相對路徑）」，原本只收第一層。使用者會在課程資料夾自建「付費」「網站轉高級」
                 之類的子資料夾放 MD，壓平到根層會失去分類。
v1.10 2026-08-26 課程資料夾不再出現雙日期：課程名本身已經以合法日期開頭時（資料夾批次
                 直接拿原檔名當課程名，而檔名常見 20260826_主題.mp4）就沿用該名稱，
                 不再前綴今天的日期。沿用而非改成今天，是因為檔名裡的日期通常才是上課日。
v1.9 2026-08-26 課程整理SOP 兩項修改（使用者拷問後定案）：
                 (1) 建任務當下就把本機來源影片／錄音「剪」進 courseDir 並改名成課程名
                     （分段來源給 _01、_02 序號），不再等 AI 到 acquisition 才搬。
                     搬不動就全有全無回滾：已搬的放回原位、剛建的課程資料夾刪掉、建立失敗；
                     資料夾批次時連同已建好的其他堂課一起撤回。來源資料夾本身與非媒體檔不碰。
                     manifest 的 source 改記新位置，另存 originalValue 與 movedFiles 供追溯，
                     preserveOriginal 只剩 YouTube 為真。
                 (2) archive 驗收條件加一條：courseDir 第一層全部 .md 要複製一份到
                     D:\\本機MD檔\\30_研究\\課程逐字稿整理\\<與課程資料夾同名>\\
                     （沒有就建；同名跳過不覆蓋；HTML／srt／媒體不進 vault）。
v1.8 2026-08-26 首頁新增第二個分頁「字幕詞庫」（hash 入口 #lexicon）：用 iframe 內嵌既有的
                 /lexicon 管理頁（lexicon_admin.py 不動，該網址仍可單獨開）。iframe 等第一次
                 點分頁才給 src，首頁載入不多打一次請求；切走再切回保留頁面狀態。
v1.7 2026-08-26 課程流水線的「含教學 / 含最小案例」改成跟在「🌳 技能樹」勾選框後面同一列
                 顯示（原本落在下方獨立子區塊，離技能樹太遠看不出從屬關係）。
v1.6 2026-08-24 「圖檔轉 PDF」的資料夾／PDF 名稱可以留空自動命名：ffmpeg 把第一張圖縮成
                 1024px JPEG 交給 Groq 視覺模型讀封面書名（認不出來往後看，最多 5 張），
                 再拿去博客來搜尋校正成正式書名，最後命名成「主書名 - 作者」（副標不進檔名，
                 免得幾百張圖的深路徑撞 Windows 長度上限）。認不出來就中止並要求手填，不亂猜。
                 key 先用網頁登入後傳來的，被 Groq 回 401 就退回環境變數 GROQ_API_KEY
                 （實際踩到：Firebase Secret 那把久沒用被 Groq 自動過期，本機那把才是新的）。
                 另外兩個實測到的坑：Groq 前面有 Cloudflare，Python-urllib 的預設 UA 會被擋成
                 403 error 1010，所以每個請求都自報 UA；免費層 TPM 只有 8000 而一張圖約 4000
                 token，看兩張就 429，因此撞到限流會照 retry-after 等待再重試。
                 這個帳號可用的視覺模型實測只有 qwen/qwen3.6-27b（llama-4 系列 model_not_found），
                 它會先吐 <think> 段落，解析 JSON 前要先剝掉。
v1.5 2026-08-19 課程批次來源新增第一層媒體檔預覽，選資料夾或手填路徑後即可確認清單。
v1.4 2026-08-19 課程流水線的資料夾來源改收支援的音訊與影片；m4a 可直接轉錄，
                 不再把產出 MP3 當成逐字稿的強制前置；#course 入口延後到狀態初始化完成。
v1.2 2026-08-08 新增「圖檔轉 PDF」：把原本只有 SKILL.md（給 AI 讀的 SOP）的 image-to-pdf
                 實作成真的程式，讓 /admin/ 卡片能直接跑。選圖走本機 tkinter 選檔框
                 （瀏覽器的 file input 拿不到完整路徑，一定得由本機開）；PDF 分批合成
                 再合併，避免幾百張大圖一次吃爆記憶體。
v1.1 2026-08-01 新增 YouTube 下載：貼網址、MP4 / MP3 可單選也可複選（勾兩個就下載兩份），
                 用 yt-dlp 存到 C:\\OBS。同時開放 CORS，讓 Firebase 上的 /admin/ 卡片
                 能直接打這個服務的 API（瀏覽器把 127.0.0.1 視為安全來源）。
v1.0 2026-07-31 初版：把 C:\\OBS 的 影片轉mp3.bat、錄音黨合併.bat、merge.ps1、convert_to_mp3.py
                 整合成單一本機服務，網頁 UI 內嵌在本檔，port 8767。
                 網頁操作、本機 ffmpeg 處理、檔案不上傳、輸出回原資料夾。

用法：雙擊 start.bat，或 python server.py
"""

import base64
import difflib
import html as html_lib
import json
import os
import re
import shutil
import socket
import string
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

# 詞庫管理與校正比對後台（獨立模組，載不到不影響既有功能）
try:
    import lexicon_admin as LEXADMIN
except Exception as _lex_err:
    LEXADMIN = None
    print(f'[warn] 詞庫後台未載入：{_lex_err}')

PORT = 8767
SERVICE_VERSION = "1.6"
DEFAULT_VIDEO_DIR = r"C:\OBS\影片轉mp3"
DEFAULT_AUDIO_DIR = r"C:\OBS\影片檔合併"
DEFAULT_YTDL_DIR = r"C:\OBS"
# 2026-08-19 使用者裁定：課程產物一律不進 vault（媒體檔會撐大 vault 的 git 快照）。
# 這是預設值，網頁上的「課程歸檔根目錄」可以隨時改成別的位置。
COURSE_ROOT = r"D:\2026_已整理課程"
# 課程包的 Markdown 副本要複製進 Obsidian vault 的這裡（archive 階段由 AI 執行）。
VAULT_COURSE_MD_ROOT = r"D:\本機MD檔\30_研究\課程逐字稿整理"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv", ".ts"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".opus", ".wma"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
COURSE_SOURCE_TYPES = {"youtube", "local_video", "local_mp3", "mp3_folder", "mp3_parts"}

# 課程來源的唯一 canonical 副檔名集合。transcribe.py SUPPORTED 已確認為
# .mp3/.mp4/.wav/.m4a/.ogg/.webm，再納入本服務既有的其他影片格式；不任意擴大
# 到 ffmpeg 可能讀取的所有格式。
COURSE_MEDIA_EXTENSIONS = {
    ".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm",
} | VIDEO_EXTENSIONS

# mp3_parts＝同一堂課的多個媒體檔；mp3_folder＝資料夾內每個媒體檔各自是一堂獨立的課。
MULTI_PART_SOURCE = "mp3_parts"
PIPELINE_STATUSES = {"pending", "in_progress", "completed", "blocked", "skipped", "cancelled"}


def sanitize_course_name(name: str) -> str:
    """移除 Windows 不合法字元並收斂空白，不允許空課程名。"""
    clean = re.sub(r'[<>:"/\\|?*]', " ", str(name or ""))
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    if not clean:
        raise ValueError("請填寫有效的課程名稱")
    return clean


def validate_course_source(source_type: str, value: str) -> bool:
    """驗證課程來源；本機來源必須真的存在。"""
    if source_type not in COURSE_SOURCE_TYPES:
        return False
    value = str(value or "").strip()
    if source_type == "youtube":
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        return parsed.scheme in {"http", "https"} and (
            host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
        )
    path = Path(value)
    if source_type == "local_video":
        return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    if source_type == "local_mp3":
        # legacy key 不改名：現在代表可直接進課程流水線的單一媒體檔。
        return path.is_file() and path.suffix.lower() in COURSE_MEDIA_EXTENSIONS
    if source_type == MULTI_PART_SOURCE:
        # 分段媒體整個資料夾算「一堂課」；非支援檔案不納入。
        return path.is_dir() and bool(list_batch_media(path))
    if source_type == "mp3_folder":
        # 資料夾批次：每個媒體檔各一堂課，音訊影片都收
        return path.is_dir() and bool(list_batch_media(path))
    return False


# 課程名開頭已經是一個合法日期（常見於檔名 20260826_主題.mp4）時就沿用它，
# 不再前綴今天，否則資料夾會變成 20260826_20260826_主題（2026-08-26 使用者實際踩到）。
# 沿用而不是換成今天：檔名裡那個日期通常才是上課日，不一定等於整理日。
LEADING_DATE_RE = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[_\-\s]|$)")


def course_folder_name(clean_name: str) -> str:
    """課程資料夾名：`YYYYMMDD_課程名`；課程名自己已帶日期就不重複加。"""
    if LEADING_DATE_RE.match(clean_name):
        return clean_name
    return f"{datetime.now().strftime('%Y%m%d')}_{clean_name}"


def _pipeline_stage(status: str, criterion: str) -> dict:
    if status not in PIPELINE_STATUSES:
        raise ValueError(f"不合法的 stage 狀態：{status}")
    return {
        "status": status,
        "completion_criteria": criterion,
        "evidence": [],
        "outputs": [],
        "error": "",
    }


ARTIFACT_KEYS = ("video", "mp3", "transcript", "review", "rawSegments",
                 "summary", "report", "mindmap", "skillTree")

# yt-dlp 的畫質選項；best＝不限制。
VIDEO_QUALITIES = ("best", "1080p", "720p", "480p")

# 轉錄引擎。auto＝照 skill 優先序（作者上傳字幕 → Groq → 本機 whisper → 網頁 → 自動字幕）。
# 費用立場見 institution preferences 8.2：只用訂閱額度與免費額度。
# auto 模式的候選鏈，使用者可自訂順序與開關。
# 付費引擎（assemblyai）刻意不在此列：要花錢的只能明確指定，不得自動 fallback 進來。
ENGINE_CHAIN = ("subtitle_manual", "groq", "local_whisper", "web", "subtitle_auto")
# 使用者 2026-08-18 裁定：預設只走作者上傳字幕與 Groq，
# 本機 whisper（很慢）、網頁自動化（脆弱）、自動生成字幕（品質差）要自己勾。
DEFAULT_ENGINE_PRIORITY = ["subtitle_manual", "groq"]

TRANSCRIPT_ENGINES = {
    "auto": "照使用者排定的優先序自動選擇",
    "subtitle_only": "只用現成字幕，沒有就失敗",
    "groq": "Groq whisper-large-v3，免費額度，有 SRT 時間軸",
    "assemblyai": "AssemblyAI 說話者辨識（付費，一次性 $50 免費額度用完後計費），無 SRT",
    "local_whisper": "本機 whisper CLI，不花額度但很慢",
}

# 沒有指定 artifacts 時的預設＝改版前的固定行為：課程包五份全做、技能樹不做。
ARTIFACT_DEFAULTS = {
    # YouTube 來源預設保留 MP4（維持改版前行為）；本機來源會自動關掉。
    "video": True,
    "mp3": True, "transcript": True, "summary": True,
    "report": True, "mindmap": True, "skillTree": False,
    # 校對要花 agy 額度，預設不做；重要課程才勾。
    "review": False,
    # 原字分段版是額外客製檔，skill 要求「使用者明確要求」才產。
    "rawSegments": False,
}

# 下游產物 → 需要的前置產物。勾了下游就自動把前置補上。
ARTIFACT_REQUIRES = {
    "summary": "transcript",
    "report": "transcript",
    "mindmap": "transcript",
    "skillTree": "transcript",
    "review": "transcript",
    "rawSegments": "transcript",
}


def resolve_course_artifacts(supplied: dict, source_type: str, skill_mode: int,
                             source_val: str = "") -> dict:
    """補齊產物相依；只在實際來源全為 MP3 時關掉無事可做的轉檔階段。"""
    raw = supplied.get("artifacts")
    if raw is None:
        artifacts = dict(ARTIFACT_DEFAULTS)
        # 舊呼叫端只給 skillTreeMode，沒有 artifacts；用 mode 回推技能樹。
        artifacts["skillTree"] = skill_mode > 0
    else:
        if not isinstance(raw, dict):
            raise ValueError("artifacts 必須是物件")
        unknown = set(raw) - set(ARTIFACT_KEYS)
        if unknown:
            raise ValueError(f"不認得的產物項目：{'、'.join(sorted(unknown))}")
        artifacts = {k: bool(raw.get(k, False)) for k in ARTIFACT_KEYS}

    if artifacts["skillTree"] and skill_mode == 0:
        raise ValueError("勾選技能樹時 skillTreeMode 不能是 0")
    if not artifacts["skillTree"] and skill_mode > 0:
        artifacts["skillTree"] = True

    if not any(artifacts.values()):
        raise ValueError("至少要勾選一項產物")

    # 相依自動補齊：下游課程包 → 逐字稿。transcribe.py 可直接讀取支援的媒體，
    # 所以逐字稿不依賴「產出 MP3」。
    for downstream, prerequisite in ARTIFACT_REQUIRES.items():
        if artifacts[downstream]:
            artifacts[prerequisite] = True

    # 只有 YouTube 需要「下載影片」；本機來源的影片本來就在手上。
    if source_type != "youtube":
        artifacts["video"] = False

    # 只有單檔是 MP3，或資料夾內全部支援媒體都是 MP3，才沒有轉檔可做。
    source_path = Path(str(source_val or ""))
    if source_type != "youtube" and source_path.is_file():
        all_sources_are_mp3 = source_path.suffix.lower() == ".mp3"
    elif source_type in {"mp3_folder", MULTI_PART_SOURCE} and source_path.is_dir():
        source_media = list_batch_media(source_path)
        all_sources_are_mp3 = bool(source_media) and all(
            item.suffix.lower() == ".mp3" for item in source_media)
    else:
        all_sources_are_mp3 = False
    if all_sources_are_mp3:
        artifacts["mp3"] = False

    return artifacts


def _stage_for(selected: bool, criterion: str) -> dict:
    """有勾選的 stage 是 pending，沒勾的直接標 skipped。"""
    return _pipeline_stage("pending" if selected else "skipped", criterion)


# 2026-08-26 使用者裁定：建任務當下就把本機來源媒體「剪」進 courseDir 並改名成課程名，
# 不再等 AI 到 acquisition 才搬（舊做法常忘了搬，archive 因為「產物不在 courseDir」卡住）。
# 這同時推翻 course-content-pipeline SKILL.md 2026-08-20 的「只搬不改名」。
def plan_source_moves(source_type: str, source_val: str, clean_name: str) -> list:
    """算出 [(來源檔, courseDir 內的目標檔名)]；YouTube 沒有本機原檔，回空清單。"""
    if source_type == "youtube":
        return []
    src = Path(source_val)
    if source_type == MULTI_PART_SOURCE:
        # 分段是同一堂課：依檔名排序後編號，維持段序可讀。
        return [(item, f"{clean_name}_{index:02d}{item.suffix}")
                for index, item in enumerate(list_batch_media(src), start=1)]
    return [(src, f"{clean_name}{src.suffix}")]


def _restore_moved(moved: list) -> list:
    """把搬過的檔案放回原位；回傳沒能放回去的目標路徑。"""
    failed = []
    for record in reversed(moved):
        origin, target = Path(record["from"]), Path(record["to"])
        try:
            if target.exists():
                origin.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(target), str(origin))
        except OSError:
            failed.append(record["to"])
    return failed


def move_source_into_course_dir(source_type: str, source_val: str,
                                course_dir: Path, clean_name: str) -> tuple:
    """把本機來源媒體搬進 courseDir 並改名；回傳 (新的 source value, 搬移紀錄)。

    全有全無：任何一個檔搬不動（被播放器鎖住、空間不足、權限不足），
    就把這一輪已搬的檔案全部放回原位再拋錯，不留半殘的課程資料夾。
    來源資料夾本身與其中的非媒體檔一律不動。
    """
    pairs = plan_source_moves(source_type, source_val, clean_name)
    if not pairs:
        return source_val, []

    moved = []
    try:
        for origin, target_name in pairs:
            target = course_dir / target_name
            if target.exists():
                raise ValueError(f"課程資料夾已經有同名檔案：{target_name}")
            shutil.move(str(origin), str(target))
            moved.append({"from": str(origin), "to": str(target)})
    except (OSError, ValueError) as exc:
        leftovers = _restore_moved(moved)
        message = f"來源檔搬移失敗：{exc}；已建立的課程資料夾與搬移全部取消"
        if leftovers:
            message += "；下列檔案沒能放回原位，請手動處理：" + "、".join(leftovers)
        raise ValueError(message) from exc

    # 分段來源整批進 courseDir，來源改指資料夾本身；單檔則指向搬完的那個檔。
    new_value = str(course_dir) if source_type == MULTI_PART_SOURCE else moved[0]["to"]
    return new_value, moved


def rollback_course_manifest(manifest: dict) -> list:
    """撤銷一份已建立的課程：搬回原檔、刪掉 manifest 與空的課程資料夾。

    回傳沒能放回原位的檔案清單。資料夾若還有殘留檔案就保留不刪。
    """
    moved = manifest.get("source", {}).get("movedFiles", []) or []
    failed = _restore_moved(moved)
    course_dir = Path(manifest.get("courseDir", ""))
    try:
        (course_dir / "course-manifest.json").unlink(missing_ok=True)
        course_dir.rmdir()
    except OSError:
        pass
    return failed


def create_course_manifest(source_type: str, source_val: str, course_name: str,
                           options: dict | None = None, output_root: str | None = None) -> tuple:
    """建立不覆蓋既有資料夾的 course-manifest.json，供 AI 跨 Session 續跑。"""
    source_val = str(source_val or "").strip()
    if not validate_course_source(source_type, source_val):
        raise ValueError("課程來源無效或不存在")
    clean_name = sanitize_course_name(course_name)
    supplied = dict(options or {})
    skill_mode = int(supplied.get("skillTreeMode", 0) or 0)
    if skill_mode not in {0, 1, 2, 3}:
        raise ValueError("skillTreeMode 只能是 0、1、2、3")
    artifacts = resolve_course_artifacts(supplied, source_type, skill_mode, source_val)
    engine = str(supplied.get("transcriptEngine", "auto") or "auto")
    if engine not in TRANSCRIPT_ENGINES:
        raise ValueError(f"不支援的轉錄引擎：{engine}")
    if engine == "subtitle_only" and source_type != "youtube":
        raise ValueError("只有 YouTube 來源可能有現成字幕，本機來源請改選其他引擎")
    quality = str(supplied.get("videoQuality", "best") or "best")
    if quality not in VIDEO_QUALITIES:
        raise ValueError(f"不支援的影片畫質：{quality}")
    summary_style = str(supplied.get("summaryStyle", "standard") or "standard")
    if summary_style not in {"standard", "dense"}:
        raise ValueError("summaryStyle 只能是 standard 或 dense")
    priority = supplied.get("enginePriority")
    if priority is None:
        priority = list(DEFAULT_ENGINE_PRIORITY)
    else:
        if not isinstance(priority, list) or not priority:
            raise ValueError("enginePriority 至少要留一個引擎")
        unknown = [e for e in priority if e not in ENGINE_CHAIN]
        if unknown:
            paid = [e for e in unknown if e in TRANSCRIPT_ENGINES]
            if paid:
                raise ValueError(
                    f"{'、'.join(paid)} 要付費，不能放進自動嘗試順序；請在轉錄引擎直接指定")
            raise ValueError(f"不認得的引擎：{'、'.join(unknown)}")
        if len(set(priority)) != len(priority):
            raise ValueError("enginePriority 有重複項目")
    root = Path(output_root or COURSE_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    base_name = course_folder_name(clean_name)
    course_dir = root / base_name
    suffix = 2
    while course_dir.exists():
        course_dir = root / f"{base_name}-{suffix}"
        suffix += 1
    course_dir.mkdir(parents=False)

    # 建完資料夾立刻把本機原檔剪進來；搬不動就連同剛建的空資料夾一起收掉。
    try:
        moved_value, moved_files = move_source_into_course_dir(
            source_type, source_val, course_dir, clean_name)
    except ValueError:
        try:
            course_dir.rmdir()
        except OSError:
            pass
        raise

    # 課程包的 MD 副本落點（2026-08-26 使用者裁定）：一堂課一個同名子資料夾。
    vault_md_dir = Path(VAULT_COURSE_MD_ROOT) / course_dir.name

    is_youtube = source_type == "youtube"
    is_multi_part = source_type == MULTI_PART_SOURCE
    if is_youtube:
        want_video = artifacts["video"]
        quality_text = "最佳畫質" if quality == "best" else quality
        acquisition_criterion = (
            "來源媒體存在且非空；YouTube 另有 "
            + (f"MP4（{quality_text}）、" if want_video else "")
            + "MP3、metadata 與字幕／raw transcript"
        )
    elif is_multi_part:
        acquisition_criterion = (
            "分段媒體已由 8767 剪進 courseDir 並依檔名排序改名為 "
            f"{clean_name}_01、{clean_name}_02…，每段 size > 0；"
            "這些是同一堂課的分段，來源資料夾保持原狀不得刪除"
        )
    else:
        acquisition_criterion = (
            "本機原檔已由 8767 剪進 courseDir 並改名為 "
            f"{clean_name}<原副檔名>，size > 0；來源資料夾保持原狀不得刪除"
        )

    transcription_criterion = (
        "每一段各自產出 raw transcript 且非空，並依段序合併成單一份完整逐字稿"
        if is_multi_part
        else "raw transcript 存在且非空，並記錄字幕或轉錄來源"
    )
    engine_criteria = {
        "subtitle_only": "；只用作者上傳字幕，抓不到字幕就標 blocked，不得自行改用轉錄",
        "groq": "；走 transcribe.py＋Groq whisper-large-v3，另需產出 SRT 時間軸",
        "assemblyai": "；走 transcribe.py --mode diarize，輸出以說話者分段，"
                      "此模式不產 SRT 時間軸，並記錄用掉的免費額度",
        "local_whisper": "；走本機 whisper CLI，不消耗任何線上額度",
    }
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    manifest = {
        "schemaVersion": "1.1",
        "id": uuid.uuid4().hex[:12],
        "createdAt": now,
        "updatedAt": now,
        "courseName": clean_name,
        "source": {
            "type": source_type,
            "value": moved_value,
            # 本機來源的原檔已經被剪走，只有 YouTube 還談得上「保留原始來源」。
            "preserveOriginal": is_youtube,
            "originalValue": source_val,
            "movedFiles": moved_files,
        },
        "outputRoot": str(root),
        "courseDir": str(course_dir),
        "options": {
            "artifacts": artifacts,
            "skillTreeMode": skill_mode,
            "minimumExample": skill_mode == 3 or bool(supplied.get("minimumExample", False)),
            "youtubeArtifacts": (
                (["mp4"] if artifacts["video"] else [])
                + ["mp3", "metadata", "subtitle_or_raw_transcript"]
                if is_youtube else []
            ),
            "videoQuality": quality,
            "summaryStyle": summary_style,
            "multiPart": is_multi_part,
            "transcriptEngine": engine,
            "enginePriority": priority,
        },
        "stages": {
            "acquisition": _pipeline_stage("pending", acquisition_criterion),
            "media_to_mp3": _stage_for(
                artifacts["mp3"], "MP3 存在、size > 0、ffprobe duration > 0"),
            "transcription": _stage_for(
                artifacts["transcript"],
                transcription_criterion + engine_criteria.get(engine, "")),
            "transcript_review": _stage_for(
                artifacts["review"],
                "校對版逐字稿存在且非空；raw 逐字稿未被覆蓋；"
                "只修專有名詞、人名與同音誤字，語意與篇幅未被改寫或壓縮"),
            "raw_segments": _stage_for(
                artifacts["rawSegments"],
                "原字分段版存在且非空；保留原句與口語感，只做段落整理，未摘要化改寫"),
            "summary": _stage_for(
                artifacts["summary"],
                "摘要 MD 存在且非空，四個固定章節齊全"
                + ("；摘要總覽採 1-2 段高密度總結寫法"
                   if summary_style == "dense" else "")),
            "training_report": _stage_for(
                artifacts["report"], "培訓報告 MD 存在且非空，六個固定章節齊全"),
            "mindmap": _stage_for(
                artifacts["mindmap"],
                "心智圖 MD 與 Markmap HTML 均存在且非空，HTML 內嵌內容與 MD 一致"),
            "skill_tree": _stage_for(
                artifacts["skillTree"],
                "技能樹存在；模式 3 每個節點另含教學與最小案例"),
            "archive": _pipeline_stage(
                "pending",
                "所有要求產物位於 courseDir，manifest 證據完整；"
                f"並已把 courseDir 底下全部 .md（含子資料夾，保留相對路徑）複製一份到 {vault_md_dir}"
                "（資料夾與子資料夾不存在就建；同名檔一律跳過不覆蓋，並在 evidence 列出跳過清單；"
                "HTML、srt、words.json 與媒體檔不進 vault）"),
        },
    }
    manifest_path = course_dir / "course-manifest.json"
    temp_path = course_dir / ".course-manifest.json.tmp"
    temp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(manifest_path)
    return manifest, manifest_path

BATCH_MEDIA_EXTENSIONS = COURSE_MEDIA_EXTENSIONS


def list_batch_media(folder) -> list:
    """資料夾批次要處理的媒體檔，依檔名排序；非媒體檔忽略。"""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(
        (item for item in folder.iterdir()
         if item.is_file() and item.suffix.lower() in BATCH_MEDIA_EXTENSIONS),
        key=lambda x: x.name,
    )


def preview_course_folder(folder) -> dict:
    """預覽資料夾第一層的支援媒體；子資料夾不列入也不算忽略。"""
    raw_folder = str(folder or "").strip()
    if not raw_folder:
        raise ValueError("請指定要預覽的資料夾")
    path = Path(raw_folder)
    if not path.exists():
        raise ValueError(f"找不到資料夾：{raw_folder}")
    if not path.is_dir():
        raise ValueError(f"不是資料夾：{raw_folder}")

    media = list_batch_media(path)
    ignored_count = sum(
        1 for item in path.iterdir()
        if item.is_file() and item.suffix.lower() not in COURSE_MEDIA_EXTENSIONS
    )
    return {
        "files": [
            {
                "name": item.name,
                "type": "video" if item.suffix.lower() in VIDEO_EXTENSIONS else "audio",
            }
            for item in media
        ],
        "supportedCount": len(media),
        "ignoredCount": ignored_count,
    }


def create_course_batch(source_type: str, source_val: str, course_name: str = "",
                        options: dict | None = None, output_root: str | None = None) -> list:
    """一次建立多份 manifest。

    `mp3_folder`＝資料夾內每個媒體檔各一堂課，回傳 N 份；
    其他來源型別各自只有一堂課（`mp3_parts` 是整個資料夾合成一堂），回傳 1 份。
    整批共用同一組 options（產物勾選、轉錄引擎等）。
    """
    if source_type != "mp3_folder":
        if not str(course_name or "").strip():
            course_name = derive_course_name(source_type, source_val)
        return [create_course_manifest(source_type, source_val, course_name,
                                       options=options, output_root=output_root)]

    media = list_batch_media(source_val)
    if not media:
        raise ValueError("資料夾裡沒有可處理的音訊或影片檔")

    results = []
    for item in media:
        # 逐檔各自成課：型別依副檔名分流；legacy local_mp3 key 承載非影片媒體。
        per_type = "local_video" if item.suffix.lower() in VIDEO_EXTENSIONS else "local_mp3"
        try:
            results.append(create_course_manifest(
                per_type, str(item), item.stem, options=options, output_root=output_root))
        except (ValueError, OSError) as exc:
            # 全有全無：一個檔搬不動，這批已經建好的課程也全部撤回，不留下半批。
            leftovers = []
            for done_manifest, _ in results:
                leftovers += rollback_course_manifest(done_manifest)
            message = (f"批次建立中止於「{item.name}」：{exc}；"
                       f"已建立的 {len(results)} 堂課全部回滾，來源檔已放回原位")
            if leftovers:
                message += "；下列檔案沒能放回原位，請手動處理：" + "、".join(leftovers)
            raise ValueError(message) from exc
    return results


# manifest 的 stage 狀態就是進度來源：AI 每做完一段就原子回寫，
# 網頁只讀不寫，所以關掉網頁、換台電腦、隔天再看都還在。
def scan_course_progress(output_root: str | None = None) -> list:
    """掃描歸檔根目錄下所有 course-manifest.json，算出每堂課的進度。"""
    root = Path(output_root or COURSE_ROOT)
    if not root.is_dir():
        return []
    rows = []
    for manifest_path in sorted(root.glob("*/course-manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            rows.append({
                "courseName": manifest_path.parent.name,
                "manifestPath": str(manifest_path),
                "totalStages": 0, "doneStages": 0, "percent": 0,
                "currentStage": "", "status": "unreadable", "error": str(exc),
            })
            continue

        stages = data.get("stages", {})
        active = [(name, st) for name, st in stages.items()
                  if st.get("status") != "skipped"]
        total = len(active)
        done = sum(1 for _, st in active if st.get("status") == "completed")
        blocked = next((n for n, st in active if st.get("status") == "blocked"), "")
        current = next((n for n, st in active
                        if st.get("status") in ("in_progress", "pending")), "")

        if blocked:
            status = "blocked"
        elif total and done == total:
            status = "completed"
        elif done or any(st.get("status") == "in_progress" for _, st in active):
            status = "running"
        else:
            status = "pending"

        rows.append({
            "courseName": data.get("courseName", manifest_path.parent.name),
            "manifestPath": str(manifest_path),
            "totalStages": total,
            "doneStages": done,
            "percent": round(done / total * 100) if total else 0,
            "currentStage": blocked or current,
            "status": status,
            "updatedAt": data.get("updatedAt", ""),
            "error": next((st.get("error", "") for _, st in active if st.get("error")), ""),
        })
    return rows


# 一批幾張圖合成一個暫存 PDF。637 張 337 MB 的實例一次做會把整份 PDF 堆在記憶體裡，
# 分批之後每批只留幾十 MB，最後才用 pikepdf 串流合併。
IMGPDF_CHUNK = 50

FFMPEG_FALLBACK = (
    r"C:\Users\admin\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
)

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def find_tool(name: str) -> str:
    """先找 PATH，找不到就用 winget 安裝的絕對路徑。"""
    found = shutil.which(name)
    if found:
        return found
    fallback = Path(FFMPEG_FALLBACK) / f"{name}.exe"
    if fallback.exists():
        return str(fallback)
    return name


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")


def ytdl_base() -> list:
    """yt-dlp 的呼叫方式：PATH 有就直接用，沒有就退回同一個 Python 的模組。"""
    found = shutil.which("yt-dlp")
    return [found] if found else [sys.executable, "-m", "yt_dlp"]


def decode_console(data: bytes) -> str:
    """解 CLI 子進程的輸出。

    Windows 上 yt-dlp 依主控台碼頁輸出（本機實測是 cp950），寫死 utf-8 會把
    每個中文字變成 U+FFFD。依序試 utf-8 → cp950 → mbcs，最後才用替換字元。
    """
    if not data:
        return ""
    for enc in ("utf-8", "cp950", "mbcs"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def fetch_youtube_title(url: str) -> str:
    """用本機 yt-dlp 讀影片標題，不下載媒體，也不呼叫外部 AI API。"""
    if not validate_course_source("youtube", url):
        raise ValueError("YouTube 網址無效")
    try:
        result = subprocess.run(
            ytdl_base() + [
                "--no-playlist", "--skip-download", "--no-warnings",
                "--print", "%(title)s", url,
            ],
            capture_output=True,          # 收 bytes，解碼交給 decode_console
            creationflags=NO_WINDOW, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"無法取得 YouTube 影片標題：{exc}") from exc
    stdout = decode_console(result.stdout)
    title = next((line.strip() for line in stdout.splitlines() if line.strip()), "")
    if result.returncode != 0 or not title:
        stderr = decode_console(result.stderr).strip()
        detail = stderr.splitlines()[-1] if stderr else "yt-dlp 沒有回傳標題"
        raise ValueError(f"無法取得 YouTube 影片標題：{detail}")
    return title


def derive_course_name(source_type: str, source: str, youtube_title_loader=None) -> str:
    """由 YouTube 標題、本機檔名或資料夾名推導安全課程名稱。"""
    if source_type not in COURSE_SOURCE_TYPES:
        raise ValueError("不支援的課程來源類型")
    source = str(source or "").strip().rstrip("\\/")
    if source_type == "youtube":
        loader = youtube_title_loader or fetch_youtube_title
        candidate = loader(source)
    elif source_type in {"mp3_folder", MULTI_PART_SOURCE}:
        candidate = Path(source).name
    else:
        candidate = Path(source).stem
    try:
        return sanitize_course_name(candidate)
    except ValueError as exc:
        raise ValueError("無法取得課程名稱，請手動輸入") from exc


# 兩種輸出格式的 yt-dlp 參數；勾兩個就是同一個網址跑兩次，各存一份。
YTDL_FORMATS = {
    "mp4": ["-f", "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b", "--merge-output-format", "mp4"],
    "mp3": ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"],
}


# ---------------------------------------------------------------- 工作管理

class Job:
    """一次轉檔或合併的工作，進度由網頁輪詢 /api/status 讀取。"""

    def __init__(self, kind: str, items: list, output_dir: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind                      # 'convert' | 'merge'
        self.output_dir = output_dir
        self.cancelled = False
        self.done = False
        self.started_at = time.time()
        self.finished_at = None
        self.message = ""
        self.outputs = []
        self.lock = threading.Lock()
        self.items = [self._make_item(entry) for entry in items]

    @staticmethod
    def _make_item(entry) -> dict:
        """轉檔／合併傳進來是檔案路徑字串；YT 下載傳的是 {'url','format'}。"""
        base = {
            "status": "waiting",              # waiting | running | done | failed | cancelled
            "percent": 0.0,
            "duration": 0.0,
            "current": 0.0,
            "error": "",
            "output": "",
        }
        if isinstance(entry, dict):
            fmt = entry.get("format", "mp4")
            return {**base, "name": f"[{fmt.upper()}] {entry['url']}", "path": entry["url"],
                    "url": entry["url"], "format": fmt, "size": 0}
        return {**base, "name": Path(entry).name, "path": entry,
                "size": Path(entry).stat().st_size if Path(entry).exists() else 0}

    def snapshot(self) -> dict:
        with self.lock:
            total = len(self.items) or 1
            finished = sum(1 for i in self.items if i["status"] in ("done", "failed", "cancelled"))
            running = sum(i["percent"] for i in self.items if i["status"] == "running") / 100.0
            overall = min((finished + running) / total * 100.0, 100.0)
            elapsed = (self.finished_at or time.time()) - self.started_at
            return {
                "id": self.id,
                "kind": self.kind,
                "done": self.done,
                "cancelled": self.cancelled,
                "message": self.message,
                "overall": round(overall, 1),
                "elapsed": round(elapsed, 1),
                "outputs": list(self.outputs),
                "outputDir": self.output_dir,
                # imgpdf 自動命名用：網頁要顯示認到的書名與來源
                "targetName": getattr(self, "target_name", ""),
                "identify": getattr(self, "identify", None),
                "items": [dict(i) for i in self.items],
            }


JOBS = {}
JOBS_LOCK = threading.Lock()


def probe_duration(path: str) -> float:
    """用 ffprobe 取媒體長度（秒），取不到回 0。"""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, creationflags=NO_WINDOW,
        )
        return float(result.stdout.strip())
    except (ValueError, OSError):
        return 0.0


TIME_RE = re.compile(r"out_time=(\d+):(\d+):([\d.]+)")


def run_ffmpeg(cmd: list, duration: float, on_progress) -> tuple:
    """跑 ffmpeg 並即時回報進度，回傳 (returncode, stderr_text)。"""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as err_file:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=err_file, text=True,
            encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW,
        )
        for line in process.stdout:
            match = TIME_RE.search(line)
            if match:
                current = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
                percent = min(current / duration * 100.0, 99.9) if duration > 0 else 0.0
                if on_progress(current, percent) is False:      # 回 False 代表要取消
                    process.terminate()
                    try:
                        process.wait(timeout=10)               # 等 ffmpeg 真的放掉輸出檔再回去刪
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    return -1, "已取消"
        process.wait()
        err_file.seek(0)
        return process.returncode, err_file.read().strip()


def remove_file(path) -> None:
    """刪半成品；ffmpeg 剛結束時檔案可能還被鎖住，重試幾次再放棄。"""
    for _ in range(10):
        try:
            Path(path).unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.3)


def worker_convert(job: Job):
    """逐支影片轉成同名 MP3，放回原資料夾。"""
    for item in job.items:
        if job.cancelled:
            item["status"] = "cancelled"
            continue

        source = Path(item["path"])
        target = source.with_suffix(".mp3")
        item["duration"] = probe_duration(item["path"])
        item["status"] = "running"

        def on_progress(current, percent, _item=item):
            _item["current"] = current
            _item["percent"] = round(percent, 1)
            return not job.cancelled

        code, stderr = run_ffmpeg(
            [FFMPEG, "-y", "-nostdin", "-loglevel", "error", "-progress", "pipe:1",
             "-i", str(source), "-vn", "-acodec", "libmp3lame", "-ab", "192k", str(target)],
            item["duration"], on_progress,
        )

        if job.cancelled:
            item["status"] = "cancelled"
            remove_file(target)
        elif code == 0:
            item["status"] = "done"
            item["percent"] = 100.0
            item["output"] = str(target)
            job.outputs.append(str(target))
        else:
            item["status"] = "failed"
            item["error"] = stderr[-500:] or f"ffmpeg exit {code}"

    job.done = True
    job.finished_at = time.time()
    job.message = "已取消" if job.cancelled else "轉檔完成"


def worker_merge(job: Job):
    """把選到的音檔依清單順序 concat 成一個檔（-c copy，不重新編碼）。"""
    total = sum(probe_duration(i["path"]) for i in job.items)
    for item in job.items:
        item["status"] = "running"

    ext = Path(job.items[0]["path"]).suffix
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = Path(job.output_dir) / f"merged_{stamp}{ext}"

    list_path = Path(tempfile.gettempdir()) / f"_merge_{job.id}.txt"
    lines = [f"file '{Path(i['path']).as_posix()}'" for i in job.items]
    list_path.write_text("\n".join(lines), encoding="utf-8")

    def on_progress(current, percent):
        for i in job.items:
            i["current"] = current
            i["percent"] = round(percent, 1)
        return not job.cancelled

    code, stderr = run_ffmpeg(
        [FFMPEG, "-y", "-nostdin", "-loglevel", "error", "-progress", "pipe:1",
         "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(target)],
        total, on_progress,
    )
    list_path.unlink(missing_ok=True)

    if job.cancelled:
        remove_file(target)
        for i in job.items:
            i["status"] = "cancelled"
        job.message = "已取消"
    elif code == 0:
        for i in job.items:
            i["status"] = "done"
            i["percent"] = 100.0
        job.outputs.append(str(target))
        job.message = "合併完成"
    else:
        for i in job.items:
            i["status"] = "failed"
            i["error"] = stderr[-500:] or f"ffmpeg exit {code}"
        job.message = "合併失敗"

    job.done = True
    job.finished_at = time.time()


YTDL_PCT_RE = re.compile(r"\[download\]\s+([\d.]+)%")
YTDL_DEST_RE = re.compile(r"\[(?:download|ExtractAudio|Merger)\]\s+(?:Destination:|Merging formats into)\s+\"?(.+?)\"?$")
YTDL_HAVE_RE = re.compile(r"\[download\]\s+(.+?) has already been downloaded")


def worker_ytdl(job: Job):
    """逐個「網址 × 格式」跑 yt-dlp；勾了 MP4+MP3 就是同一網址跑兩次，各存一份。"""
    out_dir = Path(job.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for item in job.items:
        if job.cancelled:
            item["status"] = "cancelled"
            continue

        item["status"] = "running"
        cmd = ytdl_base() + [
            "--newline", "--no-playlist", "--no-warnings", "--windows-filenames",
            "--ffmpeg-location", str(Path(FFMPEG).parent),
            "-o", str(out_dir / "%(title)s.%(ext)s"),
            *YTDL_FORMATS.get(item["format"], YTDL_FORMATS["mp4"]),
            item["url"],
        ]

        try:
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW,
            )
        except OSError as exc:
            item["status"] = "failed"
            item["error"] = f"叫不起 yt-dlp：{exc}（請先 pip install -U yt-dlp）"
            continue

        tail, final = [], ""
        for line in process.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                del tail[:-30]                    # 只留尾巴，失敗時當錯誤訊息

            pct = YTDL_PCT_RE.search(line)
            if pct:
                item["percent"] = round(float(pct.group(1)), 1)

            dest = YTDL_DEST_RE.search(line) or YTDL_HAVE_RE.search(line)
            if dest:
                final = dest.group(1).strip()
                item["name"] = f"[{item['format'].upper()}] {Path(final).name}"

            if job.cancelled:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                break

        process.wait()

        if job.cancelled:
            item["status"] = "cancelled"
        elif process.returncode == 0:
            item["status"] = "done"
            item["percent"] = 100.0
            # MP3 是先下載再抽音軌，最後那個 Destination 才是真正留下的檔案
            if final and not Path(final).exists():
                guess = out_dir / f"{Path(final).stem}.{item['format']}"
                final = str(guess) if guess.exists() else final
            item["output"] = final
            if final:
                job.outputs.append(final)
        else:
            item["status"] = "failed"
            item["error"] = "\n".join(tail)[-500:] or f"yt-dlp exit {process.returncode}"

    job.done = True
    job.finished_at = time.time()
    job.message = "已取消" if job.cancelled else "下載完成"


# ---------------------------------------------------------------- 圖檔轉 PDF
#
# 這一段是 image-to-pdf SKILL 的程式版。SKILL.md 原本只是給 AI 讀的自然語言 SOP，
# 五個步驟（掃描 → 問名稱 → 建資料夾 → 合成 PDF → 重新命名歸檔）每次都由 AI 現場
# 寫 code 執行；這裡把它固定下來，網頁按一下就跑，行為與 SKILL.md 完全一致。

def natural_sort_key(name: str) -> list:
    """1, 2, 10 要排成 1, 2, 10 而不是 1, 10, 2；截圖檔名帶時間戳也靠這個排對。"""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


def scan_images(folder: str) -> list:
    """只掃這一層的原始圖檔，不進子資料夾（SKILL 步驟 1）。"""
    directory = Path(folder)
    if not directory.is_dir():
        return []
    files = [entry for entry in directory.iterdir()
             if entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS]
    files.sort(key=lambda entry: natural_sort_key(entry.name))
    return [str(entry) for entry in files]


# 選檔框跑在獨立子進程，選到的路徑用「檔案」傳回來，不走 stdout：
# Windows 上子進程的 stdout 是 pipe 時會用 locale 編碼（cp950），中文路徑會被
# 轉成 U+FFFD 救不回來。寫檔可以兩邊都明講 UTF-8，中文檔名才不會壞。
PICK_DIALOG_CODE = r"""
import sys
import tkinter as tk
from tkinter import filedialog

out_path = sys.argv[1]
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
picked = filedialog.askopenfilename(
    title="選一張圖片（會處理它所在的整個資料夾）",
    filetypes=[("圖片檔", "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff"),
               ("所有檔案", "*.*")],
)
root.destroy()
with open(out_path, "w", encoding="utf-8") as handle:
    handle.write(picked or "")
"""


def pick_image_dialog() -> str:
    """開 Windows 選檔框讓使用者挑圖，回傳完整路徑（取消就回空字串）。

    瀏覽器的 <input type=file> 只給檔名不給路徑，所以選檔一定得由本機開。
    跑在獨立子進程裡：tkinter 不喜歡被丟在 HTTP 執行緒上，而且獨立進程崩掉也
    不會拖垮服務。用 tkinter 而不是 .NET 的 OpenFileDialog，是因為這台機器
    有 AppDomainManager 注入的前科，能不碰 .NET Framework 就不碰。
    """
    work_dir = Path(tempfile.mkdtemp(prefix="imgpick_"))
    script = work_dir / "pick.py"
    result_file = work_dir / "picked.txt"
    try:
        script.write_text(PICK_DIALOG_CODE, encoding="utf-8")
        subprocess.run(
            [sys.executable, str(script), str(result_file)],
            capture_output=True, creationflags=NO_WINDOW, timeout=600,
        )
        if not result_file.exists():
            return ""
        return result_file.read_text(encoding="utf-8").strip()
    except subprocess.TimeoutExpired:
        return ""
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


COURSE_PICK_DIALOG_CODE = r"""
import os
import sys
import tkinter as tk
from tkinter import filedialog

out_path, mode, source_type, initial = sys.argv[1:5]
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
kwargs = {"initialdir": initial} if initial and os.path.isdir(initial) else {}
if mode == "folder":
    title = "選擇課程歸檔目錄" if source_type == "output" else "選擇課程媒體資料夾"
    picked = filedialog.askdirectory(title=title, mustexist=True, **kwargs)
elif source_type == "local_video":
    picked = filedialog.askopenfilename(
        title="選擇課程影片",
        filetypes=[("影片檔", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts"),
                   ("所有檔案", "*.*")],
        **kwargs,
    )
else:
    picked = filedialog.askopenfilename(
        title="選擇課程音訊",
        filetypes=[("支援的音訊檔", "*.mp3 *.m4a *.wav *.ogg"),
                   ("所有檔案", "*.*")],
        **kwargs,
    )
root.destroy()
with open(out_path, "w", encoding="utf-8") as handle:
    handle.write(picked or "")
"""


def course_picker_options(kind: str, source_type: str) -> dict:
    """決定課程欄位要開檔案或資料夾選擇器。"""
    if kind == "output":
        return {"mode": "folder", "sourceType": "output"}
    if kind != "source" or source_type not in COURSE_SOURCE_TYPES:
        raise ValueError("不支援的選擇器類型")
    if source_type == "youtube":
        raise ValueError("YouTube 來源請貼上網址後抓取標題")
    return {
        "mode": "folder" if source_type in {"mp3_folder", MULTI_PART_SOURCE} else "file",
        "sourceType": source_type,
    }


def pick_course_path(kind: str, source_type: str, initial: str = "") -> str:
    """開課程來源／歸檔路徑選擇器，取消時回空字串。"""
    options = course_picker_options(kind, source_type)
    initial_path = Path(str(initial or "").strip())
    if initial_path.is_file():
        initial = str(initial_path.parent)
    elif not initial_path.is_dir():
        initial = ""
    work_dir = Path(tempfile.mkdtemp(prefix="coursepick_"))
    script = work_dir / "pick.py"
    result_file = work_dir / "picked.txt"
    try:
        script.write_text(COURSE_PICK_DIALOG_CODE, encoding="utf-8")
        subprocess.run(
            [sys.executable, str(script), str(result_file), options["mode"],
             options["sourceType"], initial],
            capture_output=True, creationflags=NO_WINDOW, timeout=600,
        )
        if not result_file.exists():
            return ""
        return result_file.read_text(encoding="utf-8").strip()
    except subprocess.TimeoutExpired:
        return ""
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def merge_pdfs(parts: list, output: Path) -> None:
    """把分批產生的暫存 PDF 併成一份。"""
    if len(parts) == 1:
        shutil.move(str(parts[0]), str(output))
        return

    try:
        import pikepdf
        # 來源必須撐到 save 為止，pikepdf 是延遲讀取的，提早 close 會拿到空頁。
        sources = [pikepdf.Pdf.open(str(part)) for part in parts]
        try:
            merged = pikepdf.Pdf.new()
            for source in sources:
                merged.pages.extend(source.pages)
            merged.save(str(output))
        finally:
            for source in sources:
                source.close()
    except ImportError:
        from pypdf import PdfWriter
        writer = PdfWriter()
        for part in parts:
            writer.append(str(part))
        with open(output, "wb") as handle:
            writer.write(handle)
        writer.close()


# ---------------------------------------------------------------- 書名自動辨識
#
# 「資料夾與 PDF 名稱」留空時改由圖片自己認（SKILL 步驟 2 的自動版）：
#   ① ffmpeg 把圖縮成 1024px JPEG——ffmpeg 本來就是這個服務的前提，
#      不必為了讀一張圖多裝 Pillow，順便避開 Groq 的 base64 4MB 上限。
#   ② 丟給 Groq 的視覺模型讀封面，只要書名與作者。
#   ③ 拿辨識到的書名去博客來搜尋，挑最接近的一筆當正式書名，
#      這樣副標題與標點才會是出版社的正式寫法，不是模型自己拼的。
# 認不出來就讓工作失敗並要求手動填名稱；絕不亂猜一個名字去改幾百個檔案。

GROQ_CHAT_API = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_API = "https://api.groq.com/openai/v1/models"
# 依序嘗試，帳號沒開通就換下一個；清單外若有新的 vision 模型，會由 /models 動態補上。
# 2026-08-24 實測：這個帳號只有 qwen 吃得到圖，llama-4 系列一律 model_not_found。
GROQ_VISION_MODELS = (
    "qwen/qwen3.6-27b",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
)
BOOKS_SEARCH_URL = "https://search.books.com.tw/search/query/key/{key}/cat/all"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
IDENTIFY_MAX_PAGES = 5        # 第一張是空白頁或版權頁時往後看，最多看 5 張
# Groq 免費層一張圖約 4000 token，TPM 只有 8000，等於每分鐘看兩張就會撞 429
GROQ_RATE_LIMIT_RETRIES = 4
GROQ_RATE_LIMIT_MAX_WAIT = 70.0
VISION_MAX_WIDTH = 1024
MAX_NAME_LENGTH = 80          # 書名帶長副標時整條當資料夾名會爆路徑長度
AUTHOR_MAX_LENGTH = 30        # 合譯本作者一串列下去會把檔名撐爆

# Windows 檔名不收的字元換成全形，直接刪掉會讓書名讀起來斷掉
FILENAME_REPLACEMENTS = {"<": "＜", ">": "＞", ":": "：", '"': "”",
                         "/": "／", "\\": "＼", "|": "｜", "?": "？", "*": "＊"}

VISION_PROMPT = (
    "這是一本書的掃描頁。判斷這一頁上有沒有書名（封面、書名頁、書背都算）。\n"
    "只輸出 JSON，不要任何說明文字或程式碼框：\n"
    '{"title": "書名原文", "author": "作者", "confidence": "high|low|none"}\n'
    "書名清楚可讀才給 high；模糊、只認出片段給 low；"
    "空白頁、版權頁、目錄、內文、閱讀器介面一律 none 且 title 給空字串。\n"
    "書名照原文輸出，繁體中文書不要翻譯，也不要自己加書名號。"
)


def sanitize_filename(name: str) -> str:
    """把書名整成 Windows 收得下的資料夾／檔名。"""
    cleaned = "".join(FILENAME_REPLACEMENTS.get(char, char) for char in name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(". ")
    return cleaned[:MAX_NAME_LENGTH].rstrip(". ")


def groq_headers(api_key: str, json_body: bool = False) -> dict:
    """Groq 前面掛 Cloudflare，Python-urllib 的預設 UA 會被擋成 403 error code 1010，
    所以每一個請求都要自報一個像樣的 User-Agent。"""
    headers = {"Authorization": f"Bearer {api_key}",
               "User-Agent": f"classroom-mediatools/{SERVICE_VERSION}"}
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def env_groq_key() -> str:
    """本機環境變數那把。每次都重讀，服務啟動後才換 key 也吃得到。"""
    return os.environ.get("GROQ_API_KEY", "").strip()


def groq_key_is_valid(key: str) -> bool:
    """打一次 /models 看認不認這把 key，比跑到一半才 401 好。"""
    request = urllib.request.Request(GROQ_MODELS_API, headers=groq_headers(key))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 401:                                   # Groq 對過期／不存在的 key 回 401
            return False
        # 403 多半是 Cloudflare 擋請求而不是 key 有問題，不要誤判成「換一把」
        raise RuntimeError(f"Groq 回了 {exc.code}，這不像是 key 的問題：{exc.reason}") from exc
    except Exception as exc:                                  # noqa: BLE001
        raise RuntimeError(f"連不上 Groq：{exc}") from exc


def resolve_groq_key(preferred: str) -> str:
    """網頁帶來的 key 失效就退回本機環境變數。

    2026-08-24 實際踩到：Firebase Secret 裡那把因為久沒用被 Groq 自動過期，
    但本機環境變數是新的。兩邊各有可能過期，所以兩把都試過再放棄。
    """
    candidates, seen = [], set()
    for key in (preferred, env_groq_key()):
        if key and key not in seen:
            seen.add(key)
            candidates.append(key)
    if not candidates:
        raise RuntimeError("沒有拿到 Groq API Key，無法自動辨識書名。"
                           "請重新整理 /admin/ 頁面（key 由登入後的後端發），或自己填名稱。")
    for key in candidates:
        if groq_key_is_valid(key):
            return key
    raise RuntimeError(
        f"Groq 不認這 {len(candidates)} 把 key（網頁帶來的與本機 GROQ_API_KEY 都回 401）。"
        "Groq 會讓久沒用的 key 自動過期，請到 console.groq.com/keys 產一把新的，"
        "更新 Firebase Secret GROQ_API_KEY 後重新部署 functions。")


def compose_folder_name(title: str, author: str) -> str:
    """資料夾與 PDF 名＝「主書名 - 作者」。

    2026-08-24 使用者裁定：博客來的正式書名副標常常很長，整條拿來當資料夾名
    會讓每張圖的檔名都跟著長，幾百張深路徑就會撞 Windows 的路徑長度上限。
    所以冒號後的副標砍掉只留主書名，改用作者來區分同名書的不同版本。
    """
    main = re.split(r"[：:]", title, maxsplit=1)[0].strip() or title.strip()
    who = re.sub(r"\s+", " ", author or "").strip()[:AUTHOR_MAX_LENGTH]
    return sanitize_filename(f"{main} - {who}" if who else main)


def shrink_for_vision(path: str) -> bytes:
    """縮成 1024px 寬的 JPEG 再送出去，大圖直接 base64 會被 API 擋掉。"""
    with tempfile.TemporaryDirectory(prefix="imgpdf_vision_") as work:
        output = Path(work) / "page.jpg"
        result = subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-i", path,
             "-vf", f"scale='min({VISION_MAX_WIDTH},iw)':-2", "-q:v", "4", str(output)],
            capture_output=True)
        if result.returncode != 0 or not output.exists():
            detail = result.stderr.decode("utf-8", errors="replace").strip()[:200]
            raise RuntimeError(f"ffmpeg 讀不了這張圖：{Path(path).name}｜{detail}")
        return output.read_bytes()


def groq_vision_models(api_key: str) -> list:
    """問 Groq 這個帳號現在有哪些模型，硬編清單以外的新 vision 模型也吃得到。"""
    request = urllib.request.Request(GROQ_MODELS_API, headers=groq_headers(api_key))
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:                                         # noqa: BLE001
        return list(GROQ_VISION_MODELS)                       # 列不到就照硬編清單試
    available = {item.get("id", "") for item in data.get("data", [])}
    ordered = [model for model in GROQ_VISION_MODELS if model in available]
    # 帳號有、硬編清單沒有的視覺系列補在後面當備援
    ordered += sorted(model for model in available
                      if ("llama-4" in model or "qwen" in model) and model not in ordered)
    return ordered or list(GROQ_VISION_MODELS)


def retry_after_seconds(exc, detail: str) -> float:
    """429 要等多久：先看 retry-after 標頭，沒有就從訊息裡的「try again in 960ms」撈。"""
    header = exc.headers.get("retry-after") if exc.headers else None
    if header:
        try:
            return min(float(header), GROQ_RATE_LIMIT_MAX_WAIT)
        except ValueError:
            pass
    match = re.search(r"try again in ([\d.]+)(ms|s)", detail)
    if match:
        seconds = float(match.group(1)) / (1000 if match.group(2) == "ms" else 1)
        return min(seconds + 0.5, GROQ_RATE_LIMIT_MAX_WAIT)
    return 20.0


def groq_read_page(api_key: str, image_path: str, models: list, on_progress=None) -> dict:
    """把一張圖交給 Groq 視覺模型，回 {'title','author','confidence'}。"""
    encoded = base64.b64encode(shrink_for_vision(image_path)).decode("ascii")
    payload = {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": VISION_PROMPT},
            {"type": "image_url",
             "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}},
        ]}],
        "temperature": 0,
        # 推理模型的 <think> 也吃 token，給太少會在還沒吐 JSON 前就被截斷
        "max_tokens": 1200,
    }
    last_error = ""
    for model in models:
        body = json.dumps({**payload, "model": model}).encode("utf-8")
        data = None
        for attempt in range(GROQ_RATE_LIMIT_RETRIES):
            request = urllib.request.Request(GROQ_CHAT_API, data=body,
                                             headers=groq_headers(api_key, json_body=True))
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
                last_error = f"{exc.code} {detail}"
                if exc.code in (400, 404):                    # 這個模型不能用就換下一個
                    break
                if exc.code == 429 and attempt < GROQ_RATE_LIMIT_RETRIES - 1:
                    delay = retry_after_seconds(exc, detail)
                    if on_progress:
                        on_progress(f"Groq 限流，等 {delay:.0f} 秒再試")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Groq 辨識失敗：{last_error}") from exc
            except Exception as exc:                          # noqa: BLE001
                raise RuntimeError(f"連不上 Groq：{exc}") from exc

        if data is None:                                      # 這個模型試不成，換下一個
            continue

        text = data["choices"][0]["message"]["content"].strip()
        # qwen 這類推理模型會先吐一段 <think>…</think>，裡面的大括號會害 JSON 抓錯
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        text = re.sub(r"^<think>.*", "", text, flags=re.S).strip()   # think 被 max_tokens 截斷
        match = re.search(r"\{.*\}", text, re.S)              # 模型偶爾會多包一層說明
        if not match:
            last_error = f"模型沒有回 JSON：{text[:120]}"
            continue
        try:
            parsed = json.loads(match.group())
        except json.JSONDecodeError:
            last_error = f"模型回的 JSON 壞掉：{match.group()[:120]}"
            continue
        return {"title": str(parsed.get("title") or "").strip(),
                "author": str(parsed.get("author") or "").strip(),
                "confidence": str(parsed.get("confidence") or "none").strip().lower(),
                "model": model}

    raise RuntimeError(f"Groq 沒有可用的視覺模型（{last_error or '模型清單是空的'}）")


def books_search_titles(keyword: str, limit: int = 8) -> list:
    """博客來搜尋結果的書名清單。搜不到或被擋就回空的，不讓它擋住整個流程。"""
    url = BOOKS_SEARCH_URL.format(key=quote(keyword, safe=""))
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            page = response.read().decode("utf-8", errors="replace")
    except Exception:                                         # noqa: BLE001
        return []
    titles, seen = [], set()
    for raw in re.findall(r'<h4><a [^>]*title="([^"]+)"', page):
        title = re.sub(r"\s+", " ", html_lib.unescape(raw)).strip()
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def pick_official_title(recognized: str, candidates: list) -> str:
    """從搜尋結果挑最像的一筆；差太多就回空字串，改用辨識到的原文。"""
    key = re.sub(r"\s+", "", recognized)
    if not key:
        return ""
    contained = [c for c in candidates if key in re.sub(r"\s+", "", c)]
    if contained:
        # 含辨識書名的最短一筆＝沒有加贈品、套書、WORKBOOK 之類後綴的本體
        return min(contained, key=len)
    best, best_score = "", 0.0
    for candidate in candidates:
        score = difflib.SequenceMatcher(None, key, re.sub(r"\s+", "", candidate)).ratio()
        if score > best_score:
            best, best_score = candidate, score
    return best if best_score >= 0.55 else ""


def identify_book_name(paths: list, api_key: str, on_progress=None) -> dict:
    """看前幾張圖認出書名，再用博客來校正成正式書名。"""
    api_key = resolve_groq_key(api_key)
    models = groq_vision_models(api_key)
    looked = []
    found = None
    for index, path in enumerate(paths[:IDENTIFY_MAX_PAGES], start=1):
        if on_progress:
            on_progress(f"辨識書名中（第 {index} 張：{Path(path).name}）")
        page = groq_read_page(api_key, path, models, on_progress)
        looked.append(f"{Path(path).name}→{page['title'] or '沒有書名'}")
        if page["title"] and page["confidence"] != "none":
            found = {**page, "page": Path(path).name}
            break

    if not found:
        raise RuntimeError(
            f"看了前 {len(looked)} 張圖都認不出書名（{'、'.join(looked)}），"
            "請自己填名稱再跑一次。原始圖檔沒有被動過。")

    if on_progress:
        on_progress(f"比對書名中（{found['title']}）")
    candidates = books_search_titles(found["title"])
    official = pick_official_title(found["title"], candidates)
    full_title = official or found["title"]
    name = compose_folder_name(full_title, found["author"])
    if not name:
        raise RuntimeError(f"辨識到的書名清乾淨之後是空的（原文：{found['title']}），請自己填名稱。")

    return {"name": name,
            "fullTitle": full_title,
            "recognized": found["title"],
            "author": found["author"],
            "official": official,
            "source": "books.com.tw" if official else "vision",
            "sourceLabel": "博客來比對" if official else "封面辨識，博客來沒有相符的書",
            "page": found["page"],
            "model": found["model"],
            "confidence": found["confidence"],
            "candidates": candidates}


def worker_imgpdf(job: Job):
    """SKILL 步驟 3~5：建資料夾 → 合成 PDF → 圖檔重新命名歸檔。

    進度分兩段：合成 PDF 記到每張圖 50%，搬檔完成才算 100%，
    所以進度條不會在合成階段就衝到滿格。
    """
    try:
        import img2pdf
    except ImportError as exc:
        raise RuntimeError("本機 Python 缺 img2pdf 模組，請先執行 pip install img2pdf") from exc

    paths = [item["path"] for item in job.items]

    # 名稱留空＝自動命名：辨識完才知道資料夾要叫什麼，所以擺在建資料夾之前
    if not (job.target_name or "").strip():
        def report(text):
            job.message = text
        job.identify = identify_book_name(paths, getattr(job, "groq_key", ""), report)
        job.target_name = job.identify["name"]
        job.message = f"辨識為《{job.target_name}》（{job.identify['sourceLabel']}），開始合成"

    name = job.target_name
    parent = Path(job.output_dir)
    target_dir = parent / name

    if target_dir.exists() and any(target_dir.iterdir()):
        raise RuntimeError(f"資料夾已經存在而且不是空的，先改名或清空：{target_dir}")

    for item in job.items:
        item["status"] = "running"

    temp_dir = Path(tempfile.mkdtemp(prefix="imgpdf_"))
    try:
        # --- 步驟 4：分批合成 PDF（維持原解析度，img2pdf 是無失真嵌入）
        parts = []
        for start in range(0, len(paths), IMGPDF_CHUNK):
            if job.cancelled:
                break
            batch = paths[start:start + IMGPDF_CHUNK]
            part_path = temp_dir / f"part_{len(parts):04d}.pdf"
            with open(part_path, "wb") as handle:
                handle.write(img2pdf.convert(batch))
            parts.append(part_path)
            for item in job.items[start:start + len(batch)]:
                item["percent"] = 50.0

        if job.cancelled:
            for item in job.items:
                item["status"] = "cancelled"
            job.message = "已取消，原始圖檔沒有被動過"
            return

        job.message = "合成完畢，正在歸檔"
        merged_pdf = temp_dir / "merged.pdf"
        merge_pdfs(parts, merged_pdf)

        # --- 步驟 3：建資料夾（等 PDF 真的做出來才建，失敗不留空殼）
        target_dir.mkdir(parents=True, exist_ok=True)
        final_pdf = target_dir / f"{name}.pdf"
        shutil.move(str(merged_pdf), str(final_pdf))
        job.outputs.append(str(final_pdf))

        # --- 步驟 5：圖檔搬進同一個資料夾並重新編號（4 位補零，副檔名保留）
        for index, item in enumerate(job.items, start=1):
            source = Path(item["path"])
            destination = target_dir / f"{name}_{index:04d}{source.suffix.lower()}"
            shutil.move(str(source), str(destination))
            item["status"] = "done"
            item["percent"] = 100.0
            item["output"] = str(destination)

        size_mb = final_pdf.stat().st_size / 1024 / 1024
        job.message = f"完成：{len(job.items)} 張圖 → {final_pdf.name}（{size_mb:.1f} MB）"
    finally:
        # done / finished_at 交給 start_job 的 runner 收尾，這裡先設會跟錯誤訊息搶時序
        shutil.rmtree(temp_dir, ignore_errors=True)


def start_job(kind: str, paths: list, output_dir: str, **extra) -> Job:
    job = Job(kind, paths, output_dir)
    for key, value in extra.items():                          # imgpdf 要多帶 target_name
        setattr(job, key, value)
    with JOBS_LOCK:
        JOBS[job.id] = job
    worker = {"convert": worker_convert, "merge": worker_merge,
              "ytdl": worker_ytdl, "imgpdf": worker_imgpdf}[kind]

    def runner():
        """工作緒若炸掉也要把 job 收乾淨，否則網頁會一直停在處理中。"""
        try:
            worker(job)
        except Exception as exc:                              # noqa: BLE001
            for item in job.items:
                if item["status"] in ("waiting", "running"):
                    item["status"] = "failed"
                    item["error"] = str(exc)
            job.message = f"發生錯誤：{exc}"
        finally:
            job.done = True
            if job.finished_at is None:
                job.finished_at = time.time()

    threading.Thread(target=runner, daemon=True).start()
    return job


# ---------------------------------------------------------------- 檔案瀏覽

def list_drives() -> list:
    return [f"{letter}:\\" for letter in string.ascii_uppercase if Path(f"{letter}:\\").exists()]


def list_folder(path: str, kind: str) -> dict:
    folder = Path(path)
    if not folder.exists() or not folder.is_dir():
        return {"error": f"找不到資料夾：{path}"}

    wanted = VIDEO_EXTENSIONS if kind == "video" else AUDIO_EXTENSIONS
    dirs, files = [], []
    try:
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            try:
                if entry.is_dir():
                    dirs.append(entry.name)
                elif entry.suffix.lower() in wanted and not entry.name.startswith("merged_"):
                    files.append({
                        "name": entry.name,
                        "path": str(entry),
                        "size": entry.stat().st_size,
                        "mtime": entry.stat().st_mtime,
                    })
            except OSError:
                continue
    except PermissionError:
        return {"error": f"沒有權限讀取：{path}"}

    parent = str(folder.parent) if folder.parent != folder else ""
    return {"path": str(folder), "parent": parent, "dirs": dirs, "files": files, "drives": list_drives()}


# ---------------------------------------------------------------- HTTP

PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>影音工具 — 影片轉MP3 / 錄音合併</title>
<style>
  :root {
    --bg: #0f1216; --card: #171c23; --line: #262d38; --text: #e6edf3;
    --muted: #8b98a8; --accent: #4d9fff; --ok: #3fb950; --bad: #f85149;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text);
         font-family: "Segoe UI", "Microsoft JhengHei", system-ui, sans-serif; }
  header { padding: 20px 24px 0; max-width: 980px; margin: 0 auto; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 16px; }
  main { max-width: 980px; margin: 0 auto; padding: 0 24px 40px; }
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
  .tab { padding: 8px 16px; border-radius: 8px 8px 0 0; background: var(--card);
         border: 1px solid var(--line); border-bottom: none; cursor: pointer; font-size: 14px; }
  .tab.on { background: var(--accent); border-color: var(--accent); color: #fff; }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 16px; margin-bottom: 16px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  input[type=text], select { flex: 1; min-width: 240px; padding: 9px 12px; border-radius: 8px;
                     border: 1px solid var(--line); background: #0d1117; color: var(--text);
                     font-family: inherit; font-size: 13px; }
  button { padding: 9px 16px; border-radius: 8px; border: 1px solid var(--line);
           background: #212832; color: var(--text); cursor: pointer; font-size: 13px;
           font-family: inherit; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  button.go { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  button.stop { background: #3a2226; border-color: #6b2f35; color: #ffb3b3; }
  .crumbs { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0 8px; }
  .chip { padding: 4px 10px; border-radius: 999px; background: #0d1117; border: 1px solid var(--line);
          font-size: 12px; cursor: pointer; color: var(--muted); }
  .chip:hover { color: var(--text); border-color: var(--accent); }
  ul.files { list-style: none; margin: 0; padding: 0; max-height: 320px; overflow-y: auto; }
  ul.files li { display: flex; align-items: center; gap: 10px; padding: 8px 10px;
                border-bottom: 1px solid var(--line); font-size: 13px; }
  ul.files li:last-child { border-bottom: none; }
  ul.files li:hover { background: #1c222b; }
  .fname { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fsize { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .empty { color: var(--muted); font-size: 13px; padding: 16px 4px; }
  .bar { height: 8px; border-radius: 999px; background: #0d1117; overflow: hidden; }
  .bar > i { display: block; height: 100%; width: 0; background: var(--accent);
             transition: width .3s ease; }
  .bar.ok > i { background: var(--ok); }
  .bar.bad > i { background: var(--bad); }
  .job-item { margin-bottom: 12px; }
  .job-head { display: flex; justify-content: space-between; gap: 12px; font-size: 13px;
              margin-bottom: 5px; }
  .job-head b { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pct { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .err { color: var(--bad); font-size: 12px; margin-top: 4px; word-break: break-all; }
  .note { color: var(--muted); font-size: 12px; margin-top: 10px; }
  .hide { display: none; }
  .out { font-size: 12px; color: var(--ok); word-break: break-all; margin-top: 3px; }
  .label { font-size: 13px; color: var(--muted); margin-bottom: 6px; }
  textarea { width: 100%; padding: 9px 12px; border-radius: 8px; border: 1px solid var(--line);
             background: #0d1117; color: var(--text); font-family: inherit; font-size: 13px;
             resize: vertical; }
  .pick { display: flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: 8px;
          border: 1px solid var(--line); background: #0d1117; font-size: 13px; cursor: pointer; }
  .pick:has(input:checked) { border-color: var(--accent); color: var(--accent); }
  .picks { display: flex; gap: 8px; flex-wrap: wrap; }
  .pick.locked { cursor: not-allowed; border-color: #33507a; color: #7fa8dd; background: #10161f; }
  .pick.locked input { cursor: not-allowed; }
  .pick .lk { font-size: 11px; color: var(--muted); }
  .pick.off { opacity: .38; cursor: not-allowed; border-color: var(--line); color: var(--muted); }
  .subpicks { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 0 10px;
              padding-left: 14px; border-left: 2px solid #2f3b4a; }
  .subpicks .pick { padding: 6px 12px; font-size: 12px; }
  .subpicks.inline { margin: 0; align-items: center; }
  .engine { display: flex; align-items: center; gap: 8px; margin: 10px 0 0 10px;
            padding-left: 14px; border-left: 2px solid #2f3b4a; }
  .engine select { flex: 0 1 380px; min-width: 200px; }
  .engine .hintx { font-size: 12px; color: var(--muted); }
  .prog li { display: flex; align-items: center; gap: 10px; padding: 7px 10px;
             border-bottom: 1px solid var(--line); font-size: 13px; }
  .prog li:last-child { border-bottom: none; }
  .prog ul { list-style: none; margin: 8px 0 0; padding: 0; }
  .prog .nm { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .prog .st { font-size: 12px; white-space: nowrap; }
  .prog .minibar { width: 120px; height: 6px; border-radius: 999px; background: #0d1117;
                   overflow: hidden; flex: none; }
  .prog .minibar > i { display: block; height: 100%; background: var(--accent); }
  .prog .minibar.ok > i { background: var(--ok); }
  .prog .minibar.bad > i { background: var(--bad); }
  .s-completed { color: var(--ok); } .s-blocked { color: var(--bad); }
  .s-running { color: var(--accent); } .s-pending { color: var(--muted); }
  .prio { margin: 8px 0 0 10px; padding-left: 14px; border-left: 2px solid #2f3b4a; }
  .prio ul { list-style: none; margin: 6px 0 0; padding: 0; }
  .prio li { display: flex; align-items: center; gap: 8px; padding: 5px 8px; font-size: 12px;
             border: 1px solid var(--line); border-radius: 6px; background: #0d1117;
             margin-bottom: 5px; max-width: 520px; }
  .prio li.off { opacity: .4; }
  .prio .nm { flex: 1; }
  .prio .ord { color: var(--muted); font-variant-numeric: tabular-nums; min-width: 16px; }
  .prio button { padding: 2px 8px; font-size: 12px; line-height: 1.4; }
  .paid { color: #f0a020; }
  .course-preview { margin-top: 8px; padding: 10px 12px; border: 1px solid var(--line);
                    border-radius: 8px; background: #0d1117; font-size: 12px; }
  .course-preview ul { list-style: none; margin: 7px 0 0; padding: 0; max-height: 220px;
                       overflow-y: auto; }
  .course-preview li { display: flex; justify-content: space-between; gap: 12px;
                       padding: 5px 0; border-top: 1px solid var(--line); }
  .course-preview .kind { color: var(--muted); white-space: nowrap; }
</style>
</head>
<body>
<header>
  <h1>🎬 影音工具</h1>
  <div class="sub">網頁操作、本機處理。檔案不上傳，轉檔輸出寫回原資料夾。</div>
  <div class="tabs">
    <div class="tab on" data-mode="course">課程整理SOP</div>
    <div class="tab" data-mode="lexicon">字幕詞庫</div>
    <div class="tab" data-mode="video">影片轉 MP3</div>
    <div class="tab" data-mode="audio">錄音檔合併</div>
    <div class="tab" data-mode="ytdl">YouTube 下載</div>
  </div>
</header>
<main>
  <div class="card hide" id="ytdlCard">
    <div class="label">貼上網址（一行一個，可以一次貼多個）</div>
    <textarea id="ytUrls" rows="4" spellcheck="false"
              placeholder="https://www.youtube.com/watch?v=..."></textarea>
    <div class="row" style="margin:12px 0">
      <label class="pick"><input type="checkbox" id="fmtMp4" checked> 🎬 MP4 影片</label>
      <label class="pick"><input type="checkbox" id="fmtMp3"> 🎵 MP3 僅聲音</label>
      <span class="fsize">兩個都勾就各存一份</span>
    </div>
    <div class="label">存到這個資料夾</div>
    <div class="row">
      <input type="text" id="ytOut" spellcheck="false">
    </div>
    <div class="row" style="margin-top:14px">
      <button class="go" id="ytRun" onclick="startYtdl()">開始下載</button>
      <button class="stop hide" id="ytCancel" onclick="cancel()">取消</button>
    </div>
  </div>

  <div class="card hide" id="browseCard">
    <div class="row">
      <input type="text" id="path" spellcheck="false">
      <button onclick="go(document.getElementById('path').value)">前往</button>
      <button onclick="load()">重新整理</button>
    </div>
    <div class="crumbs" id="drives"></div>
    <div class="crumbs" id="dirs"></div>
    <div class="row" style="margin:10px 0 6px">
      <button onclick="pick(true)">全選</button>
      <button onclick="pick(false)">全不選</button>
      <span class="fsize" id="count"></span>
    </div>
    <ul class="files" id="files"></ul>
    <div class="row" style="margin-top:14px">
      <button class="go" id="run" onclick="start()">開始</button>
      <button class="stop hide" id="cancel" onclick="cancel()">取消</button>
      <span class="fsize" id="hint"></span>
    </div>
  </div>

  <div class="card" id="courseCard">
    <div class="label">來源類型</div>
    <select id="courseType" onchange="courseSourceTypeChanged()">
      <option value="youtube">YouTube 網址</option>
      <option value="local_video">本機錄影檔</option>
      <option value="local_mp3">本機音訊檔</option>
      <option value="mp3_folder">媒體資料夾批次（每個檔各是一堂課）</option>
      <option value="mp3_parts">同一堂課的多個檔案（分段錄音）</option>
    </select>
    <div class="label" style="margin-top:12px">來源網址或完整路徑</div>
    <div class="row">
      <input type="text" id="courseSource" spellcheck="false" placeholder="https://www.youtube.com/watch?v=..." oninput="clearCoursePreview(); syncCourseArtifacts()" onblur="suggestCourseName(); previewCourseSource()">
      <button id="courseSourcePick" onclick="pickCourseSource()">抓影片標題</button>
    </div>
    <div class="course-preview hide" id="coursePreview" aria-live="polite">
      <div id="coursePreviewSummary"></div>
      <ul id="coursePreviewList"></ul>
    </div>
    <div class="label" style="margin-top:12px">課程名稱（自動帶入後仍可修改）</div>
    <input type="text" id="courseName" spellcheck="false" placeholder="會自動抓影片標題、檔名或資料夾名">
    <div class="label" style="margin-top:16px">要產出哪些東西（沒勾的 AI 就不做）</div>
    <div class="picks">
      <label class="pick" id="w-video"><input type="checkbox" id="a-video" checked onchange="syncCourseArtifacts()"> 🎬 下載 MP4</label>
      <label class="pick" id="w-mp3"><input type="checkbox" id="a-mp3" checked onchange="syncCourseArtifacts()"> 🎵 轉檔 MP3</label>
      <label class="pick" id="w-transcript"><input type="checkbox" id="a-transcript" checked onchange="syncCourseArtifacts()"> 📝 逐字稿</label>
      <label class="pick" id="w-review" title="用 agy（Gemini）校對專有名詞與人名，raw 稿保留不覆蓋"><input type="checkbox" id="a-review" onchange="syncCourseArtifacts()"> 🔍 校對逐字稿</label>
      <label class="pick" id="w-summary"><input type="checkbox" id="a-summary" checked onchange="syncCourseArtifacts()"> 📄 摘要</label>
      <label class="pick" id="w-report"><input type="checkbox" id="a-report" checked onchange="syncCourseArtifacts()"> 📊 培訓報告</label>
      <label class="pick" id="w-mindmap"><input type="checkbox" id="a-mindmap" checked onchange="syncCourseArtifacts()"> 🧠 心智圖</label>
      <label class="pick" id="w-skillTree"><input type="checkbox" id="a-skillTree" onchange="syncCourseArtifacts()"> 🌳 技能樹</label>
      <div class="subpicks inline" id="courseSkillSub" style="display:none">
        <label class="pick" id="w-teach"><input type="checkbox" id="s-teach" onchange="syncCourseArtifacts()"> 含教學</label>
        <label class="pick"><input type="checkbox" id="s-minimum" onchange="syncCourseArtifacts()"> 含最小案例</label>
      </div>
    </div>
    <div class="engine" id="courseQualityRow">
      <span class="hintx">影片畫質</span>
      <select id="courseQuality" onchange="syncCourseArtifacts()">
        <option value="best">最佳畫質（檔案最大）</option>
        <option value="1080p">1080p</option>
        <option value="720p">720p</option>
        <option value="480p">480p</option>
      </select>
      <span class="hintx">只影響下載的 MP4，MP3 音質不受影響。</span>
    </div>
    <div class="subpicks" id="courseSummarySub" style="display:none">
      <label class="pick" id="w-dense"><input type="checkbox" id="s-dense" onchange="syncCourseArtifacts()"> 高密度總覽（1-2 段，取代預設 3-6 段）</label>
      <label class="pick" id="w-rawSegments"><input type="checkbox" id="a-rawSegments" onchange="syncCourseArtifacts()"> 另出原字分段版（保留原句不改寫）</label>
    </div>
    <div class="engine" id="courseEngineRow">
      <span class="hintx">轉錄引擎</span>
      <select id="courseEngine" onchange="syncCourseArtifacts()">
        <option value="auto">自動（作者字幕 → Groq → 本機 whisper）</option>
        <option value="groq">Groq whisper-large-v3（免費額度，有 SRT）</option>
        <option value="assemblyai">💲 AssemblyAI 說話者辨識（付費，無 SRT）</option>
        <option value="local_whisper">本機 whisper（不花額度，很慢）</option>
        <option value="subtitle_only">只用現成字幕（限 YouTube）</option>
      </select>
      <span class="hintx" id="courseEngineHint"></span>
    </div>
    <div class="prio" id="coursePrioBox">
      <span class="hintx">自動模式的嘗試順序（取消勾選就跳過該引擎；付費引擎不會進入這裡）</span>
      <ul id="coursePrioList"></ul>
    </div>
    <div class="note" id="courseDepNote"></div>
    <div class="label" style="margin-top:12px">課程歸檔根目錄</div>
    <div class="row">
      <input type="text" id="courseOut" spellcheck="false">
      <button id="courseOutPick" onclick="pickCourseOutput()">選擇資料夾</button>
    </div>
    <label class="pick" id="courseBatchWrap" style="margin-top:14px; display:none">
      <input type="checkbox" id="courseBatch" onchange="syncCourseArtifacts()">
      📚 批次：資料夾內每個媒體檔各建一堂課
    </label>
    <div class="row" style="margin-top:14px">
      <button class="go" id="courseCreate" onclick="startCourseCreate()">建立可續跑任務</button>
    </div>
    <div class="note">
      下一步很簡單：① 建立任務　② 按「複製 AI 執行指令」　③ 回到目前的 Hermes 對話貼上。<br>
      AI 收到指令後只會做你上面勾起來的項目，沒勾的不會產出。
    </div>
    <div class="row" style="margin-top:10px">
      <button class="go hide" id="courseCopyPrompt" onclick="copyCoursePrompt()">複製 AI 執行指令</button>
    </div>
    <div class="out" id="courseResult" style="white-space:pre-wrap"></div>

    <div class="prog" id="courseProgressBox" style="margin-top:20px">
      <div class="row" style="justify-content:space-between">
        <div class="label" style="margin:0">課程進度（讀 manifest，AI 做完一段就會更新）</div>
        <button onclick="refreshCourseProgress()">重新整理</button>
      </div>
      <div class="job-head" style="margin-top:8px">
        <b id="courseProgTitle">尚未載入</b><span class="pct" id="courseProgPct"></span>
      </div>
      <div class="bar" id="courseProgBar"><i></i></div>
      <ul id="courseProgList"></ul>
      <div class="note">關掉網頁也不影響：進度存在每個課程資料夾的 course-manifest.json 裡。</div>
    </div>
  </div>

  <div class="card hide" id="lexCard" style="padding:0;overflow:hidden">
    <iframe id="lexFrame" title="字幕詞庫管理"
            style="width:100%;height:calc(100vh - 230px);min-height:520px;border:0;display:block"></iframe>
  </div>

  <div class="card hide" id="progress">
    <div class="job-head"><b id="jobTitle">處理中</b><span class="pct" id="jobPct">0%</span></div>
    <div class="bar" id="jobBar"><i></i></div>
    <div id="jobItems" style="margin-top:16px"></div>
    <div class="note" id="jobNote"></div>
  </div>
</main>

<script>
let mode = 'course';   // 預設開在課程整理SOP
let cur = '';
let files = [];
let checked = new Set();
let jobId = null;
let timer = null;
let lastCoursePrompt = '';

const DEFAULTS = { video: %DEFAULT_VIDEO%, audio: %DEFAULT_AUDIO%, ytdl: %DEFAULT_YTDL%, course: %DEFAULT_COURSE% };
const $ = id => document.getElementById(id);
const mb = n => n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' : (n / 1048576).toFixed(1) + ' MB';
const hhmmss = s => [3600, 60, 1].map((u, i) => String(Math.floor(s / u) % (i ? 60 : 999)).padStart(2, '0')).join(':');

document.querySelectorAll('.tab').forEach(tab => tab.onclick = () => {
  if (jobId) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  tab.classList.add('on');
  mode = tab.dataset.mode;
  $('ytdlCard').classList.toggle('hide', mode !== 'ytdl');
  $('courseCard').classList.toggle('hide', mode !== 'course');
  $('lexCard').classList.toggle('hide', mode !== 'lexicon');
  $('browseCard').classList.toggle('hide', mode === 'ytdl' || mode === 'course' || mode === 'lexicon');
  if (mode === 'lexicon') { const f = $('lexFrame'); if (!f.src) f.src = '/lexicon'; return; }
  if (mode === 'ytdl') { $('ytOut').value = $('ytOut').value || DEFAULTS.ytdl; return; }
  if (mode === 'course') {
    $('courseOut').value = $('courseOut').value || DEFAULTS.course;
    courseSourceTypeChanged();
    refreshCourseProgress();
    return;
  }
  $('run').textContent = mode === 'video' ? '開始轉檔' : '開始合併';
  go(DEFAULTS[mode]);
});

async function coursePost(path, payload) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function courseSourceTypeChanged() {
  const type = $('courseType').value;
  const source = $('courseSource');
  const button = $('courseSourcePick');
  const settings = {
    youtube: ['https://www.youtube.com/watch?v=...', '抓影片標題'],
    local_video: ['選擇影片或輸入完整路徑', '選擇影片'],
    local_mp3: ['選擇音訊檔或輸入完整路徑', '選擇音訊'],
    mp3_folder: ['選擇含支援音訊或影片的資料夾，每個檔各建一堂課', '選擇資料夾'],
    mp3_parts: ['選擇資料夾，裡面的支援音訊或影片依檔名排序合成同一堂課', '選擇資料夾']
  }[type];
  source.placeholder = settings[0];
  button.textContent = settings[1];
  clearCoursePreview();
  syncCourseArtifacts();
  if (type === 'mp3_folder' && source.value.trim()) previewCourseSource();
}

// 產物勾選：把相依前置自動補上並鎖住，順便回推 skillTreeMode。
// artifact keys 保持在函式內；hash 自動路由則必須等下列模組級狀態初始化完成。
const ENGINE_LABELS = {
  subtitle_manual: '作者上傳字幕（最準，限 YouTube）',
  groq: 'Groq whisper-large-v3（免費額度，有 SRT）',
  local_whisper: '本機 whisper（不花額度，很慢）',
  web: 'ChatEverywhere 網頁（自動化較脆弱）',
  subtitle_auto: 'YouTube 自動生成字幕（沒標點，品質最差）'
};
// 預設只走作者上傳字幕與 Groq；其餘要自己勾（很慢／脆弱／品質差）。
let coursePrio = [
  { key: 'subtitle_manual', on: true },
  { key: 'groq', on: true },
  { key: 'local_whisper', on: false },
  { key: 'web', on: false },
  { key: 'subtitle_auto', on: false }
];

// hash 入口：admin 卡片用 #course；YouTube fallback 用 #ytdl:<encoded URLs>。
// 這段需在 ENGINE_LABELS 與 coursePrio 完成初始化後才能觸發 tab 的同步呼叫鏈。
if (location.hash === '#course') {
  document.querySelector('.tab[data-mode="course"]').click();
} else if (location.hash === '#lexicon') {
  document.querySelector('.tab[data-mode="lexicon"]').click();
} else if (location.hash.startsWith('#ytdl:')) {
  try {
    const prefill = decodeURIComponent(location.hash.slice(6));
    document.querySelector('.tab[data-mode="ytdl"]').click();
    $('ytUrls').value = prefill;
  } catch (e) {}
} else {
  // 預設分頁＝課程整理SOP；用 click 觸發，初始化邏輯只留一份
  document.querySelector('.tab[data-mode="course"]').click();
}

function coursePrioMove(i, delta) {
  const j = i + delta;
  if (j < 0 || j >= coursePrio.length) return;
  const tmp = coursePrio[i]; coursePrio[i] = coursePrio[j]; coursePrio[j] = tmp;
  syncCourseArtifacts();
}

function coursePrioToggle(i, on) { coursePrio[i].on = on; syncCourseArtifacts(); }

function coursePrioRender() {
  $('coursePrioList').innerHTML = coursePrio.map((e, i) => `
    <li class="${e.on ? '' : 'off'}">
      <input type="checkbox" ${e.on ? 'checked' : ''} onchange="coursePrioToggle(${i}, this.checked)">
      <span class="ord">${i + 1}.</span>
      <span class="nm">${ENGINE_LABELS[e.key]}</span>
      <button onclick="coursePrioMove(${i}, -1)" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button onclick="coursePrioMove(${i}, 1)" ${i === coursePrio.length - 1 ? 'disabled' : ''}>↓</button>
    </li>`).join('');
}

function courseArtifactState() {
  const keys = ['video', 'mp3', 'transcript', 'review', 'rawSegments',
                'summary', 'report', 'mindmap', 'skillTree'];
  const type = $('courseType').value;
  const source = $('courseSource').value.trim().toLowerCase();
  // 前端不掃資料夾：只有明確選到單一 .mp3 才關掉轉檔，資料夾交由後端逐檔判斷。
  const mp3Source = type === 'local_mp3' && source.endsWith('.mp3');
  const want = {};
  keys.forEach(k => want[k] = $('a-' + k).checked);
  const needTranscript = want.summary || want.report || want.mindmap
    || want.skillTree || want.review || want.rawSegments;
  const transcript = want.transcript || needTranscript;
  const mp3 = want.mp3 && !mp3Source;
  let mode = 0;
  if (want.skillTree) mode = $('s-minimum').checked ? 3 : ($('s-teach').checked ? 2 : 1);
  const engine = $('courseEngine').value;
  const quality = $('courseQuality').value;
  const summaryStyle = $('s-dense').checked ? 'dense' : 'standard';
  const priority = coursePrio.filter(e => e.on).map(e => e.key);
  const batch = $('courseType').value === 'mp3_folder' && $('courseBatch').checked;
  return { mp3Source, want, needTranscript, transcript, mp3, mode, engine, priority,
           batch, quality, summaryStyle, isYoutube: $('courseType').value === 'youtube' };
}

function syncCourseArtifacts() {
  const st = courseArtifactState();
  $('courseSkillSub').style.display = st.want.skillTree ? 'flex' : 'none';
  if (!st.want.skillTree) { $('s-teach').checked = false; $('s-minimum').checked = false; }
  const lockTeach = st.want.skillTree && $('s-minimum').checked;
  if (lockTeach) $('s-teach').checked = true;

  courseSetBox('mp3', st.mp3, false, st.mp3Source);
  courseSetBox('transcript', st.transcript, st.needTranscript, false);
  courseSetBox('video', st.isYoutube && st.want.video, false, !st.isYoutube);
  ['summary', 'report', 'mindmap', 'skillTree', 'review', 'rawSegments']
    .forEach(k => courseSetBox(k, st.want[k], false, false));
  $('courseQualityRow').style.display = (st.isYoutube && st.want.video) ? 'flex' : 'none';
  $('courseSummarySub').style.display = st.want.summary ? 'flex' : 'none';
  if (!st.want.summary) { $('s-dense').checked = false; }
  $('s-teach').disabled = lockTeach;
  $('w-teach').classList.toggle('locked', lockTeach);

  const notes = [];
  if (st.mp3Source) notes.push('來源已經是 MP3，不需要轉檔這一步。');
  if (st.needTranscript) notes.push('🔒 的是下游需要而自動帶進來的前置，不能單獨取消。');
  if ($('courseType').value === 'mp3_folder' || $('courseType').value === 'mp3_parts') {
    notes.push('資料夾會只處理支援的音訊與影片；若全部來源已是 MP3，後端會自動略過轉檔。');
  }
  if (lockTeach) notes.push('「含最小案例」要先有教學，已自動帶上「含教學」。');
  if (st.want.review) notes.push('🔍 校對會呼叫 agy（Gemini）逐段修專有名詞，會吃額度；raw 稿保留不覆蓋，下游改吃校對版。');
  const nothing = !st.mp3 && !st.transcript && !st.want.video && !st.want.summary
    && !st.want.report && !st.want.mindmap && !st.want.skillTree
    && !st.want.review && !st.want.rawSegments;
  if (nothing) {
    notes.push('⚠️ 至少要勾一項產物才能建立任務。');
  }
  $('courseEngineRow').style.display = st.transcript ? 'flex' : 'none';
  const isYt = $('courseType').value === 'youtube';
  const subOnly = $('courseEngine').querySelector('option[value="subtitle_only"]');
  subOnly.disabled = !isYt;
  if (!isYt && st.engine === 'subtitle_only') { $('courseEngine').value = 'auto'; }
  const hints = {
    auto: '沒有作者字幕就用 Groq，都不行才退本機 whisper。',
    groq: '每天約 8 小時音訊免費額度，可中斷續跑，會產出 SRT。',
    assemblyai: '一次性 $50 免費額度（約 185 小時），8 小時課約 $1.2；輸出依說話者分段，沒有 SRT。',
    local_whisper: '完全不花額度，但 8 小時音訊在 CPU 上可能要跑十幾個小時。',
    subtitle_only: '抓不到作者上傳字幕就停下來標 blocked，不會自己改用轉錄。'
  };
  $('courseEngineHint').textContent = hints[$('courseEngine').value] || '';
  $('courseBatchWrap').style.display = ($('courseType').value === 'mp3_folder') ? 'flex' : 'none';
  $('coursePrioBox').style.display = (st.transcript && st.engine === 'auto') ? 'block' : 'none';
  coursePrioRender();
  if (st.engine === 'auto' && !st.priority.length) {
    notes.push('⚠️ 自動模式至少要留一個引擎。');
  }
  $('courseDepNote').innerHTML = notes.join('<br>');
}

function courseSetBox(key, checked, locked, disabled) {
  const box = $('a-' + key), wrap = $('w-' + key);
  box.checked = checked;
  box.disabled = locked || disabled;
  wrap.classList.toggle('locked', locked && !disabled);
  wrap.classList.toggle('off', disabled);
  let lk = wrap.querySelector('.lk');
  if (locked && !disabled) {
    if (!lk) { lk = document.createElement('span'); lk.className = 'lk'; wrap.appendChild(lk); }
    lk.textContent = '🔒 前置';
  } else if (lk) { lk.remove(); }
}

function clearCoursePreview() {
  const preview = $('coursePreview');
  const list = $('coursePreviewList');
  preview.classList.add('hide');
  $('coursePreviewSummary').textContent = '';
  list.replaceChildren();
}

async function previewCourseSource() {
  const preview = $('coursePreview');
  const list = $('coursePreviewList');
  const sourceType = $('courseType').value;
  const source = $('courseSource').value.trim();
  clearCoursePreview();
  if (sourceType !== 'mp3_folder' || !source) return;

  preview.classList.remove('hide');
  $('coursePreviewSummary').textContent = '正在讀取資料夾內容…';
  try {
    const data = await coursePost('/api/course/preview', { source });
    if ($('courseType').value !== sourceType || $('courseSource').value.trim() !== source) return;
    list.replaceChildren();
    if (!data.files.length) {
      $('coursePreviewSummary').textContent =
        `沒有找到支援的音訊或影片；略過 ${data.ignoredCount} 個非支援檔。`;
      return;
    }
    $('coursePreviewSummary').textContent =
      `共 ${data.supportedCount} 個可處理檔案；略過 ${data.ignoredCount} 個非支援檔。`;
    data.files.forEach(item => {
      const row = document.createElement('li');
      const name = document.createElement('span');
      const kind = document.createElement('span');
      name.textContent = item.name;
      kind.className = 'kind';
      kind.textContent = item.type === 'video' ? '影片' : '音訊';
      row.appendChild(name);
      row.appendChild(kind);
      list.appendChild(row);
    });
  } catch (error) {
    if ($('courseType').value !== sourceType || $('courseSource').value.trim() !== source) return;
    list.replaceChildren();
    $('coursePreviewSummary').textContent = `❌ 無法預覽：${error.message}`;
  }
}

async function suggestCourseName() {
  const source = $('courseSource').value.trim();
  if (!source) return;
  const button = $('courseSourcePick');
  const result = $('courseResult');
  button.disabled = true;
  result.textContent = $('courseType').value === 'youtube' ? '正在讀取影片標題…' : '正在帶入課程名稱…';
  try {
    const data = await coursePost('/api/course/name', {
      sourceType: $('courseType').value, source
    });
    $('courseName').value = data.courseName;
    result.textContent = `✅ 課程名稱已帶入：${data.courseName}`;
  } catch (error) {
    result.textContent = `❌ ${error.message}；也可以直接手動輸入課程名稱。`;
  } finally {
    button.disabled = false;
  }
}

async function pickCourseSource() {
  if ($('courseType').value === 'youtube') {
    await suggestCourseName();
    return;
  }
  const button = $('courseSourcePick');
  const result = $('courseResult');
  button.disabled = true;
  result.textContent = '請在本機視窗選擇來源…';
  try {
    const data = await coursePost('/api/course/pick', {
      kind: 'source', sourceType: $('courseType').value,
      initial: $('courseSource').value.trim()
    });
    if (data.cancelled) { result.textContent = '已取消選擇。'; return; }
    $('courseSource').value = data.picked;
    $('courseName').value = data.courseName;
    syncCourseArtifacts();
    await previewCourseSource();
    result.textContent = `✅ 已選擇：${data.picked}\n課程名稱：${data.courseName}`;
  } catch (error) {
    result.textContent = `❌ ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function pickCourseOutput() {
  const button = $('courseOutPick');
  const result = $('courseResult');
  button.disabled = true;
  result.textContent = '請在本機視窗選擇歸檔目錄…';
  try {
    const data = await coursePost('/api/course/pick', {
      kind: 'output', sourceType: $('courseType').value,
      initial: $('courseOut').value.trim() || DEFAULTS.course
    });
    if (data.cancelled) { result.textContent = '已取消選擇。'; return; }
    $('courseOut').value = data.picked;
    result.textContent = `✅ 歸檔目錄：${data.picked}`;
  } catch (error) {
    result.textContent = `❌ ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function copyCoursePrompt() {
  if (!lastCoursePrompt) return;
  try {
    await navigator.clipboard.writeText(lastCoursePrompt);
  } catch (error) {
    const area = document.createElement('textarea');
    area.value = lastCoursePrompt;
    document.body.appendChild(area);
    area.select();
    document.execCommand('copy');
    area.remove();
  }
  $('courseResult').textContent = '✅ AI 指令已複製。現在回到目前的 Hermes 對話貼上並送出。';
}

// 只把這次勾到的產物寫進指令，避免 AI 照舊做滿五份。
function buildCoursePrompt(manifestPaths, st) {
  const paths = Array.isArray(manifestPaths) ? manifestPaths : [manifestPaths];
  const wanted = [];
  if (st.want.video && st.isYoutube) {
    wanted.push('下載 MP4' + (st.quality === 'best' ? '（最佳畫質）' : `（${st.quality}）`));
  }
  if (st.mp3) wanted.push('轉檔 MP3');
  if (st.transcript) {
    const label = { auto: '', groq: '（指定用 Groq whisper-large-v3，需產出 SRT）',
      assemblyai: '（指定用 transcribe.py --mode diarize 說話者辨識，此模式無 SRT）',
      local_whisper: '（指定用本機 whisper CLI）',
      subtitle_only: '（只用作者上傳字幕，抓不到就標 blocked，不要改用轉錄）' }[st.engine] || '';
    wanted.push('逐字稿' + (st.engine === 'auto'
      ? '（依序嘗試：' + st.priority.join(' → ') + '，前面成功就不再往下）'
      : label));
  }
  if (st.want.rawSegments) wanted.push('原字分段版（保留原句、不改寫、不摘要化）');
  if (st.want.summary) {
    wanted.push('摘要' + (st.summaryStyle === 'dense'
      ? '（摘要總覽用 1-2 段高密度總結寫法）' : ''));
  }
  if (st.want.report) wanted.push('培訓報告');
  if (st.want.mindmap) wanted.push('心智圖（MD 與 Markmap HTML）');
  if (st.want.review) wanted.push('逐字稿校對版（呼叫 agy，只修專有名詞與同音誤字，raw 稿不覆蓋，下游吃校對版）');
  if (st.want.skillTree) wanted.push('技能樹模式 ' + st.mode);
  const multi = $('courseType').value === 'mp3_parts'
    ? '\n這是同一堂課切成多段的錄音：每段各自轉逐字稿，再依檔名順序合併成單一份完整逐字稿，之後的產物都只做一份，涵蓋整堂課。'
    : '';
  const head = paths.length > 1
    ? '執行 course-content-pipeline，以下 ' + paths.length + ' 個任務請**依序**處理，'
      + '每一個都跑到完再換下一個，中途不要停下來問我。'
      + '\nmanifest 清單：\n' + paths.map(x => '- ' + x).join('\n')
    : '執行 course-content-pipeline。' + '\nmanifest：' + paths[0];
  return head
    + '\n從第一個 pending 或 blocked stage 開始，逐階段親自實跑；不要使用子代理人或外部 AI API。'
    + '把 completion_criteria、evidence、outputs、error 原子寫回 manifest；completed 階段不要重跑。'
    + '\n這次只做以下項目：' + wanted.join('、')
    + '。manifest 中標成 skipped 的 stage 一律不要產出，也不要補做。'
    + multi;
}

// 進度來源是 manifest 檔本身，不需要 server 執行任何工作。
async function refreshCourseProgress() {
  const root = $('courseOut').value.trim() || DEFAULTS.course;
  try {
    const res = await fetch('/api/course/progress?root=' + encodeURIComponent(root));
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    renderCourseProgress(data.rows || []);
  } catch (e) {
    $('courseProgTitle').textContent = '讀不到進度：' + e.message;
  }
}

function renderCourseProgress(rows) {
  if (!rows.length) {
    $('courseProgTitle').textContent = '這個歸檔目錄下還沒有課程任務';
    $('courseProgPct').textContent = '';
    $('courseProgBar').firstElementChild.style.width = '0%';
    $('courseProgList').innerHTML = '';
    return;
  }
  const doneAll = rows.filter(r => r.status === 'completed').length;
  const overall = Math.round(rows.reduce((a, r) => a + r.percent, 0) / rows.length);
  $('courseProgTitle').textContent = `${rows.length} 堂課　已完成 ${doneAll}`;
  $('courseProgPct').textContent = overall + '%';
  $('courseProgBar').firstElementChild.style.width = overall + '%';
  const label = { completed: '完成', blocked: '卡住', running: '進行中',
                  pending: '等待中', unreadable: '讀不到' };
  $('courseProgList').innerHTML = rows.map(r => {
    const cls = r.status === 'completed' ? 'ok' : (r.status === 'blocked' ? 'bad' : '');
    const tail = r.status === 'completed' ? '完成'
      : `${label[r.status] || r.status}${r.currentStage ? '：' + r.currentStage : ''}`;
    return `<li>
      <span class="nm" title="${r.manifestPath}">${r.courseName}</span>
      <span class="minibar ${cls}"><i style="width:${r.percent}%"></i></span>
      <span class="st s-${r.status}">${r.doneStages}/${r.totalStages}　${tail}</span>
    </li>`;
  }).join('');
}

async function startCourseCreate() {
  const button = $('courseCreate');
  const result = $('courseResult');
  button.disabled = true;
  result.textContent = '建立中…';
  const st = courseArtifactState();
  try {
    const response = await fetch(st.batch ? '/api/course/batch' : '/api/course/create', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sourceType: $('courseType').value,
        source: $('courseSource').value.trim(),
        courseName: $('courseName').value.trim(),
        outputRoot: $('courseOut').value.trim() || DEFAULTS.course,
        options: {
          artifacts: {
            video: st.want.video, mp3: st.mp3, transcript: st.transcript,
            review: st.want.review, rawSegments: st.want.rawSegments,
            summary: st.want.summary, report: st.want.report,
            mindmap: st.want.mindmap, skillTree: st.want.skillTree
          },
          videoQuality: st.quality,
          summaryStyle: st.summaryStyle,
          transcriptEngine: st.engine,
          enginePriority: st.priority,
          skillTreeMode: st.mode,
          minimumExample: st.mode === 3
        }
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    const paths = data.manifestPaths || [data.manifestPath];
    lastCoursePrompt = buildCoursePrompt(paths, st);
    $('courseCopyPrompt').classList.remove('hide');
    result.textContent = paths.length > 1
      ? `✅ 已建立 ${paths.length} 個任務：\n` + paths.map(x => '　• ' + x).join('\n')
        + `\n\n下一步：按上方「複製 AI 執行指令」，一次貼給 AI 就會依序做完。`
      : `✅ 任務已建立：${paths[0]}\n\n下一步：按上方「複製 AI 執行指令」，回到目前的 Hermes 對話貼上並送出。`;
    refreshCourseProgress();
  } catch (error) {
    lastCoursePrompt = '';
    $('courseCopyPrompt').classList.add('hide');
    result.textContent = `❌ ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function startYtdl() {
  const urls = $('ytUrls').value.split('\n').map(s => s.trim()).filter(Boolean);
  const formats = [];
  if ($('fmtMp4').checked) formats.push('mp4');
  if ($('fmtMp3').checked) formats.push('mp3');
  if (!urls.length) { alert('請先貼上網址'); return; }
  if (!formats.length) { alert('請至少勾選 MP4 或 MP3'); return; }

  const res = await fetch('/api/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: 'ytdl', urls, formats, outputDir: $('ytOut').value || DEFAULTS.ytdl })
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  jobId = data.id;
  $('ytRun').disabled = true;
  $('ytCancel').classList.remove('hide');
  $('progress').classList.remove('hide');
  timer = setInterval(poll, 500);
  poll();
}

function go(path) { cur = path; load(); }

async function load() {
  $('path').value = cur;
  const res = await fetch(`/api/list?path=${encodeURIComponent(cur)}&kind=${mode}`);
  const data = await res.json();
  if (data.error) {
    $('files').innerHTML = `<li class="empty">${data.error}</li>`;
    files = []; checked.clear(); render(); return;
  }
  cur = data.path;
  $('path').value = cur;
  $('drives').innerHTML = data.drives.map(d => `<span class="chip" onclick="go('${d.replace(/\\/g, '\\\\')}')">${d}</span>`).join('');
  const up = data.parent ? `<span class="chip" onclick="go(${JSON.stringify(data.parent).replace(/"/g, '&quot;')})">⬆ 上一層</span>` : '';
  $('dirs').innerHTML = up + data.dirs.map(d =>
    `<span class="chip" onclick="go(${JSON.stringify(cur.replace(/\\+$/, '') + '\\' + d).replace(/"/g, '&quot;')})">📁 ${d}</span>`).join('');
  files = data.files;
  checked = new Set(files.map(f => f.path));
  render();
}

function render() {
  $('files').innerHTML = files.length ? files.map((f, i) => `
    <li>
      <input type="checkbox" ${checked.has(f.path) ? 'checked' : ''} onchange="toggle(${i}, this.checked)">
      <span class="fname" title="${f.name}">${f.name}</span>
      <span class="fsize">${mb(f.size)}</span>
    </li>`).join('')
    : `<li class="empty">這個資料夾沒有${mode === 'video' ? '影片' : '音訊'}檔。</li>`;
  const n = checked.size;
  $('count').textContent = `已選 ${n} / ${files.length} 個`;
  $('run').textContent = mode === 'video' ? '開始轉檔' : '開始合併';
  $('run').disabled = !!jobId || n === 0 || (mode === 'audio' && n < 2);
  $('hint').textContent = mode === 'audio' && n === 1 ? '合併至少要選 2 個檔案' : '';
}

function toggle(i, on) { on ? checked.add(files[i].path) : checked.delete(files[i].path); render(); }
function pick(all) { checked = all ? new Set(files.map(f => f.path)) : new Set(); render(); }

async function start() {
  const list = files.filter(f => checked.has(f.path)).map(f => f.path);
  const res = await fetch('/api/start', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ kind: mode === 'video' ? 'convert' : 'merge', files: list, outputDir: cur })
  });
  const data = await res.json();
  if (data.error) { alert(data.error); return; }
  jobId = data.id;
  $('progress').classList.remove('hide');
  $('cancel').classList.remove('hide');
  render();
  timer = setInterval(poll, 500);
  poll();
}

async function cancel() {
  if (!jobId) return;
  await fetch(`/api/cancel?id=${jobId}`, { method: 'POST' });
}

async function poll() {
  if (!jobId) return;
  const data = await (await fetch(`/api/status?id=${jobId}`)).json();
  const finished = data.items.filter(i => i.status === 'done').length;
  $('jobTitle').textContent = data.kind === 'merge' ? '合併中'
    : `${data.kind === 'ytdl' ? '下載中' : '轉檔中'}（${finished}/${data.items.length}）`;
  $('jobPct').textContent = `${data.overall}%　已用 ${hhmmss(data.elapsed)}`;
  $('jobBar').firstElementChild.style.width = data.overall + '%';

  $('jobItems').innerHTML = data.items.map(i => {
    const cls = i.status === 'failed' ? 'bad' : i.status === 'done' ? 'ok' : '';
    const tail = i.duration > 0 && i.status === 'running'
      ? `${hhmmss(i.current)} / ${hhmmss(i.duration)}　${i.percent}%`
      : ({ waiting: '等待中', done: '完成', failed: '失敗', cancelled: '已取消', running: i.percent + '%' })[i.status];
    return `<div class="job-item">
      <div class="job-head"><b title="${i.name}">${i.name}</b><span class="pct">${tail}</span></div>
      <div class="bar ${cls}"><i style="width:${i.status === 'done' ? 100 : i.percent}%"></i></div>
      ${i.error ? `<div class="err">${i.error}</div>` : ''}
      ${i.output ? `<div class="out">→ ${i.output}</div>` : ''}
    </div>`;
  }).join('');

  if (data.done) {
    clearInterval(timer);
    jobId = null;
    $('cancel').classList.add('hide');
    $('ytCancel').classList.add('hide');
    $('ytRun').disabled = false;
    $('jobTitle').textContent = data.message;
    $('jobNote').innerHTML = data.outputs.length
      ? `輸出位置：${data.outputDir}<br>` + data.outputs.map(o => `✅ ${o}`).join('<br>')
      : '';
    render();
    if (data.kind !== 'ytdl') load();
  }
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass                                  # 不要洗畫面

    def _cors(self):
        """讓 https://my-teaching-tools-2b36c.web.app/admin/ 打得到這個本機服務。
        Chrome 把 127.0.0.1 當安全來源，但私有網路請求要求下面第三個標頭。"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self._cors()
        self.end_headers()

    def _json(self, data: dict, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if LEXADMIN is not None:
            handled = LEXADMIN.handle("GET", url.path)
            if handled is not None:
                code, ctype, payload = handled
                self._send(code, payload, ctype)
                return

        if url.path in ("/", "/index.html"):
            page = (PAGE
                    .replace("%DEFAULT_VIDEO%", json.dumps(DEFAULT_VIDEO_DIR))
                    .replace("%DEFAULT_AUDIO%", json.dumps(DEFAULT_AUDIO_DIR))
                    .replace("%DEFAULT_YTDL%", json.dumps(DEFAULT_YTDL_DIR))
                    .replace("%DEFAULT_COURSE%", json.dumps(COURSE_ROOT)))
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")

        elif url.path == "/api/health":
            self._json({
                "status": "ok",
                "service": "video-to-mp3",
                "version": SERVICE_VERSION,
                "courseRoot": COURSE_ROOT,
            })

        elif url.path == "/api/list":
            path = query.get("path", [DEFAULT_VIDEO_DIR])[0]
            kind = query.get("kind", ["video"])[0]
            self._json(list_folder(path, kind))

        elif url.path == "/api/status":
            job = JOBS.get(query.get("id", [""])[0])
            self._json(job.snapshot() if job else {"error": "找不到這個工作"}, 200 if job else 404)

        elif url.path == "/api/course/progress":
            try:
                rows = scan_course_progress(query.get("root", [""])[0] or None)
            except OSError as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"rows": rows, "count": len(rows)})

        elif url.path == "/api/imgpdf/scan":
            folder = query.get("dir", [""])[0]
            if not folder:
                self._json({"error": "沒有指定資料夾"}, 400)
                return
            if not Path(folder).is_dir():
                self._json({"error": f"找不到資料夾：{folder}"}, 400)
                return
            images = scan_images(folder)
            self._json({"dir": folder, "count": len(images),
                        "names": [Path(p).name for p in images[:5]]})

        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if LEXADMIN is not None and url.path.startswith("/api/lexicon"):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b''
            handled = LEXADMIN.handle("POST", url.path, raw)
            if handled is not None:
                code, ctype, payload = handled
                self._send(code, payload, ctype)
                return

        if url.path == "/api/course/preview":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON body 必須是 object")
                source = payload.get("source", "")
                if not isinstance(source, str):
                    raise ValueError("source 必須是字串")
                preview = preview_course_folder(source)
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json(preview)

        elif url.path == "/api/course/name":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON body 必須是 object")
                source_type = payload.get("sourceType", "")
                source = payload.get("source", "")
                if not isinstance(source_type, str) or not isinstance(source, str):
                    raise ValueError("sourceType 與 source 必須是字串")
                course_name = derive_course_name(source_type.strip(), source.strip())
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({"courseName": course_name})

        elif url.path == "/api/course/pick":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON body 必須是 object")
                kind = payload.get("kind", "")
                source_type = payload.get("sourceType", "")
                initial = payload.get("initial", "")
                if not all(isinstance(value, str) for value in (kind, source_type, initial)):
                    raise ValueError("picker 參數必須是字串")
                picked = pick_course_path(kind.strip(), source_type.strip(), initial.strip())
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            if not picked:
                self._json({"cancelled": True})
                return
            response = {"picked": picked}
            if kind == "source":
                response["courseName"] = derive_course_name(source_type, picked)
            self._json(response)

        elif url.path == "/api/course/batch":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON body 必須是 object")
                for field in ("sourceType", "source", "courseName"):
                    if not isinstance(payload.get(field, ""), str):
                        raise ValueError(f"{field} 必須是字串")
                if not isinstance(payload.get("options", {}), dict):
                    raise ValueError("options 必須是 object")
                results = create_course_batch(
                    (payload.get("sourceType") or "").strip(),
                    (payload.get("source") or "").strip(),
                    (payload.get("courseName") or "").strip(),
                    options=payload.get("options") or {},
                    output_root=(payload.get("outputRoot") or COURSE_ROOT).strip(),
                )
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({
                "manifestPaths": [str(path) for _, path in results],
                "count": len(results),
                "nextAction": "把「複製 AI 執行指令」的內容一次貼給支援 course-content-pipeline 的 AI",
            }, 201)

        elif url.path == "/api/course/create":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON body 必須是 object")
                for field in ("sourceType", "source", "courseName"):
                    if not isinstance(payload.get(field, ""), str):
                        raise ValueError(f"{field} 必須是字串")
                if not isinstance(payload.get("outputRoot", COURSE_ROOT), str):
                    raise ValueError("outputRoot 必須是字串")
                if not isinstance(payload.get("options", {}), dict):
                    raise ValueError("options 必須是 object")
                manifest, manifest_path = create_course_manifest(
                    source_type=(payload.get("sourceType") or "").strip(),
                    source_val=(payload.get("source") or "").strip(),
                    course_name=(payload.get("courseName") or "").strip(),
                    options=payload.get("options") or {},
                    output_root=(payload.get("outputRoot") or COURSE_ROOT).strip(),
                )
            except (json.JSONDecodeError, ValueError, TypeError, OSError) as exc:
                self._json({"error": str(exc)}, 400)
                return
            self._json({
                "manifestPath": str(manifest_path),
                "courseDir": manifest["courseDir"],
                "nextAction": "請交給支援 course-content-pipeline Skill 的 AI，從 manifest 的第一個 pending stage 續跑",
            }, 201)

        elif url.path == "/api/start":
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                self._json({"error": f"送來的資料不是合法 JSON：{exc}"}, 400)
                return
            kind = payload.get("kind", "convert")

            if kind == "imgpdf":
                folder = (payload.get("dir") or "").strip()
                name = (payload.get("name") or "").strip().rstrip(". ")
                groq_key = (payload.get("groqKey") or "").strip() or env_groq_key()
                if not Path(folder).is_dir():
                    self._json({"error": f"找不到資料夾：{folder}"}, 400)
                    return
                if name and re.search(r'[<>:"/\\|?*]', name):
                    self._json({"error": r'名稱不能有 < > : " / \ | ? * 這些字元'}, 400)
                    return
                # 名稱留空＝走自動辨識，那就一定要有 key，不然開跑才失敗很浪費時間
                if not name and not groq_key and not env_groq_key():
                    self._json({"error": "名稱留空要自動辨識書名，但沒收到 Groq API Key。"
                                         "請重新整理 /admin/ 頁面登入，或自己填名稱。"}, 400)
                    return
                images = scan_images(folder)
                if not images:
                    self._json({"error": f"這個資料夾裡沒有圖檔：{folder}"}, 400)
                    return
                job = start_job("imgpdf", images, folder,
                                target_name=name, groq_key=groq_key)
                self._json({"id": job.id, "count": len(images), "auto": not name,
                            "targetDir": str(Path(folder) / name) if name else ""})
                return

            if kind == "ytdl":
                urls = [u.strip() for u in payload.get("urls", []) if u.strip()]
                formats = [f for f in payload.get("formats", []) if f in YTDL_FORMATS]
                if not urls:
                    self._json({"error": "請先貼上網址"}, 400)
                    return
                if not formats:
                    self._json({"error": "請至少勾選 MP4 或 MP3"}, 400)
                    return
                entries = [{"url": u, "format": f} for u in urls for f in formats]
                job = start_job("ytdl", entries, payload.get("outputDir") or DEFAULT_YTDL_DIR)
                self._json({"id": job.id})
                return

            paths = [p for p in payload.get("files", []) if Path(p).is_file()]
            output_dir = payload.get("outputDir") or (str(Path(paths[0]).parent) if paths else "")

            if not paths:
                self._json({"error": "沒有選到有效的檔案"}, 400)
                return
            if kind == "merge" and len(paths) < 2:
                self._json({"error": "合併至少需要 2 個檔案"}, 400)
                return

            job = start_job(kind, paths, output_dir)
            self._json({"id": job.id})

        elif url.path == "/api/imgpdf/identify":
            # 只辨識、不動檔案，用來單獨驗證命名結果（網頁的自動流程是在 job 裡辨識）
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as exc:
                self._json({"error": f"送來的資料不是合法 JSON：{exc}"}, 400)
                return
            folder = (payload.get("dir") or "").strip()
            images = scan_images(folder)
            if not images:
                self._json({"error": f"這個資料夾裡沒有圖檔：{folder}"}, 400)
                return
            try:
                self._json(identify_book_name(
                    images, (payload.get("groqKey") or "").strip() or env_groq_key()))
            except Exception as exc:                          # noqa: BLE001
                self._json({"error": str(exc)}, 400)

        elif url.path == "/api/imgpdf/pick":
            picked = pick_image_dialog()
            if not picked:
                self._json({"cancelled": True})
                return
            folder = str(Path(picked).parent)
            images = scan_images(folder)
            self._json({"picked": picked, "dir": folder, "count": len(images),
                        "names": [Path(p).name for p in images[:5]],
                        "suggest": Path(folder).name})

        elif url.path == "/api/cancel":
            job = JOBS.get(query.get("id", [""])[0])
            if job:
                job.cancelled = True
                self._json({"ok": True})
            else:
                self._json({"error": "找不到這個工作"}, 404)

        else:
            self._json({"error": "not found"}, 404)


def port_in_use(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not Path(FFMPEG).exists() and not shutil.which("ffmpeg"):
        print("[錯誤] 找不到 ffmpeg，請先安裝或加入 PATH。")
        return 1

    if port_in_use(PORT):
        print(f"[提示] 127.0.0.1:{PORT} 已經有服務在跑，直接開瀏覽器。")
        webbrowser.open(f"http://127.0.0.1:{PORT}/")
        return 0

    for folder in (DEFAULT_VIDEO_DIR, DEFAULT_AUDIO_DIR, DEFAULT_YTDL_DIR):
        Path(folder).mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("=" * 52)
    print("  🎬 影音工具（YT下載 / 影片轉MP3 / 錄音合併）")
    print(f"  網址：http://127.0.0.1:{PORT}/")
    print("  關閉這個視窗就會停止服務。")
    print("=" * 52)
    webbrowser.open(f"http://127.0.0.1:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[結束] 服務已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
