# SOP：手動音檔轉逐字稿（Groq Whisper API）

## 前置作業（一次性設定）

### 1. 取得 Groq API Key
前往 https://console.groq.com/keys → 建立新 API Key

### 2. 設定環境變數
以系統管理員開啟命令提示字元，執行：
```
setx GROQ_API_KEY "你的API金鑰"
```
設定後重新開啟終端機才生效。確認方式：
```
echo %GROQ_API_KEY%
```

### 3. 安裝 Python 套件
```
pip install groq
```

---

## 每次使用流程

### 步驟一：準備音檔

支援格式：mp3、mp4、wav、m4a、ogg、webm
**單檔上限 25MB**（超過見下方「大檔處理」）

把音檔放到：
```
C:\Users\admin\Desktop\classroom\transcribe-audio\inbox\
```

### 步驟二：執行轉錄

開啟終端機，執行：
```
cd C:\Users\admin\Desktop\classroom\transcribe-audio
python transcribe.py inbox\你的音檔.mp3
```

等待完成，1 小時音檔約 **1–2 分鐘**。

### 步驟三：確認輸出

輸出位置：
```
done\你的音檔_逐字稿.md
```

### 步驟四：後處理

把逐字稿路徑告訴 Claude：
```
用 transcript-training-pack 處理 C:\Users\admin\Desktop\classroom\transcribe-audio\done\你的音檔_逐字稿.md
```

Claude 會自動產出：培訓報告、摘要、心智圖、心智圖網頁版（HTML）。

---

## 大檔處理（超過 25MB）

程式會自動偵測並給出壓縮指令。如需手動壓縮：

```
ffmpeg -i 原始音檔.mp3 -ac 1 -ar 16000 -b:a 32k 壓縮後.mp3
```

- `-ac 1`：單聲道（語音足夠）
- `-ar 16000`：16kHz 取樣率（Whisper 標準）
- `-b:a 32k`：32kbps 位元率，1 小時約 14MB

---

## 常見問題

**Q：轉出來是簡體字怎麼辦？**
Whisper 輸出語言跟音檔語言有關，目前設定 `language="zh"` 固定中文。
若仍出現簡體，可在 transcript-training-pack 步驟要求轉換。

**Q：免費額度多少？**
Groq 免費方案：每天約 28,800 秒（8 小時）音訊，4–10 場/月完全足夠。

**Q：多人對話能區分說話者嗎？**
目前版本不支援說話者分離（diarization），v1 先不做。

---

## 資料夾結構

```
transcribe-audio\
├── transcribe.py      ← 主程式
├── SOP.md             ← 本文件
├── inbox\             ← 音檔丟這裡
└── done\              ← 逐字稿輸出到這裡
```
