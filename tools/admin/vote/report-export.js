// v2.3 (2026-10-02) [Codex] Approved compact landscape frame for XLSX/DOCX; one-page export.
// v2.2 (2026-10-02) Larger upright Excel text and named response groups on summary.
// v2.1 (2026-10-02) Browser-local XLSX/DOCX reports with shared statistics.
import { createExcel, createWord } from './report-layout.js?v=1.0';
const loaders = {};
function library(name, file) {
  if (globalThis[name]) return Promise.resolve(globalThis[name]);
  if (!loaders[name]) loaders[name] = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = new URL(file, import.meta.url).href;
    script.onload = () => globalThis[name] ? resolve(globalThis[name]) : reject(new Error('匯出元件未載入'));
    script.onerror = () => { delete loaders[name]; script.remove(); reject(new Error('匯出元件載入失敗，請重試')); };
    document.head.append(script);
  });
  return loaders[name];
}
const pct = (n, d) => d ? (n / d * 100).toFixed(1) + '%' : '—';
function timestamp(value) {
  if (!value) return '';
  const date = value.toDate ? value.toDate() : value.seconds != null ? new Date(value.seconds * 1000) : new Date(value);
  return Number.isNaN(+date) ? '' : date.toLocaleString('zh-TW', {timeZone:'Asia/Taipei', hour12:false});
}
export function reportData(data, helpers) {
  const {answerOf, answerText, isChoice} = helpers;
  const timeValue = value => {
    if (!value) return Infinity;
    const n = value.toDate ? +value.toDate() : value.seconds != null ? value.seconds * 1000 + (value.nanoseconds || 0)/1e6 : +new Date(value);
    return Number.isFinite(n) ? n : Infinity;
  };
  data = {...data, votes:[...data.votes].sort((a,b) => timeValue(a.createdAt)-timeValue(b.createdAt))};
  const named = [...new Set(data.voters)];
  const byName = new Map(data.votes.map(v => [v.id, v]));
  const missing = named.filter(n => !byName.has(n));
  const names = [...new Set([...data.votes.map(v => v.id), ...named])];
  const answeredNamed = named.length - missing.length;
  // Prefer an attendance question; otherwise show the first choice question's real labels.
  const choices = data.questions.filter(isChoice);
  const rosterQuestion = choices.find(q => /參加|出席/.test(q.title || '')) || choices[0];
  const roster = [];
  if (rosterQuestion) {
    const respondents = names.filter(name => byName.has(name));
    const selected = predicate => respondents.filter(name => predicate(answerOf(byName.get(name), rosterQuestion)));
    rosterQuestion.options.forEach((o,i) => roster.push({label:o.title || `選項 ${i+1}`, names:selected(a => a.picks.includes(o.id))}));
    const other = selected(a => Boolean(a.other));
    if (other.length) roster.push({label:'其他（自填）', names:other});
    const deleted = selected(a => a.picks.some(id => !rosterQuestion.options.some(o => o.id === id)));
    if (deleted.length) roster.push({label:'已刪除的選項', names:deleted});
    const skipped = selected(a => !a.picks.length && !a.other);
    if (skipped.length) roster.push({label:'已填表／本題未填', names:skipped});
  }
  const stats = data.questions.map((q, index) => {
    const answers = data.votes.map(v => answerOf(v, q));
    let rows, summary;
    if (isChoice(q)) {
      rows = q.options.map((o, i) => {
        const n = answers.filter(a => a.picks.includes(o.id)).length;
        return [o.title || `選項 ${i + 1}`, n, pct(n, data.votes.length)];
      });
      const other = answers.filter(a => a.other).length;
      if (other) rows.push(['其他（自填）', other, pct(other, data.votes.length)]);
      const deleted = new Set(answers.flatMap(a => a.picks).filter(id => !q.options.some(o => o.id === id)));
      deleted.forEach(id => { const n = answers.filter(a => a.picks.includes(id)).length; rows.push(['（已刪除的選項）', n, pct(n, data.votes.length)]); });
      summary = rows.map(r => `${r[0]} ${r[1]} 票（${r[2]}）`).join('；');
    } else if (q.type === 'scale') {
      const vals = answers.map(a => a.value).filter(v => v != null);
      summary = vals.length ? `平均 ${(vals.reduce((a,b) => a+b, 0)/vals.length).toFixed(2)} 分，${vals.length} 人作答` : '尚無有效作答';
      rows = Array.from({length:q.scaleMax-q.scaleMin+1}, (_, i) => {
        const score = q.scaleMin+i, n = vals.filter(v => v === score).length;
        return [`${score} 分`, n, pct(n, vals.length)];
      });
    } else {
      const count = answers.filter(a => a.text.trim()).length;
      summary = `${count} 筆文字回答（全文見明細）`;
      rows = [['文字回答', count, '']];
    }
    return {title:`Q${index+1} ${q.title || '（未命名）'}`, rows, summary, choice:isChoice(q)};
  });
  return {...data, stats, missing, names, byName, roster, voterTotal:named.length, answeredNamed,
    missingTimes:data.votes.some(v => timeValue(v.createdAt)===Infinity),
    rosterMulti:rosterQuestion?.type === 'multi', rosterTitle:rosterQuestion?.title || '', generated:timestamp(new Date()),
    metrics:`應填 ${named.length || '未限定'} 人 ｜ 已填 ${data.votes.length} 人 ｜ 未填 ${named.length ? missing.length : '不適用'} 人 ｜ 填答率 ${pct(answeredNamed, named.length)}`,
    headers:['填答人', '填答狀態', ...data.questions.map((q,i) => `Q${i+1} ${q.title || ''}`), ...(data.allowComment ? ['留言'] : []), '填答時間（台北）'],
    details:names.map(name => { const v = byName.get(name); return [name, v ? '已填答' : '未填答', ...data.questions.map(q => v ? answerText(v,q) : ''), ...(data.allowComment ? [v?.comment || ''] : []), v ? timestamp(v.createdAt) : '']; })};
}
function save(blob, filename) {
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = filename; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
export async function exportOfficeReport(format, data, helpers) {
  const report = reportData(data,helpers);
  const onePage = format === 'xlsx-onepage';
  const extension = onePage ? 'xlsx' : format;
  const blob = extension === 'xlsx'
    ? await createExcel(report, await library('ExcelJS','./vendor/exceljs.min.js'), onePage)
    : await createWord(report, await library('docx','./vendor/docx.js'));
  const safe = report.title.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,80) || '投票';
  save(blob,`${safe}_${onePage?'一頁報告':'統計報告'}_${new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Taipei'})}.${extension}`);
}
