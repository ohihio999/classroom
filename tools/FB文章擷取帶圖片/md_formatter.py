from datetime import datetime


def format_note(post: dict, img_local_paths: list[str], source_url: str) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    author = post.get("author", "未知作者")
    text = post.get("text", "").strip()
    timestamp = post.get("timestamp", "")

    # 標題：作者 + 正文前 30 字
    short_text = text[:30].replace("\n", " ") if text else "FB 貼文"
    title = f"{author} - {short_text}"

    # 圖片 wikilink
    img_lines = "\n".join(f"![[{p}]]" for p in img_local_paths)

    frontmatter = f"""---
title: "{title}"
source: {source_url}
author: {author}
created: {today}
published: {timestamp}
platform: facebook
tags:
  - clippings
  - facebook
---"""

    body_parts = [frontmatter, "", text]
    if img_lines:
        body_parts += ["", img_lines]

    return "\n".join(body_parts)
