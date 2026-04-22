# classroom — 公司日常維運工具總專案

## 對話開始時請先讀
進度與最近更動都在 Obsidian：`本機MD檔/classroom/工作筆記.md`

## 專案背景
- 使用者：系統工程師
- 用途：公司日常維運工具（簽收、簽核、通知、追蹤等內部作業）
- 技術棧：Firebase（Firestore）+ 靜態 HTML/JS 工具頁

## 工作模式
- **加新工具**：對 Claude 說「我想做一個 XXX 工具」→ Claude 會建 `tools/<工具名>/` 子資料夾
- **結束工作**：對 Claude 說「**收工**」→ 自動 commit + push + 更新 Obsidian 工作筆記
- **接續工作**：對 Claude 說「讀工作筆記、告訴我上次做到哪」

## 工作桌 + 三個家
- 💻 本機工作桌：`C:\Users\admin\Desktop\classroom\`
- 🐙 GitHub repo：`ohihio999/classroom`（公開，網頁的家）
- 📘 Obsidian 駕駛艙：`本機MD檔/classroom/工作筆記.md`（想法的家）

## 工具清單
（之後加新工具時會自動更新）
- （尚無）

## 工作注意事項
- 人員資料一律去識別化（只用編號或代號）
- commit 訊息要寫清楚做了什麼 + 為什麼
- 收工前說「收工」讓 Claude 同步三方
