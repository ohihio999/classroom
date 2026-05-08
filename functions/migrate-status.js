/**
 * 一次性資料遷移：將 work_progress 集合的舊狀態轉換
 *   pending  → in_progress
 *   review   → done
 *
 * 執行方式（在 functions/ 目錄下）：
 *
 *   方法 A：使用 service account key（推薦）
 *     1. Firebase Console > 專案設定 > 服務帳號 > 產生新的私密金鑰 → 下載 JSON
 *     2. 將 JSON 放到 classroom/ 根目錄，命名為 service-account.json
 *     3. node migrate-status.js
 *
 *   方法 B：使用 Application Default Credentials
 *     1. 安裝 gcloud CLI 並執行：gcloud auth application-default login
 *     2. node migrate-status.js
 */

const admin = require('firebase-admin');
const path = require('path');
const fs = require('fs');

const SERVICE_ACCOUNT_PATH = path.join(__dirname, '..', 'service-account.json');

if (fs.existsSync(SERVICE_ACCOUNT_PATH)) {
  admin.initializeApp({
    credential: admin.credential.cert(require(SERVICE_ACCOUNT_PATH))
  });
} else {
  admin.initializeApp({
    credential: admin.credential.applicationDefault(),
    projectId: 'my-teaching-tools-2b36c'
  });
}

const db = admin.firestore();

async function migrate() {
  console.log('查詢 work_progress 集合…');
  const snap = await db.collection('work_progress').get();
  console.log(`共 ${snap.size} 筆文件`);

  const toMigrate = snap.docs.filter(d => {
    const s = d.data().status;
    return s === 'pending' || s === 'review';
  });

  if (toMigrate.length === 0) {
    console.log('✅ 沒有需要遷移的資料');
    return;
  }

  console.log(`需遷移：${toMigrate.length} 筆`);

  // Firestore 批次最多 500 筆，分批處理
  const BATCH_SIZE = 400;
  let migrated = 0;

  for (let i = 0; i < toMigrate.length; i += BATCH_SIZE) {
    const batch = db.batch();
    const chunk = toMigrate.slice(i, i + BATCH_SIZE);

    chunk.forEach(doc => {
      const oldStatus = doc.data().status;
      const newStatus = oldStatus === 'pending' ? 'in_progress' : 'done';
      console.log(`  ${doc.id}: ${oldStatus} → ${newStatus}`);
      batch.update(doc.ref, { status: newStatus });
    });

    await batch.commit();
    migrated += chunk.length;
    console.log(`已提交第 ${Math.ceil((i + 1) / BATCH_SIZE)} 批`);
  }

  console.log(`\n✅ 成功遷移 ${migrated} 筆資料`);
}

migrate()
  .catch(err => { console.error('❌ 遷移失敗：', err.message); process.exit(1); })
  .finally(() => process.exit(0));
