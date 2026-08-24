# 02｜AI 工具分類管理｜卡片改善報告

- **卡片 ID：** `tool-manager`
- **正式站：** https://my-teaching-tools-2b36c.web.app/admin/
- **本機來源：** `C:\Users\admin\Desktop\classroom\tools\admin\index.html:654`
- **動作：** `link` → `/tool-manager/`
- **問題等級：** 低
- **分類：** 內容／UX

## 1. 結論

「小程式分類管理」沒有反映 AI 搜尋、批次分析等主要能力。

## 2. 實際檢查證據

正式站 `/tool-manager/` HTTP 200；原始頁面確認具備新增工具、AI 批量解析、AI 搜尋、HTML／MD／JSON 備份與歷史日誌。

共通檢查：正式 `/admin/` HTML 為 HTTP 200；本機與正式站修改前卡片清單皆為 16 張且完全一致；DOM 65 個 id 無重複；全部 Modal 入口函式存在；JavaScript `node --check` exit code 0。

## 3. 卡片文案修改

| 欄位 | 修改前（正式站目前仍是此版） | 本機修改後（尚未部署） |
|---|---|---|
| 標題 | 小程式分類管理 | AI 工具分類管理 |
| 說明 | 收藏、分類、隨手可用的 AI 工具庫 | 收藏、分類、搜尋與批次分析常用 AI 工具 |
| 連結／入口 | `/tool-manager/` | `/tool-manager/` |

## 4. 已直接修改

- 已更新本機 `DEFAULT_TOOLS` 的標題與說明。
- 相關 Modal 標題／副標題有改名者同步更新，避免卡片與內頁不一致。
- 卡片內容版本升為 `CARD_CONTENT_VERSION = 2`；未來部署後，舊 Firestore 卡片內容會一次遷移，避免舊資料把新文案蓋回去，同時保留使用者既有排序與圖示。

## 5. 建議的下一階段改善

可再加卡片內最近新增數與未分類數，但需額外讀 Firestore，不納入本次文案修正。

## 6. 本輪未測／風險邊界

- 未執行 Firebase deploy，因此正式網站仍維持修改前內容。
- 不用付費 API、不下載媒體、不轉錄音檔、不搬移或重新編號使用者檔案。
- 需真實資料或桌面控制的端到端流程，應另用去識別化測試資料驗收。

## 7. 驗收方式

- 解析後卡片 ID 必須唯一且總數仍為 16。
- `node --check` 必須 exit code 0。
- 本機審閱預覽中，卡片標題與說明必須與上表「修改後」一致。
- 若屬連結卡，目標 URL／協定必須符合 PORT 台帳或正式 Hosting 路徑。
