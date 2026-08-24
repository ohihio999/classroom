import re
from playwright.sync_api import Page
import config


def _expand_photos(page: Page, post_url: str) -> list[str]:
    """
    若貼文包含相簿（多張圖），依序點開相片連結取得原圖 URL。
    """
    img_urls = []
    photo_links = page.query_selector_all('a[href*="/photo/"]')
    seen = set()
    for link in photo_links:
        href = link.get_attribute("href") or ""
        if href in seen:
            continue
        seen.add(href)
        try:
            full_url = href if href.startswith("http") else f"https://www.facebook.com{href}"
            photo_page = page.context.new_page()
            photo_page.goto(full_url, timeout=config.PAGE_TIMEOUT)
            photo_page.wait_for_load_state("domcontentloaded")
            # 找最高解析度圖片（role=img 的 src 或 meta og:image）
            og_img = photo_page.get_attribute('meta[property="og:image"]', "content")
            if og_img:
                img_urls.append(og_img)
            photo_page.close()
        except Exception as e:
            print(f"  [相簿展開失敗] {href}: {e}")
    return img_urls


def parse_post(page: Page, url: str) -> dict:
    """
    解析 FB 貼文頁面，回傳：
    {
        "author": str,
        "text": str,
        "img_urls": list[str],
        "timestamp": str,
    }
    """
    page.goto(url, timeout=config.PAGE_TIMEOUT)
    page.wait_for_load_state("domcontentloaded")

    # 等待貼文內容出現
    try:
        page.wait_for_selector('[data-ad-comet-preview="message"], [data-testid="post_message"]', timeout=10000)
    except Exception:
        pass  # 繼續嘗試

    result = {"author": "", "text": "", "img_urls": [], "timestamp": ""}

    # 作者名稱（OG metadata 最穩定）
    result["author"] = page.get_attribute('meta[property="og:title"]', "content") or ""

    # 貼文正文（優先從 og:description 取，完整版從 DOM 取）
    og_desc = page.get_attribute('meta[property="og:description"]', "content") or ""
    # 嘗試從 DOM 取完整文字
    text_el = page.query_selector('[data-ad-comet-preview="message"]') or \
              page.query_selector('[data-testid="post_message"]')
    if text_el:
        result["text"] = text_el.inner_text()
    else:
        result["text"] = og_desc

    # 時間戳
    time_el = page.query_selector('abbr[data-utime], abbr[data-shorten]')
    if time_el:
        result["timestamp"] = time_el.get_attribute("title") or time_el.inner_text()

    # OG 主圖
    og_img = page.get_attribute('meta[property="og:image"]', "content") or ""
    if og_img:
        result["img_urls"].append(og_img)

    # 相簿多圖
    album_imgs = _expand_photos(page, url)
    for img in album_imgs:
        if img not in result["img_urls"]:
            result["img_urls"].append(img)

    return result
