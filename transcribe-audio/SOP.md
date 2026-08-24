# SOP：音檔轉逐字稿與標準短字幕（Groq Whisper / AssemblyAI）

本工具提供高精度語音轉繁體中文逐字稿、字級時間戳與自動短字幕切分，支援動態小抄與分類詞庫。

---

## ⚡ 核心能力（v2.2.0）

1. **自動短字幕切分（v2.2.0 新增）**：轉錄時自動調用 `word-level` 字級時間戳，將 Whisper 粗糙長句切成 **7~10 字 / 1.5 秒** 的標準短字幕（`.srt`），完全解決長句退化問題。
2. **分類詞庫與小抄（v2.1.0+）**：自動從同目錄 `.info.json` 抽取專有名詞，並套用 `lexicon/lexicon.json` 的專門術語與錯字替換（`--categories AI`）。
3. **幻覺與雜訊過濾**：自動清除 Whisper 水印句（「請不吝點贊…」）與相鄰重複迴圈。
4. **自動音訊切段**：超過 25MB 音檔自動無損切分，不需手動跑 ffmpeg。

---

## 🚀 使用流程

### 步驟一：準備音檔
支援格式：`mp3`、`mp4`、`wav`、`m4a`、`ogg`、`webm`。

### 步驟二：執行轉錄
開啟終端機執行：
```bash
cd C:\Users\admin\Desktop\classroom\transcribe-audio
python transcribe.py "你的音檔路徑.mp3"
```
*如需指定特定詞庫分類（如 AI）：*
```bash
python transcribe.py "你的音檔路徑.mp3" --categories AI
```

### 步驟三：產出成品
轉錄完成後，同目錄下將自動產出：
1. `<檔名>_逐字稿.md`：繁體中文完整逐字稿。
2. `<檔名>_逐字稿.srt`（或 `<檔名>.srt`）：**已自動切好 7~10 字/條的標準短字幕**（可直接外掛播放或匯入剪映）。
3. `<檔名>.words.json`：單詞級毫秒時間戳資料庫。

---

## 📂 專案檔案結構

```text
transcribe-audio/
├── transcribe.py          ← 主轉錄程式 (v2.2.0)
├── refine_srt.py          ← 字幕精修與長句聚合模組
├── lexicon/               ← 分類詞庫系統
│   ├── lexicon.py         ← 詞庫載入與 Prompt 生成器
│   └── lexicon.json       ← 詞庫本體（AI、通用、字幕工具）
├── CHANGELOG.md           ← 詳細演進與版本歷程記錄
└── SOP.md                 ← 本操作手冊
```
