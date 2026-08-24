# 影音課程內容流水線 — 工作筆記

日期：2026-08-17
狀態：completed（本機實作與驗收完成；未部署 Firebase、未 git push）

## 目標

建立一個總控課程內容流水線，支援 YouTube URL、本機錄影、本機 MP3、MP3 資料夾批次，透過獨立子 Skill 產出逐字稿、摘要、培訓報告、心智圖與可選技能樹，並以 `course-manifest.json` 支援續跑。

## 主要產物

- 共用 canonical Skill：`C:\Users\admin\.codex\skills\course-content-pipeline\SKILL.md`
- 測試案例：`C:\Users\admin\.codex\skills\course-content-pipeline\test_cases.json`
- Validator：`C:\Users\admin\.codex\skills\course-content-pipeline\scripts\quick_validate.py`
- Hermes 薄路由：`C:\Users\admin\AppData\Local\hermes\skills\course-content-pipeline\SKILL.md`
- 本機服務：`C:\Users\admin\Desktop\classroom\video-to-mp3\server.py`
- 單元測試：`C:\Users\admin\Desktop\classroom\video-to-mp3\tests\test_course_pipeline.py`
- Admin 卡片 source：`C:\Users\admin\Desktop\classroom\tools\admin\index.html`

## 執行入口

- 服務：在 `video-to-mp3` 目錄執行 `python server.py`
- 課程頁：`http://127.0.0.1:8767/#course`
- Health：`GET http://127.0.0.1:8767/api/health`
- 建立任務：`POST http://127.0.0.1:8767/api/course/create`

## 固定規則

- Canonical 歸檔：`D:\本機MD檔\30_研究\課程逐字稿整理\YYYYMMDD_課程名\`
- 同名資料夾使用 `-2`、`-3` 遞增，不覆蓋。
- Manifest stages：`acquisition`、`media_to_mp3`、`transcription`、`training_pack`、`skill_tree`、`archive`。
- 合法狀態：`pending`、`in_progress`、`completed`、`blocked`、`skipped`、`cancelled`。
- 每個 stage 必須有 `completion_criteria`、`evidence`、`outputs`、`error`。
- YouTube 固定 MP4、MP3、metadata、字幕或 raw transcript；無字幕才轉錄。
- Skill tree mode 3 強制包含最小案例。
- 原始媒體不得覆蓋。

## 驗收證據

- `python scripts/quick_validate.py`：exit 0；validator、3 test cases、protected governance 全 PASS。
- `python -m unittest discover -s tests -v`：Ran 15 tests，OK。
- `python -m py_compile server.py`：exit 0。
- `git diff --check -- server.py tests/test_course_pipeline.py ../tools/admin/index.html`：exit 0。
- JavaScript parser：服務 1 支與 Admin 2 支，3/3 exit 0。
- 真實 health：HTTP 200、version 1.3、courseRoot 正確。
- 真實 create：HTTP 201，曾建立 smoke manifest 並讀回 schema／stages。
- Fresh-context A–H：全部 PASS；High、Medium、Low findings 均無；總判定 ACCEPT。
- 獨立報告：`C:\Users\admin\AppData\Local\hermes\cache\delegation\subagent-summary-0-20260817_093531_565364.txt`

## 未執行的外部動作

- 未部署 Firebase 正式 Admin 網站。
- 未 git push。
- 未刪除 `C:\Users\admin\AppData\Local\Temp\20260817_PIPELINE-SMOKE\`（刪除需另行確認）。

## A/B 換手提示

B 機若要接手，先讀本檔、完成進度檔及 canonical Skill；再執行 validator、15 tests 與 `/api/health`。不得同步各機憑證、gateway、bot token 或 session 狀態。

## Firebase 正式部署與使用教學（2026-08-17）

### 正式入口

- Admin：`https://my-teaching-tools-2b36c.web.app/admin/`
- 本機課程頁：`http://127.0.0.1:8767/#course`
- 正式部署：`firebase deploy --only hosting` exit 0；24 files 中只 upload 1 new file；fresh-context ACCEPT。

### 架構

Firebase 只承載 Admin 靜態卡片與登入頁；影片、音訊、manifest 與 AI 流水線都在同一台 Windows 的 8767 與 Hermes 執行。Admin 以 `location.href` 頂層導向 localhost，不從 HTTPS fetch HTTP API，因此不涉及 CORS；仍必須先啟動本機服務。

### 每次使用

1. 開 Admin 正式網址，以 `ohihio@gmail.com` 登入。
2. 找「影音課程流水線」卡片；若 8767 未啟動，先按右下 🚀，瀏覽器詢問時允許開啟 MediaTools Protocol。黑色服務視窗要保持開啟。
3. 點卡片本體，進入 `http://127.0.0.1:8767/#course`。
4. 選來源類型，填 YouTube URL／本機檔案完整路徑／MP3 資料夾完整路徑、課程名稱、技能樹 mode 0–3；保留預設 D 槽歸檔根目錄。
5. 按「建立可續跑任務」，複製畫面顯示的 `course-manifest.json` 絕對路徑。
6. 回 Hermes 貼：`執行 course-content-pipeline。manifest：<完整路徑>。從第一個 pending stage 開始，逐階段實跑並把 evidence、outputs、error 原子寫回 manifest；completed 階段不要重跑。`

### 直接交給 Hermes 建立新任務

`執行 course-content-pipeline。來源：<YouTube URL 或本機完整路徑>；課程名稱：<名稱>；技能樹模式：0/1/2/3。先建立 manifest，再從第一個 pending stage 做到固定課程包完成；每階段必須實跑驗收並更新 evidence。`

### 中斷後續跑

`繼續 course-content-pipeline。manifest：<course-manifest.json 完整路徑>。從第一個 pending 或 blocked stage 續跑，不重跑 completed；先讀 error 與 evidence。`

### 技能樹模式

- 0：不做技能樹。
- 1：技能樹。
- 2：技能樹＋教學。
- 3：技能樹＋教學＋每個節點最小案例。

### 常見問題

- `ERR_CONNECTION_REFUSED`：先按卡片 🚀，或雙擊 `C:\Users\admin\Desktop\classroom\video-to-mp3\start.bat`。
- 看不到新卡片：重新整理／Ctrl+F5，並確認登入帳號是 `ohihio@gmail.com`。
- 只看到「任務已建立」：正常；此步只建立 manifest，還要把 manifest 路徑交給 Hermes。
- 關掉黑色服務視窗：8767 隨即停止，下次要重新按 🚀。
- 換另一台電腦：Firebase 卡片看得到，但要在那台電腦部署 8767 服務並重新註冊 `mediatools://`。

