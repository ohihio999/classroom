import os
import re
import hashlib
from datetime import datetime
from pathlib import Path
from playwright.sync_api import Page
import config


def _sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def _extract_post_id(url: str) -> str:
    # 嘗試從 URL 抓 posts/ 或 ?story_fbid= 後的數字
    match = re.search(r'(?:posts|story_fbid)[=/](\d+)', url)
    if match:
        return match.group(1)[-6:]
    # fallback: 用 URL hash
    return hashlib.md5(url.encode()).hexdigest()[:6]


def download_images(page: Page, img_urls: list[str], post_url: str) -> list[str]:
    """
    使用 Playwright page context 下載圖片（帶 FB session cookies）。
    回傳下載成功的本機路徑清單（相對於 vault root 的 Obsidian wikilink 格式）。
    """
    os.makedirs(config.ATTACHMENT_DIR, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    post_id = _extract_post_id(post_url)

    local_paths = []
    for idx, img_url in enumerate(img_urls, start=1):
        try:
            # 用 page context 發 request，帶入完整 session cookies
            response = page.context.request.get(img_url, headers={
                "Referer": "https://www.facebook.com/",
                "User-Agent": page.evaluate("navigator.userAgent"),
            })
            if response.status != 200:
                print(f"  [跳過] 圖片 {idx} 回應 {response.status}")
                continue

            # 決定副檔名
            content_type = response.headers.get("content-type", "image/jpeg")
            ext = content_type.split("/")[-1].split(";")[0]
            if ext in ("jpeg", "jpg", "pjpeg"):
                ext = "jpg"
            elif ext not in ("png", "gif", "webp"):
                ext = "jpg"

            filename = f"{date_str}-fb{post_id}-{idx:02d}.{ext}"
            filepath = os.path.join(config.ATTACHMENT_DIR, filename)
            Path(filepath).write_bytes(response.body())
            local_paths.append(f"附件/{filename}")
            print(f"  [OK] 圖片 {idx} → {filename}")

        except Exception as e:
            print(f"  [錯誤] 圖片 {idx} 下載失敗：{e}")

    return local_paths
