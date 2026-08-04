const { onCall, HttpsError } = require('firebase-functions/v2/https');
const { onSchedule } = require('firebase-functions/v2/scheduler');
const { defineSecret } = require('firebase-functions/params');
const logger = require('firebase-functions/logger');
const admin = require('firebase-admin');
const nodemailer = require('nodemailer');

if (!admin.apps.length) admin.initializeApp();

const gptApiKey       = defineSecret('GPT_API_KEY');
const groqApiKey      = defineSecret('GROQ_API_KEY');
const assemblyAIKey   = defineSecret('ASSEMBLYAI_KEY');
const gmailAppPassword = defineSecret('GMAIL_APP_PASSWORD'); // 部署前需先執行: firebase secrets:set GMAIL_APP_PASSWORD
const ALLOWED_EMAIL   = 'ohihio@gmail.com';

exports.getGroqKey = onCall({ secrets: [groqApiKey], region: 'asia-east1' }, async (request) => {
  if (!request.auth) throw new HttpsError('unauthenticated', '請先登入');
  if (request.auth.token.email !== ALLOWED_EMAIL) throw new HttpsError('permission-denied', '無權限');
  return { key: groqApiKey.value() };
});

exports.getAssemblyAIKey = onCall({ secrets: [assemblyAIKey], region: 'asia-east1' }, async (request) => {
  if (!request.auth) throw new HttpsError('unauthenticated', '請先登入');
  if (request.auth.token.email !== ALLOWED_EMAIL) throw new HttpsError('permission-denied', '無權限');
  return { key: assemblyAIKey.value() };
});

exports.ocrEnvelope = onCall({ secrets: [gptApiKey], region: 'asia-east1' }, async (request) => {
  const { imageBase64, mimeType, prompt } = request.data;

  if (!imageBase64 || !mimeType) {
    throw new HttpsError('invalid-argument', '缺少圖片資料');
  }

  const ocrPrompt = prompt || '請從圖片中辨識文字，以純 JSON 格式回傳。找不到的欄位填空字串。';

  const response = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${gptApiKey.value()}`
    },
    body: JSON.stringify({
      model: 'gpt-4o',
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: ocrPrompt },
          { type: 'image_url', image_url: { url: `data:${mimeType};base64,${imageBase64}`, detail: 'high' } }
        ]
      }],
      max_tokens: 800,
      response_format: { type: 'json_object' }
    })
  });

  const data = await response.json();
  if (!response.ok) throw new HttpsError('internal', data.error?.message || 'GPT 辨識失敗');

  const content = data.choices[0].message.content.trim();
  const match = content.match(/\{[\s\S]*\}/);
  if (!match) throw new HttpsError('internal', '無法解析辨識結果');

  return JSON.parse(match[0]);
});

exports.toolManagerAI = onCall({ secrets: [gptApiKey], region: 'asia-east1' }, async (request) => {
  if (!request.auth) {
    throw new HttpsError('unauthenticated', '請先登入');
  }

  if (request.auth.token.email !== ALLOWED_EMAIL) {
    throw new HttpsError('permission-denied', '你沒有使用此功能的權限');
  }

  const { prompt, model } = request.data || {};

  if (!prompt || typeof prompt !== 'string') {
    throw new HttpsError('invalid-argument', '缺少 prompt');
  }

  const selectedModel = (typeof model === 'string' && model.trim()) || 'gpt-4o-mini';
  const isReasoningModel = /^(gpt-5|o1|o3|o4)/.test(selectedModel);

  const payload = {
    model: selectedModel,
    input: prompt,
    text: {
      format: { type: 'json_object' }
    }
  };

  if (isReasoningModel) {
    payload.reasoning = { effort: 'low' };
  }

  const response = await fetch('https://api.openai.com/v1/responses', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${gptApiKey.value()}`
    },
    body: JSON.stringify(payload)
  });

  const data = await response.json();
  if (!response.ok) {
    throw new HttpsError('internal', data.error?.message || 'AI 呼叫失敗');
  }

  const text = typeof data.output_text === 'string' && data.output_text.trim()
    ? data.output_text.trim()
    : (Array.isArray(data.output) ? data.output.flatMap(item => item.content || []).find(part => part?.type === 'output_text')?.text || '' : '');

  if (!text) {
    throw new HttpsError('internal', 'AI 沒有回傳可解析內容');
  }

  try {
    return JSON.parse(text);
  } catch (err) {
    throw new HttpsError('internal', 'AI 回傳格式不是合法 JSON');
  }
});

