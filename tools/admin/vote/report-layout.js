// v1.0 (2026-10-02) [Codex] Approved v4 print layout, shared by all Office exports.
const COLORS = ['008C82','F27359','FFD84D'];
const SOURCE_NOTE = '人數依填答人計算，不含同行親友。';
function wrap(text, limit) {
  const lines=[]; let line='';
  for (const char of String(text)) {
    if (char==='\n' || line.length>=limit) {lines.push(line);line='';}
    if (char!=='\n' && char!=='\r') line+=char;
  }
  lines.push(line);return lines;
}
function namesText(names) {
  const lines=[];let line='',count=0;
  for (const name of names) {
    if (line && (count>=5 || line.length+String(name).length+1>30)) {lines.push(line);line='';count=0;}
    if (String(name).length>30) {
      if(line){lines.push(line);line='';count=0;}
      lines.push(...wrap(name,30));continue;
    }
    line+=(line?'、':'')+name;count++;
  }
  if(line)lines.push(line);
  return lines.length?lines.join('\n'):'無';
}
export function summaryLayout(r) {
  const groups=(r.roster.length?r.roster:[{label:'已填答',names:r.names.filter(n=>r.byName.has(n))}]).map((g,i)=>({...g,color:COLORS[i%COLORS.length]}));
  groups.push({label:'未填答',names:r.missing,color:COLORS[2],empty:r.voterTotal?'無':'未設定指定名單'});
  const rows=groups.map(g=>{
    const label=wrap(`${g.label}（${g.names.length}人）`,9).join('\n');
    const text=g.names.length?namesText(g.names):g.empty||'無';
    return {...g,label,text,height:Math.max(48, Math.max(label.split('\n').length,text.split('\n').length)*23+16)};
  });
  const title=wrap(r.title,27).join('\n');
  const titleHeight=Math.max(48,title.split('\n').length*28+12);
  const sorting='姓名依填寫時間先後排列；未填答者依原名單順序。'+(r.missingTimes?'缺填寫時間者列於已填答者最後。':'');
  const notes=[SOURCE_NOTE,sorting];
  if(r.rosterMulti)notes.push('本題為複選，同一人可能出現在不同選項。');
  return {rows,title,titleHeight,notes,height:titleHeight+48+rows.reduce((n,g)=>n+g.height,0)+notes.length*28+18,
    metrics:[`應填　${r.voterTotal||'未限定'}${r.voterTotal?' 人':''}`,`已填　${r.votes.length} 人`,r.voterTotal?`未填　${r.missing.length} 人`:'未填　不適用',`填答率　${r.voterTotal?(r.answeredNamed/r.voterTotal*100).toFixed(1)+'%':'—'}`]};
}
export async function createExcel(r, ExcelJS, onePage=false) {
  const layout=summaryLayout(r);
  if(onePage && layout.height>470)throw new Error('名單或題目過長，無法以清楚字級放入一頁；請使用完整 Excel。');
  const book=new ExcelJS.Workbook();book.creator='投票管理';book.created=new Date();
  const thin={style:'thin',color:{argb:'FF000000'}};
  const medium={style:'medium',color:{argb:'FF000000'}};
  const pageSetup={paperSize:9,orientation:'landscape',scale:100,fitToPage:false,horizontalCentered:true,verticalCentered:true,margins:{left:0.6,right:0.6,top:0.6,bottom:0.6,header:0.2,footer:0.2}};
  function page(name){
    const s=book.addWorksheet(name,{pageSetup:{...pageSetup,margins:{...pageSetup.margins}},views:[{showGridLines:false,zoomScale:100}]});
    for(let c=1;c<=16;c++)s.getColumn(c).width=6;
    return s;
  }
  function cell(s,row,start,end,value,{fill='FFFFFF',size=14,bold=false,align='left',height=48,indent=0,color='000000'}={}){
    s.mergeCells(row,start,row,end);const c=s.getCell(row,start);c.value=value;
    c.font={name:'Microsoft JhengHei',size,bold,italic:false,color:{argb:'FF'+color}};
    c.fill={type:'pattern',pattern:'solid',fgColor:{argb:'FF'+fill}};
    c.alignment={horizontal:align,vertical:'middle',wrapText:true,indent,shrinkToFit:false,textRotation:0};
    c.border={top:thin,bottom:thin,left:thin,right:thin};s.getRow(row).height=height;return c;
  }
  function frame(s,last){
    for(let col=1;col<=16;col++){
      const top=s.getCell(1,col),bottom=s.getCell(last,col);top.border={...top.border,top:medium};bottom.border={...bottom.border,bottom:medium};
    }
    for(let row=1;row<=last;row++){
      const left=s.getCell(row,1),right=s.getCell(row,16);left.border={...left.border,left:medium};right.border={...right.border,right:medium};
    }
    s.pageSetup.printArea=`A1:P${last}`;
  }
  const s=page('統計報告');let row=1;
  cell(s,row++,1,16,layout.title,{size:20,bold:true,align:'center',height:layout.titleHeight});
  layout.metrics.forEach((m,i)=>cell(s,row,i*4+1,i*4+4,m,{fill:'EDF4FC',size:15,bold:true,align:'center'}));row++;
  layout.rows.forEach(g=>{
    cell(s,row,1,4,g.label,{fill:g.color,bold:true,color:g.color==='008C82'?'FFFFFF':'000000',indent:1,height:g.height});
    cell(s,row++,5,16,g.text,{indent:1,height:g.height});
  });
  layout.notes.forEach(n=>{const c=cell(s,row++,1,16,n,{size:10,align:'center',height:28});c.border={left:thin,right:thin};});
  const time=cell(s,row++,1,16,`統計時間：${r.generated}（台北）`,{size:9,align:'right',height:18});time.border={left:thin,right:thin};
  frame(s,row-1);
  if(layout.height>470)s.pageSetup.verticalCentered=false;
  if(!onePage){
    // Narrow field/value pages preserve readable fonts even for questionnaires with many columns.
    const all=page('完整統計');let ar=1;
    cell(all,ar++,1,16,`${r.title} — 完整統計`,{size:18,bold:true,align:'center',height:60});
    cell(all,ar++,1,16,'比例以已填答人數計算；複選合計可超過 100%。量表以有效評分人數計算。',{size:11,height:40});
    r.stats.forEach(q=>{
      cell(all,ar++,1,16,q.title,{fill:'EDF4FC',bold:true,height:Math.max(40,wrap(q.title,40).length*23+12)});
      cell(all,ar++,1,16,wrap(q.summary,42).join('\n'),{height:Math.max(40,wrap(q.summary,42).length*23+12),indent:1});
      q.rows.forEach(item=>{cell(all,ar,1,10,wrap(item[0],24).join('\n'),{indent:1,height:Math.max(36,wrap(item[0],24).length*23+12)});cell(all,ar,11,13,item[1],{align:'center',height:all.getRow(ar).height});cell(all,ar++,14,16,item[2],{align:'center',height:all.getRow(ar).height});});
    });frame(all,ar-1);all.pageSetup.verticalCentered=false;
    const detail=page('填答明細');let dr=1;
    cell(detail,dr++,1,16,`${r.title} — 填答明細`,{size:18,bold:true,align:'center',height:60});
    r.details.forEach(values=>{
      cell(detail,dr++,1,16,values[0],{fill:'EDF4FC',bold:true,indent:1,height:36});
      r.headers.slice(1).forEach((h,i)=>{
        const chunks=wrap(values[i+1] || '—',30);const labelLines=wrap(h,10);
        for(let j=0;j<Math.max(chunks.length,labelLines.length);j+=10){
          const body=chunks.slice(j,j+10),label=labelLines.slice(j,j+10);
          const height=Math.max(36,Math.max(body.length,label.length)*23+12);
          cell(detail,dr,1,4,label.join('\n')||(j?'（續）':h),{indent:1,height});
          cell(detail,dr++,5,16,body.join('\n'),{indent:1,height});
        }
      });
    });
    if(!r.details.length)cell(detail,dr++,1,16,'尚無填答。');
    frame(detail,dr-1);detail.pageSetup.verticalCentered=false;
    const missing=page('未填答名單');let mr=1;
    cell(missing,mr++,1,16,'未填答名單',{size:18,bold:true,align:'center'});
    r.missing.forEach((name,i)=>{cell(missing,mr,1,4,i+1,{align:'center',height:36});cell(missing,mr++,5,16,name,{indent:1,height:36});});
    if(!r.missing.length)cell(missing,mr++,1,16,r.voterTotal?'全員已填答':'未設定指定名單');frame(missing,mr-1);
  }
  return new Blob([await book.xlsx.writeBuffer()],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
}
export async function createWord(r,d) {
  const layout=summaryLayout(r),width=11520;
  const border={style:d.BorderStyle.SINGLE,size:6,color:'000000'};
  const none={style:d.BorderStyle.NONE,size:0,color:'FFFFFF'};
  function p(text,{size=28,bold=false,align=d.AlignmentType.LEFT,color='000000',after=0}={}){
    return new d.Paragraph({alignment:align,spacing:{before:0,after},children:String(text).split(/\r?\n/).map((line,i)=>new d.TextRun({text:line,font:'Microsoft JhengHei',size,bold,italics:false,color,...(i?{break:1}:{})}))});
  }
  function tc(text,span,{fill='FFFFFF',size=28,bold=false,align=d.AlignmentType.LEFT,color='000000',inset=180,quiet=false}={}){
    return new d.TableCell({columnSpan:span,width:{size:width*span/16,type:d.WidthType.DXA},verticalAlign:d.VerticalAlign.CENTER,
      shading:{fill},margins:{top:120,bottom:120,left:inset,right:inset},borders:{top:quiet?none:border,bottom:quiet?none:border,left:border,right:border},children:[p(text,{size,bold,align,color})]});
  }
  const tr=(cells,height)=>new d.TableRow({height:{value:height*20,rule:d.HeightRule.ATLEAST},children:cells});
  function table(rows){return new d.Table({alignment:d.AlignmentType.CENTER,width:{size:width,type:d.WidthType.DXA},columnWidths:Array(16).fill(width/16),layout:d.TableLayoutType.FIXED,
    borders:{top:{...border,size:12},bottom:{...border,size:12},left:{...border,size:12},right:{...border,size:12},insideHorizontal:border,insideVertical:border},rows});}
  const rows=[tr([tc(layout.title,16,{size:40,bold:true,align:d.AlignmentType.CENTER})],layout.titleHeight),tr(layout.metrics.map(m=>tc(m,4,{fill:'EDF4FC',size:30,bold:true,align:d.AlignmentType.CENTER})),48)];
  layout.rows.forEach(g=>rows.push(tr([tc(g.label,4,{fill:g.color,bold:true,color:g.color==='008C82'?'FFFFFF':'000000',inset:240}),tc(g.text,12,{inset:240})],g.height)));
  layout.notes.forEach(n=>rows.push(tr([tc(n,16,{size:20,align:d.AlignmentType.CENTER,quiet:true})],22)));
  rows.push(tr([tc(`統計時間：${r.generated}（台北）`,16,{size:18,align:d.AlignmentType.RIGHT,quiet:true})],18));
  const front=[table(rows)];
  const following=[];
  function heading(text){return p(text,{size:32,bold:true,align:d.AlignmentType.CENTER,after:160});}
  following.push(heading('完整逐題統計'));
  r.stats.forEach(q=>{
    following.push(p(q.title,{bold:true,after:100}),p(q.summary,{after:100}),table(q.rows.map(item=>tr([tc(item[0],10),tc(item[1],3,{align:d.AlignmentType.CENTER}),tc(item[2],3,{align:d.AlignmentType.CENTER})],32))),p(''));
  });
  following.push(new d.Paragraph({pageBreakBefore:true,children:[new d.TextRun({text:'填答明細',bold:true,size:32})]}));
  r.details.forEach(values=>{
    following.push(p(values[0],{bold:true,after:100}),table(r.headers.slice(1).map((h,i)=>tr([tc(h,4,{fill:'EDF4FC'}),tc(values[i+1]||'—',12)],32))),p(''));
  });
  if(!r.details.length)following.push(p('尚無填答。'));
  const page={size:{width:11906,height:16838,orientation:d.PageOrientation.LANDSCAPE},margin:{top:864,bottom:864,left:864,right:864}};
  const document=new d.Document({styles:{default:{document:{run:{font:'Microsoft JhengHei',size:28,italics:false}}}},sections:[
    {properties:{page,verticalAlign:d.VerticalAlign.CENTER},children:front},
    {properties:{page,type:d.SectionType.NEXT_PAGE},children:following}
  ]});
  return d.Packer.toBlob(document);
}
