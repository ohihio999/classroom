# transcribe-audio 工具演進與版本變更記錄（CHANGELOG）

本文件完整記錄 `transcribe.py`、`refine_srt.py` 與 `lexicon` 詞庫系統的架構演進、修復緣由與版本發布歷程，供後續查閱、審計與維護。

---

## 📌 版本總覽

| 版本 | 日期 | 主要維護者 | 核心變更與里程碑 |
| :--- | :--- | :--- | :--- |
| **v2.4.0 / refine v1.3.0** | 2026-08-23 | Antigravity (Gemini 3.7 Flash) | • **手動標記智慧流動合併（User Marker Flow）**：支援在 SRT 內部直接以「換行鍵（Enter）標記下一段」與「雙空格標記錯字」，系統自動流動跨 cue 合併、修復錯字並自動對齊 `words.json` 重新精準計算毫秒級時間軸。<br>• **詞庫新詞學習**：學習「多億」$\rightarrow$「多益」（TOEIC）等情境錯字並自動固化至 `lexicon.json`。<br>• **四大語義斷句黃金原則**：固化「獨立時間狀語」、「獨立人名介紹」、「介詞短語完整性」與「數量詞+名詞黏著」語法規則。 |
| **v2.3.1 / refine v1.2.0** | 2026-08-23 | Antigravity (Gemini 3.7 Flash) | • **引導詞/副詞精準吸附（Clause Starter Snap）**：遇到「到底、為什麼、是不是、因為」等引導詞時，切點精準吸附至引導詞正前方，完美分離主語與謂語（如 055「而是這1600萬」與 056「到底是怎麼花的」）。<br>• **複合名詞保護（Compound Suffixes）**：防止「創辦人」被撕裂成「創辦」+「人」（057「一個不寫程式的創辦人」+ 058「到底要怎麼管一個技術團隊呢」）。<br>• **零遺漏連續切分（Zero-drop Split）**：修復切點微調時舊區間遺漏字元之潛在缺陷。 |
| **v2.3.0 / refine v1.1.0** | 2026-08-23 | Antigravity (Gemini 3.7 Flash) | • **數字與量詞黏著保護（Sticky Binding）**：徹底修復「1600」與「萬」分家、數字與單位被硬切的 Bug。<br>• **行尾懸空字智慧避讓（Smart Nudge）**：自動將「從、在、和、的」等懸空助詞推至下一行開頭。<br>• **字數上限調至影視標準**：`SUB_MAX_CHARS` 從 12 字放寬至 16 字（時長 1.8~3.5 秒），平均 9.7~10.0 字/條。 |
| **v2.2.0** | 2026-08-23 | Antigravity (Gemini 3.7 Flash) | • **自動短字幕切條整合**：`transcribe.py` 轉錄時直接調用 `refine_srt.stage2_subtitle`，預設產出標準短字幕。<br>• **Groq API 896 長度防護**：修正中文多位元組導致 Prompt 超長 400 錯誤，限制 Prompt 為 240 字元（$\le 800$ bytes）。<br>• **AI 詞庫全面擴充**：擴增至 159 個專業術語（terms）與 54 組高精度錯字修復（fixes）。 |
| **v2.1.0** | 2026-08-22 | Claude (Opus 5) | • 改向 Whisper API 索取 **Word-level 時間戳**，產出 `.words.json`。<br>• 小抄改讀分類詞庫 `lexicon/lexicon.json`，支援 `--categories`。<br>• 自動從同目錄 `.info.json` 抽 YouTube 標題/簡介/標籤為臨時小抄。<br>• 轉錄後自動套用詞庫 `fixes` 進行同音錯字替換。 |
| **v2.0.0** | 2026-08-22 | Claude (Opus 5) | • 新增 Whisper Initial Prompt 詞彙導引 + 前段上下文接續（`temperature=0`）。<br>• 幻覺過濾：水印句黑名單、`no_speech_prob` / `avg_logprob` 品質過濾、相鄰重複迴圈去重。<br>• OpenCC 模式調整：`s2twp` $\rightarrow$ `s2tw`（停止將「函數」竄改為「函式」）。<br>• 壓縮位元率調整：32k $\rightarrow$ 64k。 |
| **v1.0.0** | 2026-07-31 | 使用者自撰 | 初版批次音訊轉錄工具（備份於 `transcribe.py.bak-20260822`）。 |

---

## 🛠️ v2.4.0 / refine v1.3.0 使用者修改習慣學習與智慧流動合併（2026-08-23）

### 1. 使用者修改標記與習慣歸納
- **換行鍵流動語義（Enter Flow Merge）**：當使用者在某條字幕按換行時，代表該行末尾詞（如「有」、「我」、「從臺大」）語義屬於**下一條字幕開頭**。系統實作了跨 cue 溢出流動合併，並自動透過 `words.json` 重新精準定位時間。
- **雙空格錯字標記（Double-space Fixes）**：雙空格包裹的字詞代表需修復錯字（如 `  多億  ` $\rightarrow$ `多益`），自動吸納並擴充至全域詞庫。
- **四大語意斷句法則**：
  1. **獨立時間狀語**（如 `2020年`、`然後在2022年`）：獨立成條，不與後方長句擠壓。
  2. **獨立人名介紹**（如 `他是Lily`）：獨立成句，介詞短語（`從臺大中文系畢業...`）切入下一條。
  3. **動詞/代詞前綴**（如 `有把...`、`我已經...`）：動詞與人稱主語緊隨所修飾的動詞短語。
  4. **數量詞+量詞+名詞黏著**（如 `另一位英文YouTuber`、`這個數字的時候`）：不可撕裂。

---

## 📂 相關原始碼與檔案路徑

- **主轉錄程式**：[transcribe.py](file:///C:/Users/admin/Desktop/classroom/transcribe-audio/transcribe.py)
- **字幕精修器**：[refine_srt.py](file:///C:/Users/admin/Desktop/classroom/transcribe-audio/refine_srt.py)
- **詞庫載入模組**：[lexicon.py](file:///C:/Users/admin/Desktop/classroom/transcribe-audio/lexicon/lexicon.py)
- **詞庫資料庫**：[lexicon.json](file:///C:/Users/admin/Desktop/classroom/transcribe-audio/lexicon/lexicon.json)
- **標準作業程序**：[SOP.md](file:///C:/Users/admin/Desktop/classroom/transcribe-audio/SOP.md)