// 工作進度表：GPT 自動分類
exports.classifyTask = onCall({ secrets: [gptApiKey], region: 'asia-east1' }, async (request) => {
  if (!request.auth) throw new HttpsError('unauthenticated', '請先登入');
  const { title, description } = request.data || {};
  if (!title || typeof title !== 'string') throw new HttpsError('invalid-argument', '缺少標題');

  const prompt = `你是任務分類助理。請根據任務標題和說明，從以下四個類別中選一個，只回傳類別名稱，不要有其他文字。
類別：技術/資訊、行政/庶務、業務/對外、其他
任務標題：${title}
任務說明：${description || '無'}`;

  const resp = await fetch('https://api.openai.com/v1/chat/completions', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${gptApiKey.value()}`
    },
    body: JSON.stringify({
      model: 'gpt-4o-mini',
      messages: [{ role: 'user', content: prompt }],
      max_tokens: 20
    })
  });

  const data = await resp.json();
  if (!resp.ok) throw new HttpsError('internal', data.error?.message || 'GPT 呼叫失敗');

  const raw = data.choices[0].message.content.trim();
  const VALID = ['技術/資訊', '行政/庶務', '業務/對外', '其他'];
  return { category: VALID.find(c => raw.includes(c)) || '其他' };
});

// 工作進度表：截止前 3 天 Email 提醒（每天 Taipei 09:00 執行）
// 注意：部署前須先執行 firebase secrets:set GMAIL_APP_PASSWORD
exports.sendWorkProgressReminders = onSchedule(
  { schedule: '0 9 * * *', timeZone: 'Asia/Taipei', secrets: [gmailAppPassword], region: 'asia-east1' },
  async () => {
    const { getFirestore } = require('firebase-admin/firestore');
    const db = getFirestore();

    const today = new Date(); today.setHours(0, 0, 0, 0);
    const threeDaysLater = new Date(today);
    threeDaysLater.setDate(today.getDate() + 3);
    const todayStr      = today.toISOString().split('T')[0];
    const threeDaysStr  = threeDaysLater.toISOString().split('T')[0];

    const ACTIVE_STATUSES = ['pending', 'in_progress', 'review'];

    logger.info('work-progress reminder started', { todayStr, threeDaysStr });

    const [taskSnap, memberSnap] = await Promise.all([
      db.collection('work_progress')
        .where('dueDate', '>=', todayStr)
        .where('dueDate', '<=', threeDaysStr)
        .get(),
      db.collection('work_progress_members').get()
    ]);

    logger.info('work-progress reminder query result', {
      rangeTaskCount: taskSnap.size,
      memberCount: memberSnap.size
    });

    if (taskSnap.empty) {
      logger.info('work-progress reminder skipped: no tasks in due date range');
      return;
    }

    const emailMap = {};
    memberSnap.forEach(d => { emailMap[d.id] = d.data().email || ''; });

    const grouped = {};
    let skippedInactive = 0;
    let skippedMissingAssignee = 0;
    taskSnap.forEach(d => {
      const task = d.data();
      if (!ACTIVE_STATUSES.includes(task.status)) {
        skippedInactive += 1;
        return;
      }
      const key = task.assigneeKey;
      if (!key) {
        skippedMissingAssignee += 1;
        return;
      }
      if (!grouped[key]) grouped[key] = { name: task.assignee || key, email: emailMap[key], tasks: [] };
      grouped[key].tasks.push({ id: d.id, ...task });
    });

    const recipients = Object.entries(grouped).map(([key, group]) => ({
      key,
      name: group.name,
      hasEmail: Boolean(group.email),
      taskCount: group.tasks.length
    }));

    logger.info('work-progress reminder grouping result', {
      activeTaskCount: Object.values(grouped).reduce((sum, group) => sum + group.tasks.length, 0),
      recipientCount: recipients.length,
      recipients,
      skippedInactive,
      skippedMissingAssignee
    });

    if (!recipients.length) {
      logger.info('work-progress reminder skipped: no active tasks after filtering');
      return;
    }

    const transporter = nodemailer.createTransport({
      service: 'gmail',
      auth: { user: ALLOWED_EMAIL, pass: gmailAppPassword.value() }
    });

    const STATUS_LABEL = { pending:'待處理', in_progress:'進行中', review:'待確認' };

    const results = await Promise.allSettled(
      Object.entries(grouped).map(async ([key, { name, email, tasks }]) => {
        if (!email) {
          logger.warn('work-progress reminder skipped recipient: missing email', { key, name, taskCount: tasks.length });
          return { key, skipped: true, reason: 'missing-email' };
        }
        const taskList = tasks.map(t => {
          const diff = Math.ceil((new Date(t.dueDate + 'T00:00:00') - today) / 864e5);
          const urgency = diff === 0 ? '【今天截止】' : `（${diff} 天後到期）`;
          return `• ${t.title} ${urgency}\n  類別：${t.category}  狀態：${STATUS_LABEL[t.status] || t.status}${t.description ? '\n  說明：' + t.description : ''}`;
        }).join('\n\n');

        const info = await transporter.sendMail({
          from: `"工作進度表" <${ALLOWED_EMAIL}>`,
          to: email,
          subject: `[工作進度表] ${name} 有 ${tasks.length} 項任務即將到期`,
          text: `${name} 您好，\n\n以下任務將在 3 天內截止，請留意處理進度：\n\n${taskList}\n\n— 工作進度表系統自動通知`
        });
        logger.info('work-progress reminder email sent', {
          key,
          name,
          taskCount: tasks.length,
          messageId: info.messageId || null,
          acceptedCount: Array.isArray(info.accepted) ? info.accepted.length : null,
          rejectedCount: Array.isArray(info.rejected) ? info.rejected.length : null
        });
        return { key, sent: true, messageId: info.messageId || null };
      })
    );

    const failed = results.filter(r => r.status === 'rejected');
    if (failed.length) {
      failed.forEach((r, idx) => logger.error('work-progress reminder email failed', {
        index: idx,
        errorMessage: r.reason?.message || String(r.reason),
        errorCode: r.reason?.code || null,
        errorResponse: r.reason?.response || null
      }));
      throw new Error(`work-progress reminder failed for ${failed.length} recipient(s)`);
    }

    logger.info('work-progress reminder finished', {
      sentCount: results.filter(r => r.status === 'fulfilled' && r.value?.sent).length,
      skippedCount: results.filter(r => r.status === 'fulfilled' && r.value?.skipped).length
    });
  }
);

/* ============================================================
   每月檢查（monthly-check）：公務車 + 銀行保險箱
   資料：Firestore monthly_check/{YYYY-MM}
   信件：1 號（上月結案＋上半月洗車提醒）／16 號（下半月洗車提醒）／25 號（未完成才催）
   收件人：ALLOWED_EMAIL
   ※ 車號、檢查項目與前端 tools/monthly-check/index.html 寫死的常數必須一致
   ============================================================ */

const MC_COLLECTION = 'monthly_check';
const MC_VEHICLES = [
  { id: 'RFD-7269', label: 'RFD-7269黑' },
  { id: 'RFJ-9383', label: 'RFJ-9383白' }
];
const MC_ITEMS = [
  { key: 'fuel',         label: '油量是否正常' },
  { key: 'dashcam',      label: '行車紀錄器接頭是否正常' },
  { key: 'coolant',      label: '水箱水位是否正常' },
  { key: 'wiper',        label: '雨刷功能是否正常' },
  { key: 'door',         label: '車門關閉情形是否正常' },
  { key: 'tire',         label: '車胎胎壓、胎紋是否正常（胎壓燈號）' },
  { key: 'exterior',     label: '車輛外表是否正常（無新刮痕、撞痕）' },
  { key: 'mileageSheet', label: '里程管理表確認' }
];
const MC_WASH = [
  { key: 'first',  label: '上半月' },
  { key: 'second', label: '下半月' }
];
const MC_URL = 'https://my-teaching-tools-2b36c.web.app/monthly-check/';

// 以台北時間算月份字串；offset 為月份位移（-1 = 上個月）
function mcMonthStr(offset = 0) {
  const taipei = new Date(Date.now() + 8 * 3600 * 1000);
  const d = new Date(Date.UTC(taipei.getUTCFullYear(), taipei.getUTCMonth() + offset, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

function mcMonthLabel(month) {
  const [y, m] = month.split('-');
  return `${y} 年 ${Number(m)} 月`;
}

// 與前端 computeStatus 同一套規則：自由文字欄不列入完成度
function mcStatus(data) {
  const missing = [];
  const abnormal = [];
  let done = 0, total = 0;

  MC_VEHICLES.forEach(v => {
    const vd = (data.vehicles || {})[v.id] || {};
    total += 2 + MC_ITEMS.length;

    if (vd.checkDate) done++; else missing.push(`${v.label}：未填檢查日期`);
    if (vd.odometer !== undefined && vd.odometer !== null && vd.odometer !== '') done++;
    else missing.push(`${v.label}：未填里程數`);

    let itemDone = 0;
    MC_ITEMS.forEach(it => {
      const cell = ((vd.items || {})[it.key]) || {};
      if (cell.status === 'normal' || cell.status === 'abnormal') itemDone++;
      if (cell.status === 'abnormal') abnormal.push({ vehicle: v.label, item: it.label, note: cell.note || '' });
    });
    done += itemDone;
    if (itemDone < MC_ITEMS.length) missing.push(`${v.label}：檢查項未填齊（${itemDone}/${MC_ITEMS.length}）`);

    total += MC_WASH.length;
    MC_WASH.forEach(s => {
      if ((vd.wash || {})[s.key]) done++;
      else missing.push(`${v.label}：${s.label}洗車未記錄`);
    });
  });

  total += 2;
  const visits = (data.safeBox || {}).visits || [];
  [0, 1].forEach(i => {
    if (visits[i] && visits[i].date) done++;
    else missing.push(`銀行保險箱：第 ${i + 1} 次未前往`);
  });

  return { done, total, missing, abnormal, complete: done >= total };
}

async function mcLoadMonth(month) {
  const { getFirestore } = require('firebase-admin/firestore');
  const snap = await getFirestore().collection(MC_COLLECTION).doc(month).get();
  return snap.exists ? snap.data() : {};
}

async function mcSendMail(subject, text) {
  const transporter = nodemailer.createTransport({
    service: 'gmail',
    auth: { user: ALLOWED_EMAIL, pass: gmailAppPassword.value() }
  });
  const info = await transporter.sendMail({
    from: `"每月檢查" <${ALLOWED_EMAIL}>`,
    to: ALLOWED_EMAIL,
    subject,
    text: `${text}\n\n填寫與查詢：${MC_URL}\n\n— 每月檢查系統自動通知`
  });
  logger.info('monthly-check mail sent', { subject, messageId: info.messageId || null });
  return info;
}

// 未記錄洗車的車輛清單（slot: 'first' | 'second'）
function mcWashPending(data, slotKey) {
  return MC_VEHICLES
    .filter(v => !(((data.vehicles || {})[v.id] || {}).wash || {})[slotKey])
    .map(v => v.label);
}

// 每月 1 號 09:00：上月結案總結 ＋ 本月上半月洗車提醒（合成一封）
exports.monthlyCheckMonthlyMail = onSchedule(
  { schedule: '0 9 1 * *', timeZone: 'Asia/Taipei', secrets: [gmailAppPassword], region: 'asia-east1' },
  async () => {
    const thisMonth = mcMonthStr(0);
    const lastMonth = mcMonthStr(-1);
    const lastData = await mcLoadMonth(lastMonth);
    const st = mcStatus(lastData);

    const lines = [];
    lines.push(`【${mcMonthLabel(lastMonth)} 結案總結】`);
    lines.push(st.complete
      ? `✅ 全部完成（${st.done}/${st.total}）`
      : `⏳ 未完成（${st.done}/${st.total}），未補齊的項目：\n${st.missing.map(m => '• ' + m).join('\n')}`);

    lines.push('');
    if (st.abnormal.length) {
      lines.push(`⚠️ 本月異常 ${st.abnormal.length} 項：`);
      st.abnormal.forEach(a => lines.push(`• ${a.vehicle}｜${a.item}${a.note ? '：' + a.note : '（未填說明）'}`));
    } else {
      lines.push('本月無異常項目。');
    }

    MC_VEHICLES.forEach(v => {
      const od = ((lastData.vehicles || {})[v.id] || {}).odometer;
      if (od !== undefined && od !== null && od !== '') lines.push(`${v.label} 里程數：${Number(od).toLocaleString()} km`);
    });

    lines.push('');
    lines.push(`【${mcMonthLabel(thisMonth)} 上半月洗車提醒】`);
    lines.push(`本檔期該洗車了：${MC_VEHICLES.map(v => v.label).join('、')}`);

    await mcSendMail(
      `[每月檢查] ${mcMonthLabel(lastMonth)}結案${st.complete ? '' : '（未完成）'} ＋ ${Number(thisMonth.split('-')[1])}月上半月洗車提醒`,
      lines.join('\n')
    );
  }
);

// 每月 16 號 09:00：下半月洗車提醒（順帶點出上半月漏掉的）
exports.monthlyCheckWashReminder = onSchedule(
  { schedule: '0 9 16 * *', timeZone: 'Asia/Taipei', secrets: [gmailAppPassword], region: 'asia-east1' },
  async () => {
    const month = mcMonthStr(0);
    const data = await mcLoadMonth(month);
    const firstPending  = mcWashPending(data, 'first');
    const secondPending = mcWashPending(data, 'second');

    const lines = [`【${mcMonthLabel(month)} 下半月洗車提醒】`];
    lines.push(secondPending.length
      ? `本檔期該洗車了：${secondPending.join('、')}`
      : '下半月洗車都已記錄，辛苦了。');

    if (firstPending.length) {
      lines.push('');
      lines.push(`⚠️ 上半月還沒記錄到的：${firstPending.join('、')}`);
    }

    await mcSendMail(`[每月檢查] ${Number(month.split('-')[1])}月下半月該洗車了`, lines.join('\n'));
  }
);

// 每月 25 號 09:00：本月未完成才寄
exports.monthlyCheckOverdue = onSchedule(
  { schedule: '0 9 25 * *', timeZone: 'Asia/Taipei', secrets: [gmailAppPassword], region: 'asia-east1' },
  async () => {
    const month = mcMonthStr(0);
    const data = await mcLoadMonth(month);
    const st = mcStatus(data);

    if (st.complete) {
      logger.info('monthly-check overdue skipped: month complete', { month, done: st.done, total: st.total });
      return;
    }

    const lines = [
      `${mcMonthLabel(month)} 還有 ${st.missing.length} 項未完成（進度 ${st.done}/${st.total}）：`,
      ...st.missing.map(m => '• ' + m)
    ];

    if (st.abnormal.length) {
      lines.push('');
      lines.push(`⚠️ 目前已記錄的異常 ${st.abnormal.length} 項：`);
      st.abnormal.forEach(a => lines.push(`• ${a.vehicle}｜${a.item}${a.note ? '：' + a.note : '（未填說明）'}`));
    }

    await mcSendMail(`[每月檢查] ${Number(month.split('-')[1])}月還有 ${st.missing.length} 項未完成`, lines.join('\n'));
  }
);
