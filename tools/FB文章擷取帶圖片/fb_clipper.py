"""
FB 文章擷取帶圖片 — 主程式

用法：
  python fb_clipper.py "https://www.facebook.com/..."
  python fb_clipper.py "https://www.facebook.com/..." --headless

第一次建議不加 --headless，確認瀏覽器能正常開啟且已登入 FB。
"""

import sys
import os
import argparse
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright

import config
import fb_parser
import image_downloader
import md_formatter


def main():
    parser = argparse.ArgumentParser(description="擷取 FB 文章與圖片存入 Obsidian")
    parser.add_argument("url", help="Facebook 貼文 URL")
    parser.add_argument("--headless", action="store_true", help="無頭模式（不顯示瀏覽器視窗）")
    args = parser.parse_args()

    print(f"[開始] 擷取：{args.url}")

    with sync_playwright() as p:
        # 使用 Chrome persistent context，帶入真實 FB session
        user_data_path = os.path.join(config.CHROME_USER_DATA, config.CHROME_PROFILE)
        print(f"[Session] 使用 Chrome profile：{user_data_path}")

        browser = p.chromium.launch_persistent_context(
            user_data_dir=user_data_path,
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        page = browser.new_page()

        try:
            print("[解析] 讀取貼文內容...")
            post = fb_parser.parse_post(page, args.url)
            print(f"  作者：{post['author']}")
            print(f"  正文前 50 字：{post['text'][:50]}")
            print(f"  找到圖片 URL：{len(post['img_urls'])} 張")

            print("[下載] 圖片下載中...")
            local_paths = image_downloader.download_images(page, post["img_urls"], args.url)
            print(f"  成功下載：{len(local_paths)} 張")

            print("[輸出] 產生 Markdown...")
            content = md_formatter.format_note(post, local_paths, args.url)

            # 輸出檔名
            author_safe = post["author"][:20].replace(" ", "_").replace("/", "_")
            date_str = datetime.now().strftime("%Y%m%d")
            filename = f"{date_str}-clip-{author_safe}-FB.md"
            # 如果同名已存在，加流水號
            filepath = os.path.join(config.INBOX_DIR, filename)
            counter = 1
            while Path(filepath).exists():
                stem = filename.removesuffix(".md")
                filepath = os.path.join(config.INBOX_DIR, f"{stem}-{counter}.md")
                counter += 1

            os.makedirs(config.INBOX_DIR, exist_ok=True)
            Path(filepath).write_text(content, encoding="utf-8")
            print(f"[完成] 筆記已存到：{filepath}")

        except Exception as e:
            print(f"[錯誤] {e}")
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
