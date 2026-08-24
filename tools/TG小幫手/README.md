# TG小幫手

這個工具是放在 `classroom` 裡的新子專案，目標是做成以 Telegram 為入口的個人自動化助理。

## 專案結構

```text
tools/TG小幫手/
  README.md
  requirements.txt
  .env.example
  bot.py
```

## 目前定位

- Telegram 當操作窗口
- 可接收指令、主動推播、回報執行結果
- 後續會逐步接上瀏覽器自動化、Email、日程、收據帳單流程、情報追蹤

## 第一階段要做什麼

1. 建立專案骨架
2. 建立 Telegram Bot 基礎收發訊息
3. 建立任務路由器
4. 再逐步接其他能力

## 第 1 課完成標準

- 已有 Telegram Bot Token
- `.env` 已設定 `TELEGRAM_BOT_TOKEN`
- 執行 `python bot.py`
- 在 Telegram 傳訊息給 Bot 時，Bot 會回 `收到：你的訊息`

## 文件位置

- 工具程式：`C:\Users\admin\Desktop\classroom\tools\TG小幫手\`
- 工作筆記：`C:\Users\admin\Desktop\本機MD檔\classroom\TG小幫手.md`

## 備註

之後每次教學會同步更新這份 README 與對應工作筆記。
