/*
  AI 額度儀表板 — Service Worker（讓頁面可「加到主畫面」常駐、短暫斷線也打得開）
  版號：v1.3.2
  版更記錄：
  - v1.3.2 (2026-07-29) 更新外殼快取，額度條改用網站原始固定綠藍紅三色與 50／25 門檻。
  - v1.3.1 (2026-07-29) 更新外殼快取，載入高飽和綠藍紅額度色階。
  - v1.3.0 (2026-07-29) 更新外殼快取，所有額度條與預設趨勢改為剩餘量及綠藍紅連續色階。
  - v1.2.3 (2026-07-29) 更新外殼快取，Codex 短期無資料時改顯示週額度並標記。
  - v1.2.2 (2026-07-29) 更新外殼快取，統一 Antigravity 名稱、移除 Gemini CLI 趨勢線並保留 Codex 無資料提示。
  - v1.2.1 (2026-07-29) 更新外殼快取，載入移除 Gemini CLI 卡片與簡化 Antigravity 標題的頁面。
  - v1.2.0 (2026-07-29) 更新快取版本，載入 Antigravity 四額度池與雙趨勢線新版頁面。
  - v1.1.0 (2026-07-29) 更新外殼快取版本，確保趨勢圖與 agy 狀態新版 HTML 不被舊快取蓋住。
  - v1.0.0 (2026-07-28) 初版：外殼快取 + 導覽請求 network-first（避免部署後吃到舊 HTML）

  注意：額度數字走 Firestore 即時連線，一律不快取；這裡只快取靜態外殼。
  改動內容時要把 CACHE 版號往上加，舊快取才會被清掉。
*/
const CACHE = "ai-usage-v9";
const SHELL = ["./", "./index.html", "./manifest.json", "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // 只管自己站上的靜態檔；Firestore／gstatic 等外部請求交給瀏覽器自己處理
  if (url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith("/ai-usage/")) return;

  // HTML：network-first，離線才回快取（部署後才不會一直吃到舊版）
  if (req.mode === "navigate" || url.pathname.endsWith(".html") || url.pathname.endsWith("/ai-usage/")) {
    e.respondWith(
      fetch(req)
        .then(res => {
          caches.open(CACHE).then(c => c.put(req, res.clone()));
          return res;
        })
        .catch(() => caches.match(req).then(r => r || caches.match("./index.html")))
    );
    return;
  }

  // 圖示等靜態資源：cache-first
  e.respondWith(
    caches.match(req).then(r => r || fetch(req).then(res => {
      caches.open(CACHE).then(c => c.put(req, res.clone()));
      return res;
    }))
  );
});
