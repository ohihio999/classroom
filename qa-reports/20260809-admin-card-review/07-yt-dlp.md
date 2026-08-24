# 07｜網址下載批次檔｜卡片改善報告

- **卡片 ID：** `yt-dlp`
- **正式站：** https://my-teaching-tools-2b36c.web.app/admin/
- **本機來源：** `C:\Users\admin\Desktop\classroom\tools\admin\index.html:661`
- **動作：** `modal` → `openYtDlp`
- **問題等級：** 中
- **分類：** 功能／UX

## 1. 結論

卡片稱「影片下載」，實際包含 FB 照片；且點開預設跳照片模式，與標題和第一個按鈕不一致。

## 2. 實際檢查證據

Modal 有影片、音訊、照片三模式與 BAT 下載；原程式 `openYtDlp()` 卻強制 `setYtMode('photo')`，已改為預設影片模式。

共通檢查：正式 `/admin/` HTML 為 HTTP 200；本機與正式站修改前卡片清單皆為 16 張且完全一致；DOM 65 個 id 無重複；全部 Modal 入口函式存在；JavaScript `node --check` exit code 0。

## 3. 卡片文案修改

| 欄位 | 修改前（正式站目前仍是此版） | 本機修改後（尚未部署） |
|---|---|---|
| 標題 | yt-dlp 影片下載 | 網址下載批次檔 |
| 說明 | 貼網址下載 YouTube、FB 等影片或音訊 | 產生 YouTube 影片／MP3 或 FB 照片下載 BAT |
| 連結／入口 | `openYtDlp` | `openYtDlp` |

## 4. 已直接修改

- 已更新本機 `DEFAULT_TOOLS` 的標題與說明。
- 相關 Modal 標題／副標題有改名者同步更新，避免卡片與內頁不一致。
- 卡片內容版本升為 `CARD_CONTENT_VERSION = 2`；未來部署後，舊 Firestore 卡片內容會一次遷移，避免舊資料把新文案蓋回去，同時保留使用者既有排序與圖示。
- 已把開啟 Modal 的預設模式從 `photo` 修正為 `video`。

## 5. 建議的下一階段改善

可記住上次使用模式；目前先採最符合標題與按鈕排序的「影片」預設。

## 6. 本輪未測／風險邊界

- 未執行 Firebase deploy，因此正式網站仍維持修改前內容。
- 不用付費 API、不下載媒體、不轉錄音檔、不搬移或重新編號使用者檔案。
- 需真實資料或桌面控制的端到端流程，應另用去識別化測試資料驗收。

## 7. 驗收方式

- 解析後卡片 ID 必須唯一且總數仍為 16。
- `node --check` 必須 exit code 0。
- 本機審閱預覽中，卡片標題與說明必須與上表「修改後」一致。
- 若屬連結卡，目標 URL／協定必須符合 PORT 台帳或正式 Hosting 路徑。
