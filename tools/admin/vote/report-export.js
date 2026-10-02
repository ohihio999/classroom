// v2.2 (2026-10-02) [Codex] Larger upright Excel text and named response groups on summary.
// v2.1 (2026-10-02) Browser-local XLSX/DOCX reports with shared statistics.
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
const clip = (s, n) => String(s).length > n ? String(s).slice(0, n - 1) + '…' : String(s);
function timestamp(value) {
  if (!value) return '';
  const date = value.toDate ? value.toDate() : value.seconds != null ? new Date(value.seconds * 1000) : new Date(value);
  return Number.isNaN(+date) ? '' : date.toLocaleString('zh-TW', {timeZone:'Asia/Taipei', hour12:false});
}
export function reportData(data, helpers) {
  const {answerOf, answerText, isChoice} = helpers;
  const named = [...new Set(data.voters)];
  const byName = new Map(data.votes.map(v => [v.id, v]));
  const missing = named.filter(n => !byName.has(n));
  const names = [...named, ...data.votes.map(v => v.id).filter(n => !named.includes(n))];
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
  return {...data, stats, missing, names, byName, roster, rosterTitle:rosterQuestion?.title || '', generated:timestamp(new Date()),
    metrics:`應填 ${named.length || '未限定'} 人 ｜ 已填 ${data.votes.length} 人 ｜ 未填 ${named.length ? missing.length : '不適用'} 人 ｜ 填答率 ${pct(answeredNamed, named.length)}`,
    headers:['填答人', '填答狀態', ...data.questions.map((q,i) => `Q${i+1} ${q.title || ''}`), ...(data.allowComment ? ['留言'] : []), '填答時間（台北）'],
    details:names.map(name => { const v = byName.get(name); return [name, v ? '已填答' : '未填答', ...data.questions.map(q => v ? answerText(v,q) : ''), ...(data.allowComment ? [v?.comment || ''] : []), v ? timestamp(v.createdAt) : '']; })};
}
const note = '選擇題比例以全部已填答人數計算；複選合計可超過 100%。票數不等於實際出席人數。';
function save(blob, filename) {
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = filename; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
async function excel(r) {
  const ExcelJS = await library('ExcelJS', './vendor/exceljs.min.js');
  const book = new ExcelJS.Workbook();
  book.creator = '投票管理'; book.created = new Date();
  function sheet(name, widths, landscape = false) {
    const s = book.addWorksheet(name, {pageSetup:{paperSize:9, orientation:landscape ? 'landscape':'portrait', fitToPage:false, scale:100, margins:{left:0.3,right:0.3,top:0.4,bottom:0.4,header:0.2,footer:0.2}}, views:[{state:'frozen', ySplit:1, zoomScale:110}]});
    s.columns = widths.map(width => ({width}));
    s.headerFooter.oddFooter = '&C第 &P 頁／共 &N 頁';
    return s;
  }
  function add(s, values, heading = false) {
    const row = s.addRow(values);
    row.eachCell({includeEmpty:true}, c => {
      c.font = {name:'Microsoft JhengHei', size:heading?16:14, bold:heading, italic:false, color:{argb:heading?'FFFFFFFF':'FF243447'}};
      c.alignment = {vertical:'top', wrapText:true, textRotation:0, shrinkToFit:false};
      c.fill = {type:'pattern', pattern:'solid', fgColor:{argb:heading?'FF1976D2':row.number%2?'FFF2F6FA':'FFFFFFFF'}};
      c.border = {bottom:{style:'hair', color:{argb:'FFDCE3EA'}}};
      c.numFmt = typeof c.value === 'number' ? '0' : '@';
    });
    row.height = Math.min(409, Math.max(30, ...values.map((v,i) => String(v ?? '').split('\n').reduce((n,line) => n+Math.max(1,Math.ceil(line.length*(heading?3.2:2.8)/(s.columns[i]?.width || 30))),0)*24+10)));
    return row;
  }
  const s = sheet('統計報告', [23,65]);
  const fullOnFront = r.stats.length <= 4 && r.stats.reduce((n,q) => n+q.rows.length,0) <= 10 && r.stats.every(q => q.title.length < 55 && q.rows.every(row => String(row[0]).length < 35));
  add(s, [clip(r.title,80),'統計報告'],true);
  add(s, ['匯出時間',r.generated]); add(s,['填答概況',r.metrics]);
  add(s,['名單總結',r.rosterTitle || '填答狀態'],true);
  function rosterRows(label, people, empty = '無') {
    // Split long rosters into rows instead of hitting Excel's row-height limit.
    const chunks = []; let line = '';
    people.forEach(name => { if (line && (line.length + String(name).length > 70)) {chunks.push(line); line='';} line += (line?'、':'') + name; });
    if (line) chunks.push(line);
    if (!chunks.length) chunks.push(empty);
    chunks.forEach((text,i) => add(s,[i?'（續）':`${label}（${people.length} 人）`,text]));
  }
  r.roster.forEach(group => rosterRows(group.label,group.names));
  if (!r.roster.length) rosterRows('已填答',r.names.filter(name => r.byName.has(name)));
  rosterRows('未填答',r.missing,r.voters.length?'無':'未設定指定填答名單');
  add(s,['名單說明','依填答人分組；多人同行的實際姓名與資料見填答明細。']);
  // Summary names get the first printed pages; detailed statistics start afterwards.
  s.getRow(s.rowCount).addPageBreak();
  if (fullOnFront) r.stats.forEach(q => { add(s,[q.title,q.choice?'數量／比例':q.summary],true); q.rows.forEach(row => add(s,[row[0],`${row[1]}　${row[2]} ${q.choice ? '▰'.repeat(Math.round(Number.parseFloat(row[2]) / 10) || 0) : ''}`])); });
  else {
    r.stats.slice(0,6).forEach(q => add(s,[clip(q.title,45),clip(q.summary,110)]));
    add(s,['完整統計','全部題目與選項請見後續「完整統計」工作表。']);
  }
  add(s,['統計說明',note]);
  s.pageSetup.printArea = `A1:B${s.rowCount}`;
  const detail = sheet('填答明細',r.headers.map((_,i) => i===0?18:i===1?12:35),true);
  add(detail,r.headers,true); r.details.forEach(row => add(detail,row));
  detail.autoFilter = {from:{row:1,column:1},to:{row:Math.max(1,detail.rowCount),column:r.headers.length}};
  detail.pageSetup.printTitlesRow = '1:1';
  const missing = sheet('未填答名單',[10,30]); add(missing,['序號','姓名'],true);
  r.missing.forEach((name,i) => add(missing,[i+1,name]));
  if (!r.missing.length) add(missing,['',r.voters.length?'全員已填答':'未設定指定填答名單']);
  const all = sheet('完整統計',[45,45,14,15]); add(all,['題目','項目','數量','比例'],true);
  r.stats.forEach(q => { add(all,[q.title,q.summary,'','']); q.rows.forEach(row => add(all,['',...row])); });
  all.pageSetup.printTitlesRow = '1:1';
  return new Blob([await book.xlsx.writeBuffer()],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
async function word(r) {
  const d = await library('docx','./vendor/docx.js');
  const p = (text, options={}) => new d.Paragraph({spacing:{after:100}, ...options, children:String(text).split(/\r?\n/).map((line,i) => new d.TextRun({text:line,...(i?{break:1}:{})}))});
  const heading = text => p(text,{heading:d.HeadingLevel.HEADING_2, keepNext:true});
  const table = rows => new d.Table({width:{size:100,type:d.WidthType.PERCENTAGE}, rows:rows.map((row,i) => new d.TableRow({tableHeader:i===0, children:row.map(value => new d.TableCell({shading:{fill:i===0?'DDEBF7':i%2?'F3F6FA':'FFFFFF'},children:[p(value)]}))}))});
  const compact = r.stats.length <= 4 && r.stats.reduce((n,q) => n+q.rows.length,0)<=10 && r.stats.every(q => q.title.length<55 && q.rows.every(row => String(row[0]).length<35));
  const front = [p(clip(r.title,80),{heading:d.HeadingLevel.TITLE}), p('統計報告'),p(`匯出時間：${r.generated}（台北）`),p(r.metrics)];
  if (compact) r.stats.forEach(q => {front.push(heading(q.title), table([['項目',q.choice?'數量／比例':q.summary],...q.rows.map(row => [row[0],`${row[1]}　${row[2]} ${q.choice?'▰'.repeat(Math.round(Number.parseFloat(row[2])/10)||0):''}`])]));});
  else {front.push(table([['題目','摘要'],...r.stats.slice(0,6).map(q => [clip(q.title,40),clip(q.summary,90)])]),p('全部題目與選項詳見後續完整統計。'));}
  front.push(p(note));
  const following = [];
  if (!compact) {following.push(heading('完整逐題統計')); r.stats.forEach(q => following.push(heading(q.title),p(q.summary),table([['項目','數量','比例'],...q.rows])));}
  following.push(heading('填答明細'));
  r.details.filter(row => row[1]==='已填答').forEach(row => {
    following.push(heading(row[0]),table([['欄位','內容'],...r.headers.slice(1).map((h,i) => [h,row[i+1] || '—'])]));
  });
  if (!r.votes.length) following.push(p('尚無填答。'));
  following.push(heading('未填答名單'),table([['序號','姓名'],...r.missing.map((name,i)=>[i+1,name])]));
  if (!r.missing.length) following.push(p(r.voters.length?'全員已填答':'未設定指定填答名單'));
  const footer = new d.Footer({children:[new d.Paragraph({alignment:d.AlignmentType.CENTER,children:[new d.TextRun({children:['第 ',d.PageNumber.CURRENT,' 頁']})]})]});
  const properties = {page:{size:{width:11906,height:16838},margin:{top:850,bottom:850,left:850,right:850}},type:d.SectionType.NEXT_PAGE};
  const document = new d.Document({styles:{default:{document:{run:{font:'Microsoft JhengHei',size:20},paragraph:{spacing:{after:100}}}}},sections:[{properties,footers:{default:footer},children:front},{properties,footers:{default:footer},children:following}]});
  return d.Packer.toBlob(document);
}
export async function exportOfficeReport(format, data, helpers) {
  const report = reportData(data,helpers);
  const blob = format === 'xlsx' ? await excel(report) : await word(report);
  const safe = report.title.replace(/[<>:"/\\|?*\x00-\x1f]/g,'_').slice(0,80) || '投票';
  save(blob,`${safe}_統計報告_${new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Taipei'})}.${format}`);
}
