(() => {
'use strict';
const root=document.getElementById('flowApp');if(!root)return;
const el=id=>document.getElementById(id),write=root.dataset.canWrite==='true';
const label={DRAFT:'초안',CURRENT:'현재 사용',SUPERSEDED:'구버전',RETIRED:'폐기'};
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 도형 표현만 코드에 정의합니다. 선택 목록/명칭은 공통코드 API에서 가져옵니다.
const shapes={
 CIRCLE:'<circle cx="16" cy="16" r="11"/>',
 ARROW:'<path d="M3 11H17V5L29 16L17 27V21H3Z"/>',
 SQUARE:'<rect x="5" y="5" width="22" height="22"/>',
 DIAMOND:'<path d="M16 3L29 16L16 29L3 16Z"/>',
 INVERTED_TRIANGLE:'<path d="M3 5H29L16 28Z"/>',
 DELAY:'<path d="M5 5H16A11 11 0 0 1 16 27H5Z"/>',
 // 복합기호는 주 기능을 바깥쪽, 보조 기능을 안쪽에 표시합니다.
 DIAMOND_SQUARE:'<path d="M16 3L29 16L16 29L3 16Z"/><rect x="9.5" y="9.5" width="13" height="13"/>',
 SQUARE_DIAMOND:'<rect x="5" y="5" width="22" height="22"/><path d="M16 5L27 16L16 27L5 16Z"/>',
 CIRCLE_SQUARE:'<circle cx="16" cy="16" r="12"/><rect x="8" y="8" width="16" height="16"/>',
 CIRCLE_ARROW:'<circle cx="16" cy="16" r="13"/><path d="M6 12H16V7L25 16L16 25V20H6Z"/>'
};
let registrants={can_select:false,current_user_id:null,current_user_name:'',users:[]},revisionSource=null;
let selected=null,steps=[],records=[],symbols=[],items=[],busy=false,dirty=false,ready=false,correcting=false;
let activeStep=null;
function message(t,error=false){el('flowMessage').textContent=t;el('flowMessage').className=error?'flow-error':'';}
function errorText(detail){
 if(!Array.isArray(detail))return typeof detail==='string'?detail:'요청을 처리하지 못했습니다.';
 return detail.map(x=>{
  const loc=Array.isArray(x.loc)?x.loc:[],i=loc.indexOf('steps'),field=loc.at(-1);
  const names={step_no:'공정번호',step_name:'공정명',symbol_code:'기호',note:'비고',revision_code:'개정번호',item_id:'완제품 품목'};
  if(i>=0&&Number.isInteger(loc[i+1])){
   const number=loc[i+1]+1;
   if(field==='step_no'||field==='step_name')return number+'번째 공정의 '+names[field]+'를 입력해 주세요. 사용하지 않는 행은 ‘삭제’를 눌러주세요.';
   return number+'번째 공정의 '+(names[field]||'입력값')+'를 확인해 주세요.';
  }
  return (names[field]||'입력값')+'를 확인해 주세요.';
 }).join('\n');
}
async function request(url,method='GET',body){
 const r=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
 const d=await r.json();if(!r.ok){const e=Error(errorText(d.detail));e.details=d.detail;throw e;}return d;
}
function editable(){return write&&ready&&(!selected||selected.status==='DRAFT'||(correcting&&selected.status==='CURRENT'))&&(!selected||selected.item_selectable===true);}
function correctable(){return write&&ready&&selected?.status==='CURRENT'&&selected.item_selectable===true;}
function textEditable(){return editable()||(correcting&&correctable());}
function registrantOptions(){
 return '<option value="">등록자 선택</option>'+registrants.users.map(x=>'<option value="'+x.id+'">'+esc(x.name+(x.department?' · '+x.department:''))+'</option>').join('');
}
function setRegistrant(selectId,nameId,existing=null){
 const select=el(selectId),name=el(nameId);if(!select||!name)return;
 const choose=registrants.can_select&&!existing;
 select.hidden=!choose;name.hidden=choose;
 select.innerHTML=registrantOptions();
 select.value=String(registrants.users.some(x=>x.id===registrants.current_user_id)?registrants.current_user_id:'');
 name.value=existing?(existing.registrant_name||existing.created_by):registrants.current_user_name;
 name.placeholder='계정에 성명을 먼저 등록해 주세요.';
}
function registrantId(selectId){
 const id=registrants.can_select?Number(el(selectId).value):registrants.current_user_id;
 if(!id||(!registrants.can_select&&!registrants.current_user_name))throw Error('등록자를 선택하거나 계정의 성명을 먼저 등록해 주세요.');
 return id;
}
function printSummary(){
 el('flowPrintSummary').innerHTML=selected?'<tr><td>'+esc(selected.part_no_snapshot)+'</td><td>'+esc(selected.part_name_snapshot)+'</td><td>'+esc(selected.revision_code)+'</td><td>'+esc(label[selected.status])+'</td><td>'+esc(selected.change_reason||'최초 등록')+'</td><td>'+esc(selected.created_by)+' / '+esc(selected.created_at)+'</td></tr>':'';
}
function controls(){
 const edit=editable()&&!busy;
 root.querySelectorAll('.flow-fields input,.flow-fields select,.flow-fields textarea,#flowSteps input,#flowSteps select,#flowSteps textarea,[data-step-op]').forEach(x=>x.disabled=!edit);
 root.querySelectorAll('[data-step-op]').forEach(button=>{
  const index=Number(button.dataset.index),op=button.dataset.stepOp;
  button.disabled=!edit||(op==='up'&&index===0)||(op==='down'&&index===steps.length-1);
 });
 if(el('flowCorrect')){el('flowCorrect').disabled=busy||!correctable();el('flowCorrect').textContent=correcting?'수정 저장':'수정';}
 if(el('flowCorrectCancel')){el('flowCorrectCancel').hidden=!correcting;el('flowCorrectCancel').disabled=busy;}
 el('flowCorrectionPanel').hidden=!correcting;el('flowCorrectionReason').disabled=busy||!correcting;
 el('flowItem').disabled=!!selected||!edit;el('flowCode').disabled=!!selected||!edit;
 el('flowItemKeyword').disabled=!!selected||!edit;el('flowItemSearch').disabled=!!selected||!edit;
 root.querySelectorAll('[data-pick-flow-item]').forEach(x=>x.disabled=!!selected||!edit);
 el('flowRegistrant').disabled=!!selected||!edit||!registrants.can_select;
 if(el('flowSave'))el('flowSave').disabled=!edit||correcting;
 if(el('flowAdd'))el('flowAdd').disabled=!edit;
 if(el('flowNew'))el('flowNew').disabled=busy||!ready;
 if(el('flowActivate'))el('flowActivate').disabled=!selected||!edit||selected.status!=='DRAFT';
 if(el('flowRevise'))el('flowRevise').disabled=busy||!write||!selected||selected.status==='DRAFT'||selected.item_selectable!==true||correcting;
 if(el('flowRetire'))el('flowRetire').disabled=busy||!write||!selected||selected.status==='RETIRED'||correcting;
 el('flowPrint').disabled=busy||!selected||dirty||correcting;
 root.querySelectorAll('[data-flow-id]').forEach(x=>x.disabled=busy);
 ['flowSearch','flowReset'].forEach(id=>el(id).disabled=busy||!ready);
}
function abandon(){return !dirty||confirm('저장하지 않은 입력을 버리고 이동할까요?');}
function clearErrors(){el('flowSteps').querySelectorAll('[aria-invalid]').forEach(x=>x.removeAttribute('aria-invalid'));}
function focusStep(index,field){
 const input=el('flowSteps').querySelector('[data-index="'+index+'"][data-field="'+field+'"]');
 if(input){input.setAttribute('aria-invalid','true');input.scrollIntoView({block:'center'});if(!input.disabled)input.focus();}
}
async function task(fn){
 if(busy)return;busy=true;controls();let failure=null;
 try{await fn();}catch(e){failure=e;message(e.message,true);}finally{busy=false;controls();}
 if(Array.isArray(failure?.details)){
  const d=failure.details.find(x=>Array.isArray(x.loc)&&x.loc.includes('steps'));
  if(d){const i=d.loc.indexOf('steps');if(Number.isInteger(d.loc[i+1]))focusStep(d.loc[i+1],d.loc.at(-1));}
 }
}
function symbolSvg(shape,name){
 return shapes[shape]?'<svg class="flow-symbol" viewBox="0 0 32 32" role="img" aria-label="'+esc(name||'공정 기호')+'"><g fill="white" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round">'+shapes[shape]+'</g></svg>':'<span class="flow-symbol-empty" title="기호 미지정">—</span>';
}
function diagram(){
 const item=items.find(x=>x.id===Number(el('flowItem').value));
 const number=selected?.part_no_snapshot||item?.part_no||'',name=selected?.part_name_snapshot||item?.part_name||'';
 el('flowDiagramTitle').textContent=[number,name].filter(Boolean).join(' · ')||'공정흐름도';
 el('flowDiagramRevision').textContent='공정흐름도 개정번호: '+(selected?.revision_code||(el('flowCode').value.trim()?'REV.'+el('flowCode').value.trim():'미입력'))+' · '+(selected?label[selected.status]:'작성 중');
 el('flowDiagram').innerHTML=steps.length?steps.map(s=>'<div class="flow-diagram-row"><div class="flow-symbol-cell">'+symbolSvg(s.symbol_shape,s.symbol_name)+'</div><div class="flow-diagram-number">'+esc(s.step_no||'미입력')+'</div><div class="flow-diagram-name">'+esc(s.step_name||'공정명 미입력')+(!s.symbol_code?'<small class="flow-note">기호 미지정</small>':'')+'</div></div>').join(''):'<p class="flow-note">공정을 등록해 주세요.</p>';
}
function symbolOptions(step){
 let options='<option value="">기호 선택</option>';
 if(step.symbol_code&&!symbols.some(x=>x.code===step.symbol_code))options+='<option selected value="'+esc(step.symbol_code)+'">'+esc((step.symbol_name||step.symbol_code)+' · 기존 기호')+'</option>';
 return options+symbols.map(x=>'<option value="'+esc(x.code)+'"'+(x.code===step.symbol_code?' selected':'')+'>'+esc(x.code===step.symbol_code&&step.symbol_name?step.symbol_name:x.name)+'</option>').join('');
}
function renderSteps(){
 el('flowSteps').innerHTML=steps.map((s,i)=>{
  const caption=(i+1)+'번째 공정';
  const action='<td class="flow-row-actions"><button type="button" class="flow-btn light" data-step-op="up" data-index="'+i+'" aria-label="'+caption+' 위로 이동">위</button> <button type="button" class="flow-btn light" data-step-op="down" data-index="'+i+'" aria-label="'+caption+' 아래로 이동">아래</button> <button type="button" class="flow-btn light" data-step-op="remove" data-index="'+i+'" aria-label="'+caption+' 삭제">삭제</button></td>';
  return '<tr data-step-row="'+i+'"'+(s===activeStep?' class="flow-step-active"':'')+'>'+action+'<td class="flow-symbol-editor"><select data-index="'+i+'" data-field="symbol_code" aria-label="'+caption+' 기호">'+symbolOptions(s)+'</select>'+symbolSvg(s.symbol_shape,s.symbol_name)+'</td><td class="flow-process-editor"><input data-index="'+i+'" data-field="step_no" aria-label="'+caption+'번호" placeholder="공정번호" maxlength="50" value="'+esc(s.step_no)+'"><input data-index="'+i+'" data-field="step_name" aria-label="'+caption+'명" placeholder="공정명" maxlength="200" value="'+esc(s.step_name)+'"></td><td class="flow-note-editor"><textarea rows="1" data-index="'+i+'" data-field="note" aria-label="'+caption+' 비고" maxlength="4000">'+esc(s.note)+'</textarea></td></tr>';
 }).join('');
 diagram();controls();
}
function revealActiveStep(){
 const index=steps.indexOf(activeStep);
 if(index<0)return;
 requestAnimationFrame(()=>{
  const row=el('flowSteps').querySelector('[data-step-row="'+index+'"]');
  row?.scrollIntoView({block:'nearest',inline:'nearest'});
 });
}
function renderList(){
 el('flowList').innerHTML=records.length?records.map(x=>'<tr><td>'+esc(x.part_no)+'</td><td>'+esc(x.part_name)+'</td><td>'+esc(x.revision_code)+'</td><td>'+esc(label[x.status])+'</td><td>'+esc(x.change_reason||'최초 등록')+'</td><td>'+esc(x.created_by)+' / '+esc(x.created_at)+'</td><td><button class="flow-btn light" data-flow-id="'+x.id+'">선택</button></td></tr>').join(''):'<tr><td colspan="7">등록된 공정흐름도가 없습니다.</td></tr>';controls();
}
async function list(){records=await request('/api/process-flows?keyword='+encodeURIComponent(el('flowKeyword').value));renderList();}
async function history(itemId){
 const all=await request('/api/process-flows'),revs=all.filter(x=>x.item_id===itemId);
 el('flowHistory').innerHTML=revs.map(x=>'<tr><td>'+esc(x.revision_code)+'</td><td>'+esc(x.created_at)+'</td><td>'+esc(x.activated_at||'미적용')+'</td><td>'+esc(x.change_reason||'최초 등록')+'</td><td>'+esc(x.created_by)+'</td><td>'+esc(label[x.status])+'</td><td class="no-print"><button class="flow-btn light" data-flow-id="'+x.id+'">조회</button></td></tr>').join('');
}
function historyValue(change,value){
 if(/공정 추가|공정 삭제/.test(change.field)&&value){
  try{const s=JSON.parse(value);return [s.step_no,s.step_name,s.symbol_name?'기호: '+s.symbol_name:'','순서: '+s.sort_order,s.note?'비고: '+s.note:''].filter(Boolean).join(' · ');}catch(_){/* 이전 문자열 기록은 그대로 표시합니다. */}
 }
 return String(value??'');
}
function historyChanges(log){
 const grouped=new Map(),priorities={'기호 도형':1,'기호 코드':2,'기호 명칭':3};
 for(const change of log.changes||[]){
  const symbol=Object.hasOwn(priorities,change.field),field=symbol?'기호':change.field;
  const key=JSON.stringify([change.step_no,field]);
  if(!symbol){grouped.set(key,{...change,field});continue;}
  const priority=priorities[change.field],previous=grouped.get(key);
  if(!previous||priority>previous.priority)grouped.set(key,{...change,field,priority});
 }
 return Array.from(grouped.values());
}
async function correctionHistory(){
 const logs=selected?await request('/api/process-flows/'+selected.id+'/corrections'):[];
 el('flowCorrectionHistory').innerHTML=logs.length?logs.map(log=>{
  const changes=historyChanges(log);
  const column=fn=>changes.map(change=>'<div>'+esc(fn(change))+'</div>').join('');
  return '<tr><td>'+esc(String(log.corrected_at).split('.')[0])+'</td><td>'+esc(log.corrected_by)+'</td><td>'+esc(log.reason)+'</td><td>'+column(change=>(change.step_no||'문서')+' · '+change.field)+'</td><td>'+column(change=>historyValue(change,change.before))+'</td><td>'+column(change=>historyValue(change,change.after))+'</td></tr>';
 }).join(''):'<tr><td colspan="6">수정 이력이 없습니다.</td></tr>';
}
function fill(x){
 el('flowItemKeyword').value=x.part_no;
 el('flowItemDisplay').textContent=x.part_no+' · '+x.part_name+(x.item_selectable?'':' · 기존 이력 조회 전용');
 el('flowItemResults').hidden=true;el('flowItemResults').innerHTML='';
 activeStep=null;selected=x;setRegistrant('flowRegistrant','flowRegistrantName',x);printSummary();steps=x.steps;dirty=false;correcting=false;el('flowCorrectionReason').value='';el('flowEditor').hidden=false;
 el('flowTitle').textContent=x.part_no+' · '+x.part_name+' · '+x.revision_code+' · '+label[x.status];
 el('flowItem').value=x.item_id;MesRevisionNumber.setInput(el('flowCode'),x.revision_code);el('flowNote').value=x.note;
 el('flowMeta').textContent='당시 품목: '+x.part_no_snapshot+' · '+x.part_name_snapshot+' | 등록: '+x.created_by+' · '+x.created_at+(x.retire_reason?' | 폐기 사유: '+x.retire_reason:'')+(x.item_selectable?'':' | 완제품 선택 대상이 아닙니다. 기존 이력은 조회 전용이며 폐기만 가능합니다.');
 renderSteps();
}
async function select(id){const x=await request('/api/process-flows/'+id);fill(x);await history(x.item_id);await correctionHistory();}
if(el('flowNew'))el('flowNew').onclick=()=>{
 if(!abandon())return;
 task(async()=>{el('flowItemKeyword').value='';el('flowItemDisplay').textContent='선택된 품목 없음';el('flowItemResults').hidden=true;el('flowItemResults').innerHTML='';activeStep=null;selected=null;setRegistrant('flowRegistrant','flowRegistrantName');printSummary();steps=[];dirty=false;correcting=false;el('flowEditor').hidden=false;el('flowTitle').textContent='신규 공정흐름도';['flowItem','flowNote'].forEach(id=>el(id).value='');MesRevisionNumber.setInput(el('flowCode'),'');el('flowMeta').textContent='';el('flowHistory').innerHTML='';el('flowCorrectionHistory').innerHTML='';renderSteps();message('공정번호·공정명을 입력하고 기호를 선택해 주세요. 입력 순서대로 흐름도를 표시합니다.');});
};
el('flowSearch').onclick=()=>task(list);el('flowReset').onclick=()=>{el('flowKeyword').value='';task(list);};
el('flowKeyword').onkeydown=e=>{if(e.key==='Enter')task(list);};
root.addEventListener('click',e=>{
 const b=e.target.closest('[data-flow-id]');if(b&&!busy&&abandon())task(()=>select(Number(b.dataset.flowId)));
 const op=e.target.closest('[data-step-op]');if(!op||!editable()||busy)return;
 const i=Number(op.dataset.index);
 if(!Number.isInteger(i)||!steps[i])return;
 if(op.dataset.stepOp==='remove'){if(steps[i].id&&!confirm('이 문서에서 공정 행을 삭제할까요? 기존 이력은 보존됩니다.'))return;steps.splice(i,1);activeStep=steps[Math.min(i,steps.length-1)]||null;}
 else{const j=i+(op.dataset.stepOp==='up'?-1:1);if(j<0||j>=steps.length)return;activeStep=steps[i];[steps[i],steps[j]]=[steps[j],steps[i]];}
 dirty=true;renderSteps();revealActiveStep();
});
el('flowSteps').oninput=e=>{
 const field=e.target.dataset.field;if(!field||field==='symbol_code'||!textEditable()||busy)return;
 steps[Number(e.target.dataset.index)][field]=e.target.value;e.target.removeAttribute('aria-invalid');dirty=true;diagram();controls();
};
el('flowSteps').onchange=e=>{
 if(e.target.dataset.field!=='symbol_code'||!editable()||busy)return;
 const step=steps[Number(e.target.dataset.index)],symbol=symbols.find(x=>x.code===e.target.value);
 step.symbol_code=symbol?.code||'';step.symbol_name=symbol?.name||'';step.symbol_shape=symbol?.shape||'';
 dirty=true;renderSteps();
};
root.querySelector('.flow-fields').oninput=event=>{if(event.target.id!=='flowItemKeyword'&&textEditable()){dirty=true;diagram();controls();}};
async function searchItems(){
 const keyword=el('flowItemKeyword').value.trim();
 if(!keyword){el('flowItemResults').hidden=true;message('조회할 품번을 입력해 주세요.',true);return;}
 items=await request('/api/process-flows/options');
 const key=keyword.toLocaleLowerCase(),matches=items.filter(item=>item.is_active==='Y'&&item.part_no.toLocaleLowerCase().includes(key));
 el('flowItemResults').hidden=false;
 el('flowItemResults').innerHTML='<div class="flow-item-result-head"><span>완제품 조회 결과</span><button type="button" id="flowItemResultsClose" class="flow-btn light">닫기</button></div>'+(matches.length?'<table><thead><tr><th>품번</th><th>품명</th><th>선택</th></tr></thead><tbody>'+matches.map(item=>'<tr><td>'+esc(item.part_no)+'</td><td>'+esc(item.part_name)+'</td><td><button type="button" class="flow-btn primary" data-pick-flow-item="'+item.id+'">선택</button></td></tr>').join('')+'</tbody></table>':'<p>일치하는 사용 중인 완제품이 없습니다.</p>');
}
el('flowItemSearch').onclick=()=>{if(!selected&&editable()&&!busy)task(searchItems);};
el('flowItemKeyword').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();if(!selected&&editable()&&!busy)task(searchItems);}};
el('flowItemKeyword').oninput=()=>{
 el('flowItemResults').hidden=true;
 const item=items.find(x=>x.id===Number(el('flowItem').value));
 if(!selected&&item&&el('flowItemKeyword').value.trim()!==item.part_no){
  el('flowItem').value='';el('flowItemDisplay').textContent='선택된 품목 없음';dirty=true;diagram();controls();
 }
};
el('flowItemResults').onclick=event=>{
 if(event.target.closest('#flowItemResultsClose')){el('flowItemResults').hidden=true;return;}
 const button=event.target.closest('[data-pick-flow-item]');if(!button||selected||!editable()||busy)return;
 const item=items.find(x=>x.id===Number(button.dataset.pickFlowItem)&&x.is_active==='Y');if(!item)return;
 el('flowItem').value=item.id;el('flowItemKeyword').value=item.part_no;
 el('flowItemDisplay').textContent=item.part_no+' · '+item.part_name;
 el('flowItemResults').hidden=true;dirty=true;diagram();controls();message('완제품을 선택했습니다.');
};
function validateSteps(requireSymbols=false){
 clearErrors();const seen=new Set();
 for(let i=0;i<steps.length;i++){
  const s=steps[i];
  for(const field of ['step_no','step_name']){
   if(!String(s[field]||'').trim()){message((i+1)+'번째 공정의 '+(field==='step_no'?'공정번호':'공정명')+'를 입력해 주세요. 사용하지 않는 행은 ‘삭제’를 눌러주세요.',true);focusStep(i,field);return false;}
  }
  const key=s.step_no.trim().toLocaleLowerCase();
  if(seen.has(key)){message((i+1)+'번째 공정번호가 중복되었습니다.',true);focusStep(i,'step_no');return false;}seen.add(key);
  if(requireSymbols&&(!s.symbol_code||!shapes[s.symbol_shape]||!symbols.some(x=>x.code===s.symbol_code))){
   message((i+1)+'번째 공정의 사용 중인 기호를 선택해 주세요.',true);focusStep(i,'symbol_code');return false;
  }
 }
 return true;
}
if(el('flowAdd'))el('flowAdd').onclick=()=>{
 if(!editable()||busy)return;
 if(steps.length>=500){message('공정은 최대 500개입니다.',true);return;}
 steps.push({id:null,step_no:'',step_name:'',note:'',symbol_code:'',symbol_name:'',symbol_shape:''});activeStep=steps.at(-1);dirty=true;renderSteps();revealActiveStep();
};
if(el('flowSave'))el('flowSave').onclick=()=>{
 if(!editable()||busy||correcting)return;
 if(!selected){
  const item=items.find(x=>x.id===Number(el('flowItem').value)&&x.is_active==='Y');
  if(!item||el('flowItemKeyword').value.trim()!==item.part_no){message('품번 조회 후 완제품을 선택해 주세요.',true);el('flowItemKeyword').focus();return;}
 }
 if(!selected&&!el('flowCode').value.trim()){message('공정흐름도 개정번호를 입력해 주세요.',true);el('flowCode').focus();return;}
 if(!validateSteps())return;
 task(async()=>{
  const body={note:el('flowNote').value,steps:steps.map(s=>({id:s.id||null,step_no:s.step_no,step_name:s.step_name,note:s.note,symbol_code:s.symbol_code||''}))};
  if(selected)body.version=selected.version;else{body.item_id=Number(el('flowItem').value);body.revision_code=MesRevisionNumber.read(el('flowCode'));body.registrant_user_id=registrantId('flowRegistrant');}
  const result=await request(selected?'/api/process-flows/'+selected.id:'/api/process-flows',selected?'PUT':'POST',body);
  fill(result);await history(result.item_id);await correctionHistory();await list();message('초안을 저장했습니다. 기호 선택과 내용을 확인한 뒤 현재 사용 적용해 주세요.');
 });
};
if(el('flowCorrect'))el('flowCorrect').onclick=()=>{
 if(!correctable()||busy)return;
 if(!correcting){correcting=true;el('flowCorrectionReason').value='';controls();message('기호·공정번호·공정명·비고·순서와 공정 추가/삭제를 수정할 수 있습니다. 개정번호는 유지되며 수정 사유와 변경 이력을 남깁니다.');return;}
 const reason=el('flowCorrectionReason').value.trim();
 if(!reason){message('수정 사유를 입력해 주세요.',true);el('flowCorrectionReason').focus();return;}
 if(!dirty){message('수정된 내용이 없습니다.',true);return;}
 if(!steps.length){message('공정을 1개 이상 등록해 주세요.',true);return;}
 if(!validateSteps(true))return;
 if(!confirm('개정번호를 유지하고 수정 내용을 저장할까요? 기존 FMEA 공정 정보는 보존되며 변경 전·후 이력이 기록됩니다.'))return;
 task(async()=>{
  const result=await request('/api/process-flows/'+selected.id+'/edit','POST',{
   version:selected.version,reason,note:el('flowNote').value,
   steps:steps.map(s=>({id:s.id||null,step_no:s.step_no,step_name:s.step_name,symbol_code:s.symbol_code||'',note:s.note||''}))
  });
  fill(result);await history(result.item_id);await correctionHistory();await list();message('수정했습니다. 개정번호와 기존 FMEA 공정 정보는 유지됩니다.');
 });
};
if(el('flowCorrectCancel'))el('flowCorrectCancel').onclick=()=>{
 if(busy||!selected||!abandon())return;task(()=>select(selected.id));
};
if(el('flowActivate'))el('flowActivate').onclick=()=>{
 if(dirty){message('초안을 먼저 저장해 주세요.',true);return;}
 if(!validateSteps(true))return;
 if(!confirm('현재 사용 공정흐름도로 적용할까요? 기존 FMEA는 자동 변경하지 않고 기존 기준을 보존합니다.'))return;
 task(async()=>{const x=await request('/api/process-flows/'+selected.id+'/activate','POST',{version:selected.version});fill(x);await history(x.item_id);await correctionHistory();await list();message('현재 사용으로 적용했습니다. 기존 FMEA는 새 흐름도와 일치 여부를 검토해 개정해 주세요.');});
};
if(el('flowRevise'))el('flowRevise').onclick=()=>{
 if(busy||!selected)return;
 if(!abandon())return;
 revisionSource={id:selected.id,version:selected.version};
 el('flowRevisionForm').reset();el('flowRevisionError').textContent='';
 setRegistrant('flowRevisionRegistrant','flowRevisionRegistrantName');
 el('flowRevisionDialog').showModal();el('flowNewRevisionCode').focus();
};
if(el('flowRevisionCancel'))el('flowRevisionCancel').onclick=()=>{if(!busy)el('flowRevisionDialog').close();};
if(el('flowRevisionDialog'))el('flowRevisionDialog').addEventListener('cancel',event=>{if(busy)event.preventDefault();});
if(el('flowRevisionForm'))el('flowRevisionForm').onsubmit=event=>{
 event.preventDefault();if(busy||!revisionSource)return;
 task(async()=>{
  el('flowRevisionSubmit').disabled=true;el('flowRevisionCancel').disabled=true;el('flowRevisionError').textContent='';
  try{
   const body={version:revisionSource.version,revision_code:MesRevisionNumber.read(el('flowNewRevisionCode')),
    change_reason:el('flowRevisionReason').value.trim(),registrant_user_id:registrantId('flowRevisionRegistrant')};
   const x=await request('/api/process-flows/'+revisionSource.id+'/revise','POST',body);
   el('flowRevisionDialog').close();fill(x);await history(x.item_id);await correctionHistory();await list();
   message('기존 공정의 고유 연결과 기호를 보존한 새 초안을 만들었습니다.');
  }catch(error){el('flowRevisionError').textContent=error.message;throw error;}
  finally{el('flowRevisionSubmit').disabled=false;el('flowRevisionCancel').disabled=false;}
 });
};
if(el('flowRetire'))el('flowRetire').onclick=()=>{
 if(dirty){message('초안을 저장하거나 다시 조회한 뒤 폐기해 주세요.',true);return;}
 const reason=prompt('폐기 사유. 기존 공정과 연결 문서 이력은 보존됩니다.');if(!reason?.trim())return;
 task(async()=>{const x=await request('/api/process-flows/'+selected.id+'/retire','POST',{version:selected.version,reason:reason.trim()});fill(x);await history(x.item_id);await correctionHistory();await list();message('폐기 처리했습니다. 구버전을 자동 적용하지 않습니다.');});
};
el('flowPrint').onclick=()=>{if(selected&&!dirty&&!correcting){printSummary();window.print();}};
function resizeWorkspace(){
 const workspace=root.querySelector('.flow-workspace'),width=workspace.clientWidth;
 if(!width)return;
 const sideBySide=window.innerWidth>1200,gap=16;
 const originalLeft=sideBySide?(width-gap)*.7:width;
 // 기존 칸의 폭(패딩/테두리 포함)을 기준으로 비고 절반을 40:60으로 재배분합니다.
 const originalTable=Math.max(880,originalLeft),originalNote=Math.max(160,originalTable-713);
 const freed=originalNote/2;
 workspace.style.setProperty('--flow-left-width',(originalLeft-freed*.6)+'px');
 workspace.style.setProperty('--flow-symbol-width',(255+freed*.4)+'px');
 workspace.style.setProperty('--flow-note-width',(originalNote/2)+'px');
 workspace.style.setProperty('--flow-table-min',(originalTable-freed*.6)+'px');
}
const layoutObserver=new ResizeObserver(resizeWorkspace);layoutObserver.observe(root.querySelector('.flow-workspace'));
window.addEventListener('resize',resizeWorkspace);
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
task(async()=>{
 [items,symbols,registrants]=await Promise.all([request('/api/process-flows/options'),request('/api/process-flows/symbols'),write?request('/api/process-flows/registrants'):Promise.resolve(registrants)]);
 setRegistrant('flowRegistrant','flowRegistrantName');
 el('flowItem').value='';
 ready=true;await list();message('품목별 공정흐름도를 선택하거나 신규 등록해 주세요.');
});
})();
