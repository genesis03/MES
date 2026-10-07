(() => {
'use strict';
const $=id=>document.getElementById(id),canWrite=$('cpApp').dataset.write==='true';
let items=[],flows=[],revisions=[],current=null,flow=null,rows=[],dirty=false,busy=false,selectedItemId='';
const textFields=['equipment','item_no','product','process','classification','specification','method','sample_size','sample_frequency','control_method','reaction','note'];
const boolFields=['fool_proof','automatic','material','production','quality','engineering'];
function message(text,error=false){$('cpMessage').textContent=text;$('cpMessage').dataset.error=String(error);if($('cpEditor').hidden){$('cpListMessage').textContent=text;$('cpListMessage').dataset.error=String(error);}}
const statusText=value=>({DRAFT:'초안',CURRENT:'현재 사용',SUPERSEDED:'구버전'}[value]||value);
function showEditor(title){$('cpEditorTitle').textContent=title;$('cpEditor').hidden=false;}
async function loadDocumentList(){
 const query=new URLSearchParams({keyword:$('cpKeyword').value.trim(),status:$('cpState').value});
 const documents=await api('/documents?'+query);
 const body=$('cpDocumentList');body.replaceChildren();
 for(const entry of documents){
  const tr=document.createElement('tr');
  for(const value of [entry.part_no,entry.part_name,entry.document_no,entry.revision_code,statusText(entry.status),entry.current_revision,entry.created_by+' / '+entry.created_at]){
   const td=document.createElement('td');td.textContent=value||'';tr.append(td);
  }
  const td=document.createElement('td'),button=document.createElement('button');button.type='button';button.className='cp-list-button';button.textContent='선택';
  button.onclick=()=>{
   if(busy||!guard())return;
   run(async()=>{
    const data=await api('/revisions/'+entry.revision_id);
    const previousId=$('cpItem').value;$('cpItem').value=data.item_id;
    try{await loadLists();}catch(error){$('cpItem').value=previousId;throw error;}
    selectedItemId=String(data.item_id);$('cpItemPartNo').value=entry.part_no;$('cpItemName').textContent=entry.part_name;
    display(data);message('저장된 문서를 불러왔습니다. 적용된 문서는 개정 등록 후 수정할 수 있습니다.');
    $('cpEditor').scrollIntoView({block:'start'});
   });
  };
  td.append(button);tr.append(td);body.append(tr);
 }
 if(!documents.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=8;td.textContent='등록된 관리계획서가 없습니다.';tr.append(td);body.append(tr);}
}
async function api(path,options={}){
 const response=await fetch('/api/control-plans'+path,options);const data=await response.json();
 if(!response.ok){const detail=data.detail;throw new Error(typeof detail==='string'?detail:detail?.errors?detail.message+'\n'+detail.errors.map(x=>x.cell+': '+x.message).join('\n'):Array.isArray(detail)?detail.map(x=>x.msg).join('\n'):'요청을 처리하지 못했습니다.');}return data;
}
function json(method,payload){return {method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)};}
function editable(){return canWrite&&items.find(x=>x.id===Number($('cpItem').value))?.selectable!==false&&(!current||current.status==='DRAFT');}
function guard(){return !dirty||confirm('저장하지 않은 입력이 있습니다. 다른 문서로 이동하시겠습니까?');}
function refreshControls(){
 const edit=editable()&&!busy;
 document.querySelector('.cp-form').disabled=!edit;
 $('cpDocumentNo').readOnly=!!current;
 $('cpRevisionCode').disabled=!edit||!!current;
 $('cpFlow').disabled=!edit;
 for(const id of ['cpSave','cpImport'])if($(id))$(id).disabled=!edit||!flow;
 if($('cpActivate'))$('cpActivate').disabled=!edit||!current||dirty;
 if($('cpRevise'))$('cpRevise').disabled=!canWrite||!current||current.status==='DRAFT'||busy;
 if($('cpInspectionLinks'))$('cpInspectionLinks').disabled=!current||dirty||busy||!canWrite||!['DRAFT','CURRENT'].includes(current.status);
 $('cpItem').disabled=busy;$('cpItemLookup').disabled=busy;$('cpRevision').disabled=busy;$('cpNew').disabled=busy;
 document.querySelectorAll('#cpSearchForm input,#cpSearchForm select,#cpSearchForm button,#cpDocumentList button,#cpReset,#cpCreate,#cpBack').forEach(el=>el.disabled=busy);
 document.querySelectorAll('#cpRows input,#cpRows textarea,#cpRows button').forEach(el=>el.disabled=!edit);
}
function header(){const result={};document.querySelectorAll('.cp-form [name]').forEach(el=>{if(el.name!=='document_no')result[el.name]=el.type==='checkbox'?el.checked:el.value;});return result;}
function fillHeader(data={}){document.querySelectorAll('.cp-form [name]').forEach(el=>{if(el.name==='document_no')return;if(el.type==='checkbox')el.checked=!!data[el.name];else el.value=data[el.name]||'';});}
function addOptions(select,values,placeholder,label){select.replaceChildren(new Option(placeholder,''));values.forEach(x=>select.add(new Option(label(x),String(x.id))));}
function emptyRow(step){return {flow_step_id:step.id,process_detail:step.step_name,...Object.fromEntries(textFields.map(x=>[x,''])),...Object.fromEntries(boolFields.map(x=>[x,false])),sub:'',main:'',outside:''};}
function rowInput(tr,row,field,label,type='text',colspan=1){const td=document.createElement('td');td.colSpan=colspan;const el=document.createElement(type==='checkbox'?'input':'textarea');if(type==='checkbox'){el.type='checkbox';el.checked=!!row[field];}else{el.value=row[field]||'';el.maxLength=4000;}el.setAttribute('aria-label',label);el.dataset.field=field;el.addEventListener('input',()=>{row[field]=type==='checkbox'?el.checked:el.value;dirty=true;refreshControls();});td.append(el);tr.append(td);}
function render(){
 const tbody=$('cpRows');tbody.replaceChildren();
 if(!flow){refreshControls();return;}
 for(const step of flow.steps){
  const grouped=rows.filter(r=>r.flow_step_id===step.id);
  if(!grouped.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=25;td.textContent=step.step_no+' '+step.step_name+' — 관리항목 없음 ';const add=document.createElement('button');add.textContent='관리항목 추가';add.onclick=()=>{rows.push(emptyRow(step));sortRows();dirty=true;render();};td.append(add);tr.append(td);tbody.append(tr);continue;}
  grouped.forEach((row,index)=>{
   const tr=document.createElement('tr');
   if(!index){
    for(const [field,value] of [['number',step.step_no],...['sub','main','outside'].map(field=>[field,grouped.find(r=>r[field])?.[field]||'']),['name',step.step_name]]){
     const td=document.createElement('td');td.rowSpan=grouped.length;td.className='cp-step cp-step-'+field;td.dataset.stepId=step.id;
     if(['sub','main','outside'].includes(field)){
      // CP retains lane placement; the actual symbol belongs to the linked flow.
      const lane=['outside','sub','main'].find(f=>grouped.some(r=>r[f]))||'main';
      if(field===lane)td.innerHTML=window.MESFlowSymbols.render(step.symbol_shape,step.symbol_name);
     }else td.textContent=value||'';
     tr.append(td);
    }
   }
   if(!index){
    const td=document.createElement('td');td.rowSpan=grouped.length;td.className='cp-step cp-step-equipment';td.dataset.stepId=step.id;
    const el=document.createElement('textarea');el.value=Array.from(new Set(grouped.map(r=>(r.equipment||'').trim()).filter(Boolean))).join('\n');el.maxLength=4000;el.dataset.field='equipment';el.setAttribute('aria-label',step.step_no+' 공정 설비명');
    el.addEventListener('input',()=>{grouped.forEach(r=>r.equipment=el.value);dirty=true;refreshControls();});td.append(el);tr.append(td);
   }
   rowInput(tr,row,'item_no','관리항목 NO');rowInput(tr,row,'product','제품 관리항목');rowInput(tr,row,'process','공정 관리항목');rowInput(tr,row,'classification','특별특성');
   rowInput(tr,row,'fool_proof','F/P','checkbox');rowInput(tr,row,'automatic','자동검사','checkbox');
   ['specification','method','sample_size','sample_frequency'].forEach((f,i)=>rowInput(tr,row,f,['규격','확인방법','샘플 크기','샘플 주기'][i]));rowInput(tr,row,'control_method','관리방안','text',2);
   ['material','production','quality','engineering'].forEach((f,i)=>rowInput(tr,row,f,['자재','생산','QC','기술'][i],'checkbox'));
   rowInput(tr,row,'reaction','이상 발생시 조치사항');rowInput(tr,row,'note','비고');
   const actions=document.createElement('td');actions.className='cp-actions';
   for(const [label,action] of [['추가',()=>{const added=emptyRow(step);added.equipment=row.equipment||'';['sub','main','outside'].forEach(field=>added[field]=grouped.find(r=>r[field])?.[field]||'');rows.splice(rows.indexOf(row)+1,0,added);}],['삭제',()=>{rows.splice(rows.indexOf(row),1);}]] ){
    const btn=document.createElement('button');btn.type='button';btn.textContent=label;btn.onclick=()=>{action();dirty=true;render();};actions.append(btn);
   }
   tr.append(actions);tbody.append(tr);
  });
 }
 refreshControls();
}
function sortRows(){const order=new Map(flow.steps.map((s,i)=>[s.id,i]));rows.sort((a,b)=>order.get(a.flow_step_id)-order.get(b.flow_step_id));}
async function loadLists(){
 const id=Number($('cpItem').value);if(!id){flows=[];revisions=[];}else [flows,revisions]=await Promise.all([api('/items/'+id+'/flows'),api('/revisions?item_id='+id)]);
 addOptions($('cpFlow'),flows,'공정흐름도 선택',x=>x.revision_code+' · '+x.status);
 addOptions($('cpRevision'),revisions,'새 문서',x=>x.document_no+' · '+x.revision_code+' · '+x.status);
}
function newDocument(){showEditor('신규 관리계획서');current=null;dirty=false;fillHeader(items.find(x=>x.id===Number($('cpItem').value))||{});$('cpDocumentNo').value='';$('cpRevisionCode').value='REV.0';$('cpRevision').value='';flow=flows.find(x=>x.status==='CURRENT')||flows[0]||null;$('cpFlow').value=flow?flow.id:'';rows=flow?flow.steps.map(emptyRow):[];render();message(flow?'공정 순서에 맞춰 관리항목을 입력하거나 회사 엑셀 파일을 불러오세요.':!$('cpItem').value?'완제품 품목을 조회하여 선택하세요.':'먼저 해당 완제품의 공정흐름도를 적용해 주세요.');}
function display(data){showEditor(($('cpItemPartNo').value||'')+' · '+data.document_no+' · '+data.revision_code+' · '+statusText(data.status));current=data;flow=data.flow;rows=data.rows;fillHeader(data.header);$('cpDocumentNo').value=data.document_no;$('cpRevisionCode').value=data.revision_code;$('cpRevision').value=data.id;
 if(!Array.from($('cpFlow').options).some(x=>x.value===String(flow.id)))$('cpFlow').add(new Option(flow.revision_code,flow.id));$('cpFlow').value=flow.id;dirty=false;render();}
async function run(action){if(busy)return;busy=true;refreshControls();try{await action();}catch(error){message(error.message,true);}finally{busy=false;refreshControls();}}
$('cpSearchForm').onsubmit=event=>{event.preventDefault();run(loadDocumentList);};
$('cpReset').onclick=()=>{if(busy)return;$('cpKeyword').value='';$('cpState').value='';run(loadDocumentList);};
if($('cpCreate'))$('cpCreate').onclick=()=>{
 if(busy||!guard())return;
 $('cpItem').value='';selectedItemId='';$('cpItemPartNo').value='';$('cpItemName').textContent='';
 flows=[];revisions=[];addOptions($('cpFlow'),[],'공정흐름도 선택',()=> '');addOptions($('cpRevision'),[],'새 문서',()=> '');
 newDocument();$('cpEditor').scrollIntoView({block:'start'});$('cpItemLookup').focus();
};
$('cpBack').onclick=()=>{if(busy||!guard())return;dirty=false;$('cpEditor').hidden=true;$('cpListMessage').textContent='관리계획서를 선택하거나 신규 등록해 주세요.';$('cpListMessage').dataset.error='false';run(loadDocumentList);$('cpSearchForm').scrollIntoView({block:'start'});};
function renderItemLookup(){
 const keyword=$('cpItemKeyword').value.trim().toLocaleLowerCase();
 const matches=items.filter(x=>!keyword||(x.part_no+' '+x.part_name).toLocaleLowerCase().includes(keyword));
 const body=$('cpItemResults');body.replaceChildren();
 for(const item of matches){
  const tr=document.createElement('tr');
  for(const value of [item.part_no,item.part_name]){const td=document.createElement('td');td.textContent=value;tr.append(td);}
  const td=document.createElement('td'),button=document.createElement('button');button.type='button';button.textContent=item.selectable===false?'이력 조회':'선택';
  button.onclick=()=>{
   if(busy||!guard())return;
   $('cpItemDialog').close();
   run(async()=>{
    const previousId=selectedItemId;$('cpItem').value=item.id;
    try{await loadLists();}catch(error){$('cpItem').value=previousId;throw error;}
    selectedItemId=String(item.id);$('cpItemPartNo').value=item.part_no;$('cpItemName').textContent=item.part_name;newDocument();
   });
  };td.append(button);tr.append(td);body.append(tr);
 }
 if(!matches.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=3;td.textContent='조회 결과가 없습니다.';tr.append(td);body.append(tr);}
}
$('cpItemLookup').onclick=()=>{if(busy)return;$('cpItemKeyword').value='';renderItemLookup();$('cpItemDialog').showModal();$('cpItemKeyword').focus();};
$('cpItemDialogClose').onclick=()=>$('cpItemDialog').close();
$('cpItemSearchForm').onsubmit=event=>{event.preventDefault();renderItemLookup();};
$('cpRevision').addEventListener('change',()=>run(async()=>{if(!guard()){$('cpRevision').value=current?.id||'';return;}if(!$('cpRevision').value)newDocument();else{display(await api('/revisions/'+$('cpRevision').value));message('저장된 문서를 불러왔습니다. 적용된 문서는 개정 등록 후 수정할 수 있습니다.');}}));
$('cpNew').onclick=()=>{if(guard())newDocument();};
$('cpFlow').addEventListener('change',()=>{const next=flows.find(x=>x.id===Number($('cpFlow').value))||null;
 if(rows.some(r=>textFields.some(f=>r[f]))&&!confirm('공정흐름도를 바꾸면 현재 관리항목을 새 공정 기준으로 다시 작성합니다. 계속하시겠습니까?')){$('cpFlow').value=flow?.id||'';return;}
 flow=next;rows=flow?flow.steps.map(emptyRow):[];dirty=true;render();});
document.querySelector('.cp-form').addEventListener('input',()=>{dirty=true;refreshControls();});$('cpRevisionCode').addEventListener('input',()=>{dirty=true;refreshControls();});
if($('cpSave'))$('cpSave').onclick=()=>run(async()=>{if(!flow)throw Error('공정흐름도를 선택해 주세요.');const payload={item_id:Number($('cpItem').value),document_no:$('cpDocumentNo').value,revision_code:$('cpRevisionCode').value,flow_revision_id:flow.id,flow_version:flow.version,header:header(),rows,version:current?.version||null};
 const result=await api('/revisions'+(current?'/'+current.id:''),json(current?'PUT':'POST',payload));await loadLists();display(result);await loadDocumentList();message('상단 정보와 관리항목을 초안으로 저장했습니다.');});
if($('cpActivate'))$('cpActivate').onclick=()=>run(async()=>{if(!current||dirty)throw Error('먼저 초안을 저장해 주세요.');if(!confirm('이 개정을 현재 사용하는 관리계획서로 적용하시겠습니까?'))return;const result=await api('/revisions/'+current.id+'/activate',json('POST',{version:current.version}));await loadLists();display(result);await loadDocumentList();message('현재 사용 적용을 완료했습니다.');});
if($('cpRevise'))$('cpRevise').onclick=()=>run(async()=>{const code=prompt('새 관리계획서 개정번호를 입력하세요.');if(!code)return;const result=await api('/revisions/'+current.id+'/revise',json('POST',{version:current.version,revision_code:code}));await loadLists();display(result);await loadDocumentList();message('기존 내용으로 새 개정 초안을 만들었습니다.');});
if($('cpImport'))$('cpImport').onclick=()=>{if(!flow){message('품목과 기준 공정흐름도를 선택해 주세요.',true);return;}$('cpFile').value='';$('cpFile').click();};
$('cpFile').onchange=()=>run(async()=>{const file=$('cpFile').files[0];if(!file)return;if(file.size>10*1024*1024)throw Error('파일 크기는 10MB 이하이어야 합니다.');
 if(rows.some(r=>textFields.some(f=>r[f]))&&!confirm('하단 관리항목을 엑셀 내용으로 교체하시겠습니까? 상단 입력은 유지됩니다.'))return;
 message('엑셀 양식과 공정 순서를 확인하고 있습니다.');const body=new FormData();body.append('file',file);body.append('item_id',$('cpItem').value);body.append('flow_revision_id',flow.id);body.append('flow_version',flow.version);if(current){body.append('revision_id',current.id);body.append('revision_version',current.version);}
 const data=await api('/import-excel',{method:'POST',body});rows=data.rows;dirty=true;render();message(data.message+' ('+rows.length+'개 관리항목)');});
if($('cpInspectionLinks'))$('cpInspectionLinks').onclick=()=>run(async()=>{
 const planId=current.id;
 const data=await api('/revisions/'+planId+'/inspection-links');
 const dialog=document.createElement('dialog');
 dialog.style.cssText='width:min(900px,95vw);max-height:85vh;margin:auto;padding:20px;border:1px solid #94a3b8;border-radius:6px;overflow:auto';
 const heading=document.createElement('h2');heading.textContent='검사기준서 연결 설정';heading.style.fontSize='18px';dialog.append(heading);
 const help=document.createElement('p');help.textContent='각 관리계획서 공정이 연결될 검사구분을 지정하세요. 미지정 공정은 불러오기에서 제외됩니다. 공정검사는 품번에 등록된 공정코드와 연결합니다.';help.style.cssText='font-size:13px;margin:12px 0';dialog.append(help);
 const fields=[];
 for(const step of current.flow.steps){
  const line=document.createElement('div');line.style.cssText='display:grid;grid-template-columns:1fr 180px 180px;gap:8px;margin:8px 0;align-items:center';
  const label=document.createElement('span');label.textContent=step.step_no+' · '+step.step_name;
  const category=document.createElement('select');
  for(const [value,text] of [['','미지정'],['RAW_INBOUND','자재 입고'],['SUBCONTRACT_INBOUND','외주가공 입고'],['PROCESS','공정검사'],['FINAL','최종검사']])category.add(new Option(text,value));
  const process=document.createElement('select');process.add(new Option('내부 공정 선택',''));
  data.processes.forEach(p=>process.add(new Option(p.name+' ('+p.code+')',p.code)));
  const old=data.links.find(x=>x.flow_step_id===step.id);category.value=old?.category||'';process.value=old?.process_code||'';
  const toggle=()=>{process.disabled=category.value!=='PROCESS';if(process.disabled)process.value='';};category.onchange=toggle;toggle();
  line.append(label,category,process);dialog.append(line);fields.push({step,category,process});
 }
 const error=document.createElement('p');error.style.color='#b91c1c';dialog.append(error);
 const save=document.createElement('button');save.textContent='연결 저장';save.style.cssText='padding:8px 16px;margin:12px 8px 0 0';
 const close=document.createElement('button');close.textContent='닫기';close.style.padding='8px 16px';close.onclick=()=>dialog.close();
 save.onclick=async()=>{
  if(current?.id!==planId||dirty){error.textContent='문서가 변경되었습니다. 닫고 다시 열어 주세요.';return;}
  const links=fields.filter(x=>x.category.value).map(x=>({flow_step_id:x.step.id,category:x.category.value,process_code:x.process.value||null}));
  if(links.some(x=>x.category==='PROCESS'&&!x.process_code)){error.textContent='공정검사에는 내부 공정을 선택해 주세요.';return;}
  save.disabled=true;
  try{const result=await api('/revisions/'+planId+'/inspection-links',json('PUT',{version:current.version,links}));display(result);message('검사기준서 연결을 저장했습니다.');dialog.close();}
  catch(e){error.textContent=e.message;}finally{save.disabled=false;}
 };
 dialog.append(save,close);dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
});
window.addEventListener('beforeprint',()=>{document.querySelectorAll('.cp-form input:not([type=checkbox]),.cp-form textarea').forEach(el=>{const span=document.createElement('span');span.className='cp-header-value';span.textContent=el.type==='date'?el.value.replace(/-/g,'.'):el.value;el.after(span);});document.querySelectorAll('#cpRows textarea').forEach(el=>{const span=document.createElement('span');span.className='cp-print-value';span.textContent=el.value;el.after(span);});});
window.addEventListener('afterprint',()=>document.querySelectorAll('.cp-print-value,.cp-header-value').forEach(el=>el.remove()));
$('cpPrint').onclick=async()=>{
 const preview=window.open('','_blank','width=1200,height=850,scrollbars=yes,resizable=yes');
 if(!preview){message('미리보기 창을 열 수 없습니다. 이 사이트의 팝업을 허용해 주세요.',true);return;}
 preview.opener=null;
 const doc=preview.document;
 doc.title='관리계획서 인쇄 미리보기';
 doc.body.textContent='미리보기를 준비하고 있습니다.';
 try{
  const cssUrl=document.querySelector('link[href*="/static/css/control_plan.css"]').href;
  const response=await fetch(cssUrl);
  if(!response.ok)throw Error('인쇄 양식을 불러오지 못했습니다. 다시 시도해 주세요.');
  const css=await response.text();
  if(preview.closed)return;
  const paper=document.querySelector('.cp-paper').cloneNode(true);
  paper.querySelectorAll('.cp-header-value,.cp-print-value,.cp-actions,button').forEach(el=>el.remove());
  const sourceControls=document.querySelector('.cp-paper').querySelectorAll('input,textarea');
  paper.querySelectorAll('input,textarea').forEach((el,index)=>{
   const source=sourceControls[index];
   if(el.type==='checkbox'){el.checked=source.checked;el.disabled=true;return;}
   const span=doc.createElement('span');
   span.className=el.closest('.cp-form')?'cp-header-value':'cp-print-value';
   span.textContent=el.type==='date'?source.value.replace(/-/g,'.'):source.value;
   el.replaceWith(span);
  });
  const style=doc.createElement('style');
  style.textContent=css.replace(/@media\s+print\s*\{/g,'@media all{')+`
   *{box-sizing:border-box;margin:0;padding:0}
   html{background:#e2e8f0}
   body{display:block!important;background:#e2e8f0!important;overflow:auto!important;font-family:Arial,sans-serif}
   .cp-preview-toolbar{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:16px;padding:12px 20px;background:#fff;border-bottom:1px solid #cbd5e1}
   .cp-preview-toolbar button{padding:8px 20px;border:0;border-radius:4px;background:#2563eb;color:#fff;cursor:pointer;font-size:14px}
   .cp-paper{width:277mm;margin:10mm auto;background:#fff;box-shadow:0 2px 12px #0002}
   .cp-header-scroll,.cp-lower-scroll{overflow:visible}
   @media print{html,body{background:#fff!important}.cp-preview-toolbar{display:none}.cp-paper{width:100%;margin:0;box-shadow:none}}
  `;
  doc.head.append(style);
  const toolbar=doc.createElement('div');toolbar.className='cp-preview-toolbar';
  const label=doc.createElement('span');label.textContent='관리계획서 인쇄 미리보기 · A4 가로';
  const button=doc.createElement('button');button.type='button';button.textContent='인쇄';button.onclick=()=>preview.print();
  toolbar.append(label,button);doc.body.replaceChildren(toolbar,doc.importNode(paper,true));
 }catch(error){if(!preview.closed)preview.close();message(error.message,true);}
};
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
run(async()=>{items=await api('/options');render();await loadDocumentList();});
})();
