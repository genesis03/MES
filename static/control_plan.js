(() => {
'use strict';
const $=id=>document.getElementById(id),canWrite=$('cpApp').dataset.write==='true';
let items=[],flows=[],revisions=[],current=null,flow=null,rows=[],dirty=false,busy=false,selectedItemId='';
const textFields=['equipment','item_no','product','process','classification','specification','method','sample_size','sample_frequency','control_method','reaction','note'];
const boolFields=['fool_proof','automatic','material','production','quality','engineering'];
function message(text,error=false){$('cpMessage').textContent=text;$('cpMessage').dataset.error=String(error);}
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
 $('cpItem').disabled=busy;$('cpRevision').disabled=busy;$('cpNew').disabled=busy;
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
   if(!index){const td=document.createElement('td');td.rowSpan=grouped.length;td.className='cp-step';td.textContent=step.step_no;tr.append(td);}
   ['sub','main','outside'].forEach(f=>rowInput(tr,row,f,f));
   rowInput(tr,row,'process_detail','공정명 상세');rowInput(tr,row,'equipment','설비명');rowInput(tr,row,'item_no','관리항목 NO');rowInput(tr,row,'product','제품 관리항목');rowInput(tr,row,'process','공정 관리항목');rowInput(tr,row,'classification','특별특성');
   rowInput(tr,row,'fool_proof','F/P','checkbox');rowInput(tr,row,'automatic','자동검사','checkbox');
   ['specification','method','sample_size','sample_frequency'].forEach((f,i)=>rowInput(tr,row,f,['규격','확인방법','샘플 크기','샘플 주기'][i]));rowInput(tr,row,'control_method','관리방안','text',2);
   ['material','production','quality','engineering'].forEach((f,i)=>rowInput(tr,row,f,['자재','생산','QC','기술'][i],'checkbox'));
   rowInput(tr,row,'reaction','이상 발생시 조치사항');rowInput(tr,row,'note','비고');
   const actions=document.createElement('td');actions.className='cp-actions';
   for(const [label,action] of [['추가',()=>{rows.splice(rows.indexOf(row)+1,0,emptyRow(step));}],['삭제',()=>{rows.splice(rows.indexOf(row),1);}]] ){
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
function newDocument(){current=null;dirty=false;fillHeader(items.find(x=>x.id===Number($('cpItem').value))||{});$('cpDocumentNo').value='';$('cpRevisionCode').value='REV.0';$('cpRevision').value='';flow=flows.find(x=>x.status==='CURRENT')||flows[0]||null;$('cpFlow').value=flow?flow.id:'';rows=flow?flow.steps.map(emptyRow):[];render();message(flow?'공정 순서에 맞춰 관리항목을 입력하거나 회사 엑셀 파일을 불러오세요.':'먼저 해당 완제품의 공정흐름도를 적용해 주세요.');}
function display(data){current=data;flow=data.flow;rows=data.rows;fillHeader(data.header);$('cpDocumentNo').value=data.document_no;$('cpRevisionCode').value=data.revision_code;$('cpRevision').value=data.id;
 if(!Array.from($('cpFlow').options).some(x=>x.value===String(flow.id)))$('cpFlow').add(new Option(flow.revision_code,flow.id));$('cpFlow').value=flow.id;dirty=false;render();}
async function run(action){if(busy)return;busy=true;refreshControls();try{await action();}catch(error){message(error.message,true);}finally{busy=false;refreshControls();}}
$('cpItem').addEventListener('change',()=>run(async()=>{if(!guard()){$('cpItem').value=selectedItemId;return;}await loadLists();selectedItemId=$('cpItem').value;newDocument();}));
$('cpRevision').addEventListener('change',()=>run(async()=>{if(!guard()){$('cpRevision').value=current?.id||'';return;}if(!$('cpRevision').value)newDocument();else{display(await api('/revisions/'+$('cpRevision').value));message('저장된 문서를 불러왔습니다. 적용된 문서는 개정 등록 후 수정할 수 있습니다.');}}));
$('cpNew').onclick=()=>{if(guard())newDocument();};
$('cpFlow').addEventListener('change',()=>{const next=flows.find(x=>x.id===Number($('cpFlow').value))||null;
 if(rows.some(r=>textFields.some(f=>r[f]))&&!confirm('공정흐름도를 바꾸면 현재 관리항목을 새 공정 기준으로 다시 작성합니다. 계속하시겠습니까?')){$('cpFlow').value=flow?.id||'';return;}
 flow=next;rows=flow?flow.steps.map(emptyRow):[];dirty=true;render();});
document.querySelector('.cp-form').addEventListener('input',()=>{dirty=true;refreshControls();});$('cpRevisionCode').addEventListener('input',()=>{dirty=true;refreshControls();});
if($('cpSave'))$('cpSave').onclick=()=>run(async()=>{if(!flow)throw Error('공정흐름도를 선택해 주세요.');const payload={item_id:Number($('cpItem').value),document_no:$('cpDocumentNo').value,revision_code:$('cpRevisionCode').value,flow_revision_id:flow.id,flow_version:flow.version,header:header(),rows,version:current?.version||null};
 const result=await api('/revisions'+(current?'/'+current.id:''),json(current?'PUT':'POST',payload));await loadLists();display(result);message('상단 정보와 관리항목을 초안으로 저장했습니다.');});
if($('cpActivate'))$('cpActivate').onclick=()=>run(async()=>{if(!current||dirty)throw Error('먼저 초안을 저장해 주세요.');if(!confirm('이 개정을 현재 사용하는 관리계획서로 적용하시겠습니까?'))return;const result=await api('/revisions/'+current.id+'/activate',json('POST',{version:current.version}));await loadLists();display(result);message('현재 사용 적용을 완료했습니다.');});
if($('cpRevise'))$('cpRevise').onclick=()=>run(async()=>{const code=prompt('새 관리계획서 개정번호를 입력하세요.');if(!code)return;const result=await api('/revisions/'+current.id+'/revise',json('POST',{version:current.version,revision_code:code}));await loadLists();display(result);message('기존 내용으로 새 개정 초안을 만들었습니다.');});
if($('cpImport'))$('cpImport').onclick=()=>{if(!flow){message('품목과 기준 공정흐름도를 선택해 주세요.',true);return;}$('cpFile').value='';$('cpFile').click();};
$('cpFile').onchange=()=>run(async()=>{const file=$('cpFile').files[0];if(!file)return;if(file.size>10*1024*1024)throw Error('파일 크기는 10MB 이하이어야 합니다.');
 if(rows.some(r=>textFields.some(f=>r[f]))&&!confirm('하단 관리항목을 엑셀 내용으로 교체하시겠습니까? 상단 입력은 유지됩니다.'))return;
 message('엑셀 양식과 공정 순서를 확인하고 있습니다.');const body=new FormData();body.append('file',file);body.append('item_id',$('cpItem').value);body.append('flow_revision_id',flow.id);body.append('flow_version',flow.version);if(current){body.append('revision_id',current.id);body.append('revision_version',current.version);}
 const data=await api('/import-excel',{method:'POST',body});rows=data.rows;dirty=true;render();message(data.message+' ('+rows.length+'개 관리항목)');});
window.addEventListener('beforeprint',()=>{document.querySelectorAll('#cpRows textarea').forEach(el=>{const span=document.createElement('span');span.className='cp-print-value';span.textContent=el.value;el.after(span);});});
window.addEventListener('afterprint',()=>document.querySelectorAll('.cp-print-value').forEach(el=>el.remove()));
$('cpPrint').onclick=()=>window.print();
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
run(async()=>{items=await api('/options');addOptions($('cpItem'),items,'품목 선택',x=>x.part_no+' · '+x.part_name);render();});
})();
