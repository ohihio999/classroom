# SOP：影音工具（影片轉 MP3 / 錄音檔合併）

## 目的

在網頁上選檔案，由本機 ffmpeg 當場處理，即時看到進度，輸出寫回原資料夾。
檔案不上傳、不經雲端。

---

## 一次性安裝

### 1. 安裝 ffmpeg

```powershell
winget install Gyan.FFmpeg
```

安裝完重開終端機確認：

```powershell
ffmpeg -version
```

### 2. 確認 Python

```powershell
python --version
```

---

## 每次使用流程

### 步驟一：啟動本機服務

三種方式都可以：

- **從 admin 頁**：<https://my-teaching-tools-2b36c.web.app/admin/> →「影片轉MP3 / 錄音合併」卡片右下角的 🚀 →
  瀏覽器問「要開啟 MediaTools Protocol 嗎」按允許 → 服務啟動並自動開分頁
- 雙擊 `start.bat`
- `python server.py`

服務跑起來後那個黑窗要留著，關掉視窗＝停止服務。
服務已經在跑時再按一次只會開分頁，不會重複啟動。

### 步驟二：進入工具頁

服務在跑的話，直接點 admin 卡片本體（或開 <http://127.0.0.1:8767/>）。

### 步驟三：選檔、開始

- 上方分頁切「影片轉 MP3」或「錄音檔合併」
- 路徑列可直接輸入，或點資料夾／磁碟機鑽進去；預設開在 `C:\OBS\影片轉mp3`、`C:\OBS\影片檔合併`
- 勾選要處理的檔案 → 開始
- 每支檔案一條進度條，可中途取消（取消會刪掉半成品）

### 步驟四：確認輸出

- **轉 MP3**：同資料夾同檔名的 `.mp3`（192 kbps）
- **合併**：同資料夾的 `merged_YYYYMMDD_HHMMSS.<原副檔名>`，用 `-c copy` 不重新編碼，
  所以只能合併同編碼格式的檔案（例如一批 `.m4a`）

支援格式：

- 影片：`mp4` `mov` `mkv` `avi` `webm` `m4v` `flv` `wmv` `ts`
- 音訊：`m4a` `mp3` `wav` `aac` `flac` `ogg` `opus` `wma`

---

## 專案結構

```text
video-to-mp3\
├── server.py    ← 服務 + 網頁 UI 全包在這支（port 8767）
├── start.bat    ← 雙擊啟動，也是 mediatools:// 協定叫起的對象
└── SOP.md
```

本機服務 port 分配：8765 Skill 管理器、8766 LINE 對話整理、**8767 影音工具**。

---

## mediatools:// 協定（🚀 按鈕怎麼運作的）

網頁本身沒辦法直接執行本機程式，所以註冊了一個自訂協定：

```text
HKCU\Software\Classes\mediatools\shell\open\command
  → "C:\Users\admin\Desktop\classroom\video-to-mp3\start.bat" "%1"
```

admin 卡片的 🚀 按鈕就是連到 `mediatools://open`。只在這台機器有效，換電腦要重新註冊。

不想要了就刪掉登錄檔那個 key：

```powershell
Remove-Item "HKCU:\Software\Classes\mediatools" -Recurse
```

---

## 舊做法（2026-07-31 已刪除）

`C:\OBS\影片轉mp3.bat`、`C:\OBS\錄音黨合併.bat`、`C:\OBS\merge.ps1`、
`video-to-mp3\convert_to_mp3.py` 都已收掉，功能全部併進 `server.py`。
要翻舊版可以查 git 歷史（commit `cc1bd16` 之前）。
