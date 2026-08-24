# 09｜AI 額度儀表板｜卡片改善報告

- **卡片 ID：** `ai-usage`
- **正式站：** https://my-teaching-tools-2b36c.web.app/admin/
- **本機來源：** `C:\Users\admin\Desktop\classroom\tools\admin\index.html:663`
- **動作：** `link` → `/ai-usage/`
- **問題等級：** 中
- **分類：** 內容準確性

## 1. 結論

卡片仍寫 Gemini，但目前儀表板與工作筆記已改為 Antigravity，名稱過時；也漏掉歷史趨勢能力。

## 2. 實際檢查證據

正式站 `/ai-usage/` HTTP 200；頁面有 6 小時／24 小時／7 天、短期／週窗口、已用／剩餘／區間消耗。

共通檢查：正式 `/admin/` HTML 為 HTTP 200；本機與正式站修改前卡片清單皆為 16 張且完全一致；DOM 65 個 id 無重複；全部 Modal 入口函式存在；JavaScript `node --check` exit code 0。

## 3. 卡片文案修改

| 欄位 | 修改前（正式站目前仍是此版） | 本機修改後（尚未部署） |
|---|---|---|
| 標題 | AI 額度儀表板 | AI 額度儀表板 |
| 說明 | 查看 Claude、Codex、Gemini 使用額度 | 查看 Claude、Codex、Antigravity 即時額度與歷史趨勢 |
| 連結／入口 | `/ai-usage/` | `/ai-usage/` |

## 4. 已直接修改

- 已更新本機 `DEFAULT_TOOLS` 的標題與說明。
- 相關 Modal 標題／副標題有改名者同步更新，避免卡片與內頁不一致。
- 卡片內容版本升為 `CARD_CONTENT_VERSION = 2`；未來部署後，舊 Firestore 卡片內容會一次遷移，避免舊資料把新文案蓋回去，同時保留使用者既有排序與圖示。

## 5. 建議的下一階段改善

可加最後更新時間與資料收集器離線提示。

## 6. 本輪未測／風險邊界

- 未執行 Firebase deploy，因此正式網站仍維持修改前內容。
- 不用付費 API、不下載媒體、不轉錄音檔、不搬移或重新編號使用者檔案。
- 需真實資料或桌面控制的端到端流程，應另用去識別化測試資料驗收。

## 7. 驗收方式

- 解析後卡片 ID 必須唯一且總數仍為 16。
- `node --check` 必須 exit code 0。
- 本機審閱預覽中，卡片標題與說明必須與上表「修改後」一致。
- 若屬連結卡，目標 URL／協定必須符合 PORT 台帳或正式 Hosting 路徑。
