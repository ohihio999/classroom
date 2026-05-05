# SOP：批次把影片檔轉成 MP3

## 目的

把同一個資料夾裡的所有影片檔一次轉成 MP3，輸出檔直接放在原本影片的同一個資料夾。

---

## 一次性安裝

### 1. 安裝 ffmpeg

用 Windows `winget`：

```powershell
winget install Gyan.FFmpeg
```

安裝完後重新開一個終端機，確認：

```powershell
ffmpeg -version
```

### 2. 確認 Python

```powershell
python --version
```

---

## 每次使用流程

### 步驟一：準備影片資料夾

把要轉的影片放在同一個資料夾，例如：

```text
C:\OBS
```

支援格式：

- `mp4`
- `mov`
- `mkv`
- `avi`
- `webm`
- `m4v`
- `flv`
- `wmv`

### 步驟二：執行批次轉檔

```powershell
cd C:\Users\admin\Desktop\classroom\video-to-mp3
python convert_to_mp3.py "C:\OBS"
```

### 步驟三：確認輸出

每支影片轉完後，會在原本同一個資料夾看到對應的 `.mp3`：

```text
C:\OBS\影片A.mp4
C:\OBS\影片A.mp3
```

---

## 工具行為

- 工具會批次處理資料夾內所有影片檔
- 轉檔後的 MP3 會放在同一個資料夾
- 檔名沿用原影片檔名，只把副檔名改成 `.mp3`

---

## 專案結構

```text
video-to-mp3\
├── convert_to_mp3.py
└── SOP.md
```
