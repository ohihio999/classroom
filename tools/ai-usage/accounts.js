/* AGY account pool UI v1.0.1 — 2026-10-05: local-only subscription notes for six accounts.
   v1.0.0 — 2026-10-05: dynamic accounts, pairing, OAuth, stale quota. */
export function initAccountPool({ db, doc, onSnapshot, meter, fmtTime }) {
  const SERVER = 'http://127.0.0.1:8772';
  const body = document.getElementById('agy-accounts-body');
  const badge = document.getElementById('agy-local-badge');
  const msg = document.getElementById('agy-switch-msg');
  const state = document.getElementById('agy-pool-state');
  const pair = document.getElementById('agy-pair');
  const add = document.getElementById('agy-add');
  const refresh = document.getElementById('agy-refresh');
  let session = '', popup = null, local = null, cloud = null, working = false;
  const legacy = {};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function call(path, payload) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const res = await fetch(SERVER + path, {
        method: payload ? 'POST' : 'GET', cache: 'no-store', signal: controller.signal,
        headers: { 'X-Agy-Session': session, ...(payload ? {'Content-Type':'application/json'} : {}) },
        ...(payload ? {body:JSON.stringify(payload)} : {})
      });
      const value = await res.json();
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) { session = ''; local = null; }
        throw new Error(value.error || `HTTP ${res.status}`);
      }
      return value;
    } finally { clearTimeout(timer); }
  }

  function quotaView(q) {
    if (!q || !q.collectedAt) return `<div class="state-note">${esc(q?.error || '尚未取得用量，請在這台電腦授權帳號。')}</div>`;
    const age = Date.now() - Date.parse(q.collectedAt);
    const allMeters = [...(q.groups || []).flatMap(g => g.buckets || []), ...(q.models || []), q.session, q.weekly, q.claude, q.claudeWeekly].filter(Boolean);
    const resetPassed = allMeters.some(m => m.resetTime && Date.parse(m.resetTime) < Date.now());
    const stale = !Number.isFinite(age) || age > 30*60000 || resetPassed || (q.status && q.status !== 'ok');
    let html = '';
    if (q.groups?.length) {
      for (const group of q.groups) for (const b of group.buckets || []) html += meter(esc(`${group.label} ${b.label || b.window}`), b);
    } else if (q.models?.length) {
      for (const m of q.models) html += meter(esc(m.label || m.id), m);
    } else {
      html = meter('Session 額度', q.session) + meter('Weekly 額度', q.weekly) + meter('Claude 額度', q.claude) + meter('Claude Weekly 額度', q.claudeWeekly);
    }
    return (stale ? '<div class="stale" style="margin-bottom:8px">舊資料・待更新，以下數字不是即時剩餘量</div>' : '')
      + html + `<div class="meter-reset">最後成功 ${fmtTime(q.collectedAt)}${resetPassed ? '・部分重置時間已過，尚未確認新用量' : ''}</div>`
      + (q.error ? `<div class="err">${esc(q.error)}</div>` : '')
      + (q.status && q.status !== 'ok' && q.nextAttempt ? `<div class="meter-reset">下次查詢 ${fmtTime(new Date(q.nextAttempt*1000).toISOString())}</div>` : '');
  }

  function render() {
    const accounts = local?.accounts || cloud?.accounts || ['a1','a2','a3'].map(key => ({key}));
    const quotas = local?.quotas || cloud?.quotas || legacy;
    badge.textContent = local ? '本機已連接' : '僅檢視';
    add.disabled = !local || working;
    refresh.disabled = !local || working || local.busy || local.manualCooldown > 0;
    refresh.textContent = local?.busy ? '背景更新中…' : local?.manualCooldown > 0 ? `等待 ${local.manualCooldown} 秒` : '立即更新全部';
    pair.textContent = local ? '重新配對本機' : '連接本機';
    const heartbeat = cloud?.heartbeat;
    const stopped = heartbeat && Date.now()-Date.parse(heartbeat) > 15*60000;
    state.textContent = local ? `每 15 分鐘查詢各帳號。網頁同步：${local.sync?.status === 'ok' ? '正常' : local.sync?.error || '等待首次同步'}`
      : heartbeat ? `最後同步 ${fmtTime(heartbeat)}${stopped ? '・背景同步已停止，請確認電腦與服務已開啟' : ''}`
      : '在這台電腦連接本機服務，即可新增帳號並開始更新。手機可查看同步後的用量。';
    let html = '';
    for (const [index,a] of accounts.entries()) {
      const key = a.key;
      const q = quotas[key] || legacy[key];
      const name = local ? `${a.label || key}（${a.email || ''}）` : `AGY 帳號 ${index+1}`;
      html += `<div class="acct"><div class="acct-head"><span class="acct-name">${esc(name)}</span>${a.isLive ? '<span class="badge on">CLI 目前登入</span>' : ''}</div>`
        + (local && a.subscriptionNote ? `<div class="meter-reset" style="margin-bottom:10px">${esc(a.subscriptionNote)}</div>` : '')
        + quotaView(q)
        + (local ? `<div class="acct-actions"><button class="act-btn primary" data-act="auth" data-key="${esc(key)}" ${working?'disabled':''}>授權用量</button><button class="act-btn" data-act="refresh" data-key="${esc(key)}" ${working||local.busy||local.manualCooldown>0?'disabled':''}>更新此帳號</button><button class="act-btn" data-act="switch" data-key="${esc(key)}" ${working||!a.saved||a.isLive?'disabled':''}>切換 CLI 帳號</button></div>` : '')
        + (local && !a.saved ? '<div class="meter-reset">用量授權獨立於 CLI 切換。此帳號尚無 CLI 登入快照。</div>' : '') + '</div>';
    }
    body.innerHTML = html || '<div class="state-note">尚無帳號，請新增。</div>';
  }

  async function poll() {
    if (!session || working) { render(); return; }
    try { local = await call('/api/status'); }
    catch(e) { local = null; msg.textContent = `本機未連接：${e.message}。請先啟動帳號服務再配對。`; }
    render();
  }

  pair.addEventListener('click', () => {
    popup = window.open(SERVER + '/pair?origin=' + encodeURIComponent(location.origin), 'agy-pair', 'popup,width=600,height=420');
    msg.textContent = popup ? '請在本機配對視窗按「允許連接」。' : '請允許此頁的彈出視窗後重試。';
  });
  window.addEventListener('message', event => {
    if (event.origin !== SERVER || event.source !== popup || event.data?.type !== 'agy-paired' || typeof event.data.token !== 'string') return;
    session = event.data.token; popup = null; msg.textContent = '本機已配對，登入憑證保存在這台電腦。'; poll();
  });

  async function authorize(key = '') {
    // Create popup within the user's click to avoid asynchronous popup blocking.
    const login = window.open('about:blank', 'agy-login', 'popup,width=700,height=760');
    if (!login) { msg.textContent = '請允許此頁的彈出視窗後重試。'; return; }
    working = true; render();
    try {
      const d = await call('/api/oauth/start', {key});
      const url = new URL(d.url);
      if (url.origin !== 'https://accounts.google.com') throw new Error('授權網址不正確');
      login.location = url.href;
      msg.textContent = '請在 Google 視窗選擇帳號並完成授權，完成後回到此頁。';
    } catch(e) { login.close(); msg.textContent = e.message; }
    finally { working = false; render(); }
  }

  async function update(key = '') {
    working = true; render();
    try { const d = await call('/api/refresh', key ? {key} : {}); msg.textContent = d.message || d.error; }
    catch(e) { msg.textContent = e.message; }
    finally { working = false; await poll(); }
  }
  add.addEventListener('click', () => authorize());
  refresh.addEventListener('click', () => update());
  body.addEventListener('click', async event => {
    const button = event.target.closest('[data-act]');
    if (!button || button.disabled || working) return;
    const {key,act} = button.dataset;
    if (act === 'auth') return authorize(key);
    if (act === 'refresh') return update(key);
    if (act !== 'switch' || !confirm('切換只影響 AGY CLI 的登入，請先關閉 AGY。用量查詢不需要切換。要繼續嗎？')) return;
    working = true; render();
    try {
      const d = await call('/api/switch', {key,killAgy:false});
      msg.textContent = d.ok ? '已切換 CLI 登入，請重新開啟 AGY。' : d.reason === 'agy_running' ? 'AGY 還在執行，請先關閉後再切換。' : d.error || d.reason;
    } catch(e) { msg.textContent = e.message; }
    finally { working = false; await poll(); }
  });

  onSnapshot(doc(db,'ai_usage','agy_pool'), snap => {
    try {
      const value = snap.exists() ? JSON.parse(snap.data().data || '{}') : null;
      cloud = value && Array.isArray(value.accounts) ? value : null;
    } catch { cloud = null; }
    render();
  }, () => { state.textContent = '帳號池同步讀取失敗；可連接本機查看。'; });
  for (const key of ['a1','a2','a3']) onSnapshot(doc(db,'ai_usage','agy_acct_'+key), snap => {
    try { legacy[key] = snap.exists() ? JSON.parse(snap.data().data || 'null') : null; }
    catch { legacy[key] = null; }
    render();
  });
  setInterval(() => { if (!document.hidden) poll(); }, 10000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });
  render();
}
