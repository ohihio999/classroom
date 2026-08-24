# 01｜AI 工作台（本機）｜卡片改善報告

- **卡片 ID：** `ai-workbench`
- **正式站：** https://my-teaching-tools-2b36c.web.app/admin/
- **本機來源：** `C:\Users\admin\Desktop\classroom\tools\admin\index.html:653`
- **動作：** `link` → `aiwork://start`
- **問題等級：** 低
- **分類：** UX／內容

## 1. 結論

原標題未標示只限本機；模型名稱大小寫不一致，且用途只寫分頁數，辨識成本偏高。

## 2. 實際檢查證據

`aiwork://` 已註冊，目標 `ai-workbench\start.bat` 存在；BAT 註解確認會開 3 AGY／2 Claude／2 Codex。為避免測試時一次開 7 個終端分頁，本輪未實際觸發。

共通檢查：正式 `/admin/` HTML 為 HTTP 200；本機與正式站修改前卡片清單皆為 16 張且完全一致；DOM 65 個 id 無重複；全部 Modal 入口函式存在；JavaScript `node --check` exit code 0。

## 3. 卡片文案修改

| 欄位 | 修改前（正式站目前仍是此版） | 本機修改後（尚未部署） |
|---|---|---|
| 標題 | AI 工作台 | AI 工作台（本機） |
| 說明 | 一次開好 7 個分頁：agy ×3、claude ×2、codex ×2 | 啟動 7 個 Hermes 分頁：3 AGY／2 Claude／2 Codex |
| 連結／入口 | `aiwork://start` | `aiwork://start` |

## 4. 已直接修改

- 已更新本機 `DEFAULT_TOOLS` 的標題與說明。
- 相關 Modal 標題／副標題有改名者同步更新，避免卡片與內頁不一致。
- 卡片內容版本升為 `CARD_CONTENT_VERSION = 2`；未來部署後，舊 Firestore 卡片內容會一次遷移，避免舊資料把新文案蓋回去，同時保留使用者既有排序與圖示。

## 5. 建議的下一階段改善

未來可在卡片旁顯示「本機限定」徽章，並在捷徑不存在時顯示修復入口。

## 6. 本輪未測／風險邊界

- 未執行 Firebase deploy，因此正式網站仍維持修改前內容。
- 不用付費 API、不下載媒體、不轉錄音檔、不搬移或重新編號使用者檔案。
- 需真實資料或桌面控制的端到端流程，應另用去識別化測試資料驗收。

## 7. 驗收方式

- 解析後卡片 ID 必須唯一且總數仍為 16。
- `node --check` 必須 exit code 0。
- 本機審閱預覽中，卡片標題與說明必須與上表「修改後」一致。
- 若屬連結卡，目標 URL／協定必須符合 PORT 台帳或正式 Hosting 路徑。
