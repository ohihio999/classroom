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

雙擊 `start.bat`（或 `python server.py`）。視窗會留著，關掉視窗＝停止服務。
啟動後會自動開瀏覽器到 <http://127.0.0.1:8767/>。

### 步驟二：從 admin 頁進入

<https://my-teaching-tools-2b36c.web.app/admin/> →「影片轉MP3 / 錄音合併」卡片。
（卡片只是連到本機服務，服務沒啟動就會連不上。）

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
├── server.py           ← 服務 + 網頁 UI 全包在這支（port 8767）
├── start.bat           ← 雙擊啟動
├── convert_to_mp3.py   ← 舊的命令列版，給 C:\OBS\影片轉mp3.bat 用；網頁版穩定後可連同 bat 一起刪
└── SOP.md
```

本機服務 port 分配：8765 Skill 管理器、8766 LINE 對話整理、**8767 影音工具**。

---

## 舊做法（已被網頁版取代）

`C:\OBS\影片轉mp3.bat`、`C:\OBS\錄音黨合併.bat`、`C:\OBS\merge.ps1`
還留在原地當備援，網頁版穩定後可以刪。
