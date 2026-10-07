(() => {
'use strict';
const root = document.getElementById('fmeaApp');
if (!root) return;
const el = id => document.getElementById(id);
const canWrite = root.dataset.canWrite === 'true';
const labels = {DRAFT:'초안',CURRENT:'현재 사용',SUPERSEDED:'구버전',RETIRED:'폐기'};
let importedFlowVersion = null;
let options = {items:[]}, selected = null, rows = [], dirty = false, busy = false, loaded = false, currentFlow = null, flowChoices = [];
const textFields = ['function_text','failure_mode','effects','classification','causes','prevention_controls','detection_controls','recommended_actions','responsibility','actions_taken','note'];
const scores = ['severity','occurrence','detection','new_severity','new_occurrence','new_detection'];
const dateFields = ['target_date','completion_date'];
const esc = value => String(value ?? '').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function message(text, error=false) {el('fmeaMessage').textContent=text;el('fmeaMessage').className='pf-message '+(error?'error':'success');}
function importMessage(text, error=false){
 message(text,error);
 const status=el('fmeaImportMessage');status.hidden=false;status.textContent=text;
 status.className='pf-message '+(error?'error':'success');
}
async function request(url, method='GET', body) {
  const response=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  return MesResponse.read(response);
}
function editable(){return canWrite&&loaded&&(!selected||selected.status==='DRAFT')&&(!selected||selected.item_selectable===true);}
function updateControls(){
  const write=editable()&&!busy;
  root.querySelectorAll('.pf-fields input,.pf-fields select,.pf-fields textarea,#fmeaRows input,#fmeaRows textarea,#fmeaRows select').forEach(x=>x.disabled=!write);
  el('fmeaItemKeyword').disabled=!!selected||!write;el('fmeaItemSearch').disabled=!!selected||!write;
  root.querySelectorAll('[data-pick-item]').forEach(x=>x.disabled=!!selected||!write);
  el('fmeaItem').disabled=!!selected||!write;el('fmeaNumber').disabled=!!selected||!write;el('fmeaCode').disabled=!!selected||!write;
  ['fmeaAddRow','fmeaSave'].forEach(id=>{if(el(id))el(id).disabled=!write;});
  if(el('fmeaImport'))el('fmeaImport').disabled=!canWrite||!loaded||busy;
  if(el('fmeaImportFile'))el('fmeaImportFile').disabled=!write;
  if(el('fmeaActivate'))el('fmeaActivate').disabled=!selected||!write;
  if(el('fmeaRevise'))el('fmeaRevise').disabled=busy||!canWrite||!selected||selected.item_selectable!==true||!['CURRENT','SUPERSEDED'].includes(selected.status);
  if(el('fmeaRetire'))el('fmeaRetire').disabled=busy||!canWrite||!selected||selected.status==='RETIRED';
  if(el('fmeaNew'))el('fmeaNew').disabled=busy||!loaded;
  el('fmeaRevisionSelect').disabled=busy||!selected;
  root.querySelectorAll('[data-remove-row],[data-add-flow-row]').forEach(x=>x.disabled=!write);
  root.querySelectorAll('[data-select-document],[data-history-revision]').forEach(x=>x.disabled=busy);
  ['fmeaSearch','fmeaReset'].forEach(id=>el(id).disabled=busy||!loaded);
  const print=el('fmeaPrint');print.removeAttribute('href');print.setAttribute('aria-disabled','true');
  if(selected&&!dirty&&!busy){print.href='/api/process-fmea/revisions/'+selected.id+'/print';print.setAttribute('aria-disabled','false');}
}
function markDirty(){dirty=true;updateControls();}
function abandon(){return !dirty||confirm('저장하지 않은 입력이 있습니다. 입력을 버리고 이동할까요?');}
function newRow(step=null){const row={id:null,process_code:null,flow_step_id:step?.id||null,flow_step_key_id:step?.step_key_id||null,flow_step_no:step?.step_no||'',flow_step_name:step?.step_name||'',action_not_applicable:false};textFields.forEach(f=>row[f]='');scores.concat(dateFields).forEach(f=>row[f]=null);return row;}
function rpn(row,prefix=''){const values=['severity','occurrence','detection'].map(f=>row[prefix+f]);return values.every(v=>Number.isInteger(v)&&v>=1&&v<=10)?values.reduce((a,b)=>a*b,1):'';}
function cell(field,row,index,type='text'){
 const attrs=' data-field="'+field+'" data-row="'+index+'" aria-label="'+esc(field)+'"';
 const isAction=['recommended_actions','actions_taken','new_severity','new_occurrence','new_detection'].includes(field);
 if(row.action_not_applicable&&isAction)return '<td><div class="pf-cell"><output>N/A</output></div></td>';
 if(type==='score')return '<td><div class="pf-cell"><select'+attrs+'><option value="">미평가</option>'+Array.from({length:10},(_,i)=>'<option value="'+(i+1)+'"'+(row[field]===i+1?' selected':'')+'>'+(i+1)+'</option>').join('')+'</select></div></td>';
 if(type==='date')return '<input type="date"'+attrs+' value="'+esc(row[field]||'')+'">';
 if(type==='input')return '<input'+attrs+' class="pf-center" value="'+esc(row[field])+'" maxlength="'+(field==='classification'?50:100)+'">';
 return '<td><div class="pf-cell"><textarea'+attrs+' maxlength="4000">'+esc(row[field])+'</textarea></div></td>';
}
function balanceRows(){
 el('fmeaRows').querySelectorAll('tr[data-analysis-row]').forEach(tr=>{
  let height=88;
  tr.querySelectorAll('textarea').forEach(area=>{area.style.height='0px';height=Math.max(height,area.scrollHeight+(area.closest('.pf-stack')?30:6));area.style.height='';});
  tr.style.setProperty('--analysis-height',Math.min(height,360)+'px');
 });
}
function renderRows(){
 const stages=currentFlow?.steps||[],html=[];
 const stageCell=(step,count)=>'<td rowspan="'+count+'" class="pf-stage-cell"><div class="pf-stage-layout"><div class="pf-stage-text"><strong>'+esc(step.step_no)+'</strong><span>'+esc(step.step_name)+'</span></div>'+ (canWrite?'<button type="button" class="pf-btn light no-print pf-stage-add" data-add-flow-row="'+step.id+'">분석행 추가</button>':'')+'</div></td>';
 const render=(row,i,firstCell)=>{
  const na=row.action_not_applicable;
  const actionCheck='<label class="pf-na-check"><input type="checkbox" data-field="action_not_applicable" data-row="'+i+'"'+(na?' checked':'')+'>조치 해당없음</label>';
  html.push('<tr data-analysis-row="'+i+'">'+firstCell+cell('function_text',row,i)+cell('failure_mode',row,i)+cell('effects',row,i)+cell('severity',row,i,'score')+'<td><div class="pf-cell">'+cell('classification',row,i,'input')+'</div></td>'+cell('causes',row,i)+cell('occurrence',row,i,'score')+cell('prevention_controls',row,i)+cell('detection_controls',row,i)+cell('detection',row,i,'score')+'<td><div class="pf-cell"><output data-rpn="'+i+'">'+rpn(row)+'</output></div></td>'+cell('recommended_actions',row,i)+'<td><div class="pf-cell pf-stack">'+actionCheck+(na?'<output>N/A</output>':cell('responsibility',row,i,'input')+cell('target_date',row,i,'date'))+'</div></td>'+(na?'<td><div class="pf-cell"><output>N/A</output></div></td>':'<td><div class="pf-cell pf-stack"><textarea data-field="actions_taken" data-row="'+i+'" aria-label="조치 내용" maxlength="4000">'+esc(row.actions_taken)+'</textarea>'+cell('completion_date',row,i,'date')+'</div></td>')+cell('new_severity',row,i,'score')+cell('new_occurrence',row,i,'score')+cell('new_detection',row,i,'score')+'<td><div class="pf-cell"><output data-new-rpn="'+i+'">'+(na?'N/A':rpn(row,'new_'))+'</output></div></td>'+cell('note',row,i)+'<td class="no-print"><div class="pf-cell"><button class="pf-btn light" data-remove-row="'+i+'"'+(!canWrite?' hidden':'')+'>제외</button></div></td></tr>');
 };
 stages.forEach(step=>{
  const entries=rows.map((row,index)=>({row,index})).filter(x=>x.row.flow_step_id===step.id);
  if(!entries.length)html.push('<tr>'+stageCell(step,1)+'<td colspan="20" class="pf-stage-cell">이 공정의 분석행을 추가해 주세요.</td></tr>');
  else entries.forEach((x,j)=>render(x.row,x.index,j===0?stageCell(step,entries.length):''));
 });
 rows.forEach((row,i)=>{
  if(stages.some(step=>step.id===row.flow_step_id))return;
  const legacy=[row.flow_step_no||row.process_code_snapshot,row.flow_step_name||row.process_name_snapshot].filter(Boolean).join(' · ');
  const choices='<option value="">공정 연결 선택</option>'+stages.map(step=>'<option value="'+step.id+'">'+esc(step.step_no+' · '+step.step_name)+'</option>').join('');
  render(row,i,'<td class="pf-stage-cell"><strong>미연결</strong><span>'+esc(legacy||'기존 공정 정보 없음')+'</span>'+(canWrite?'<select data-field="flow_step_id" data-row="'+i+'" aria-label="기존 행의 공정 연결">'+choices+'</select>':'')+'</td>');
 });
 el('fmeaRows').innerHTML=html.join('');updateControls();requestAnimationFrame(balanceRows);
}
async function flowOptions(itemId,detail=null){
 flowChoices=itemId?await request('/api/process-fmea/items/'+itemId+'/flows'):[];
 if(detail?.flow&&!flowChoices.some(x=>x.id===detail.flow.id))flowChoices.push(detail.flow);
 el('fmeaFlow').innerHTML='<option value="">공정흐름도 선택</option>'+flowChoices.map(x=>'<option value="'+x.id+'">'+esc(x.revision_code+' · '+labels[x.status])+'</option>').join('');
 el('fmeaFlow').value=detail?.flow_revision_id||'';
}
function flowWarning(){
 if(selected&&!selected.item_selectable){el('fmeaFlowWarning').textContent='완제품 선택 대상이 아닙니다. 기존 이력은 조회 전용이며 폐기만 가능합니다.';return;}
 el('fmeaFlowWarning').textContent=!currentFlow?'공정흐름도를 먼저 등록·적용한 뒤 선택해 주세요. 기존 분석행은 공정번호를 추정해 연결하지 않습니다.':selected&&selected.status!=='DRAFT'&&selected.flow_current_match===false?'기준 공정흐름도 내용이 수정되었습니다. 이 FMEA는 당시 공정번호·명칭·기호·순서를 보존합니다. 새 개정에서 최신 공정과의 일치 여부를 검토해 주세요.':currentFlow.status!=='CURRENT'?'현재 사용 공정흐름도와 다릅니다. 기존 문서는 보존하며 새 개정에서 기준 공정을 검토해 주세요.':'기준 공정흐름도 '+currentFlow.revision_code+' · 공정번호·공정명·순서 일치';
}
async function historyAndChanges(detail,revisions){
 el('fmeaHistory').innerHTML=revisions.map(r=>'<tr><td>'+esc(r.revision_code)+'</td><td>'+esc(r.created_at)+'<br>'+esc(r.activated_at||'미적용')+'</td><td>'+esc(r.change_reason||(r.previous_revision_id?'미기록':'최초 작성'))+'</td><td>'+esc(r.prepared_by)+' / '+esc(r.created_by)+'</td><td>미구현</td><td>미구현</td><td>'+esc(labels[r.status])+'</td><td><button class="pf-btn light" data-history-revision="'+r.id+'">조회</button></td></tr>').join('');
 const difference=await request('/api/process-fmea/revisions/'+detail.id+'/changes');
 el('fmeaDiffWarning').textContent=difference.warning|| (difference.previous_revision?'비교 기준: '+difference.previous_revision:'최초 작성');
 el('fmeaDiffRows').innerHTML=difference.changes.length?difference.changes.map(x=>'<tr><td>'+esc(x.kind)+'</td><td>'+esc(x.process)+'</td><td>'+esc(x.field)+'</td><td>'+esc(x.before)+'</td><td>'+esc(x.after)+'</td></tr>').join(''):'<tr><td colspan="5">확인 가능한 변경 내역이 없습니다.</td></tr>';
}
async function basisOptions(itemId, value=null, snapshot=''){
  const revisions=itemId?await request('/api/process-fmea/items/'+itemId+'/drawing-revisions'):[];
  el('fmeaBasis').innerHTML='<option value="">연결하지 않음</option>'+revisions.map(r=>'<option value="'+r.id+'">'+esc(r.revision_code+' · '+labels[r.status])+'</option>').join('');
  if(value&&!revisions.some(r=>r.id===value))el('fmeaBasis').insertAdjacentHTML('beforeend','<option value="'+Number(value)+'">'+esc(snapshot||'기준 도면')+' · 폐기/연결 확인 필요</option>');
  el('fmeaBasis').value=value||'';
}
async function list(){
  const result=await request('/api/process-fmea/documents?keyword='+encodeURIComponent(el('fmeaKeyword').value)+'&status='+encodeURIComponent(el('fmeaState').value));
  el('fmeaList').innerHTML=result.length?result.map(x=>'<tr><td>'+esc(x.part_no)+'</td><td>'+esc(x.part_name)+'</td><td>'+esc(x.document_no)+'</td><td>'+esc(x.revision_code)+'</td><td>'+esc(labels[x.status])+'</td><td>'+esc(x.current_revision||'없음')+'</td><td>'+esc(x.prepared_by)+'</td><td>'+esc(x.created_at)+'</td><td><button class="pf-btn light" data-select-document="'+x.id+'" data-revision-id="'+x.revision_id+'">선택</button></td></tr>').join(''):'<tr><td colspan="9">등록된 공정 FMEA가 없습니다.</td></tr>';
  updateControls();
}
function fill(detail){
  el('fmeaImportMessage').hidden=true;
  el('fmeaItemKeyword').value=detail.part_no;
  el('fmeaItemDisplay').textContent=detail.part_no+' · '+detail.part_name+(detail.item_selectable?'':' · 기존 이력 조회 전용');
  el('fmeaItemResults').hidden=true;el('fmeaItemResults').innerHTML='';
  importedFlowVersion=null;selected=detail;rows=detail.rows;currentFlow=detail.flow||null;dirty=false;el('fmeaEditor').hidden=false;
  el('fmeaEditorTitle').textContent=detail.part_no+' · '+detail.part_name+' · 공정 FMEA';
  el('fmeaItem').value=detail.item_id;el('fmeaNumber').value=detail.document_no;MesRevisionNumber.setInput(el('fmeaCode'),detail.revision_code);
  const fields={fmeaCompany:'company',fmeaModelYear:'model_year',fmeaTeam:'team',fmeaAuthor:'prepared_by',fmeaDate:'date_prepared',fmeaNote:'note'};
  Object.entries(fields).forEach(([id,field])=>el(id).value=detail[field]||'');
  el('fmeaOwner').value=detail.process_owner||'';el('fmeaDue').value=detail.completion_due_date||'';el('fmeaMassDate').value=detail.mass_production_date||'';el('fmeaVehicle').value=detail.vehicle_model_snapshot||'';
  el('fmeaFlow').value=detail.flow_revision_id||'';
  flowWarning();
  el('fmeaStatus').textContent=labels[detail.status];
  el('fmeaHistoryNote').textContent='당시 품번/품명: '+detail.part_no_snapshot+' · '+detail.part_name_snapshot+' | 등록: '+detail.created_by+' · '+detail.created_at+(detail.activated_at?' | 적용: '+detail.activated_by+' · '+detail.activated_at:'');
  el('fmeaChangeReason').textContent=(detail.change_reason?'개정 사유: '+detail.change_reason:'')+(detail.retire_reason?' | 폐기 사유: '+detail.retire_reason:'');
  renderRows();
}
async function selectRevision(revisionId){
  const detail=await request('/api/process-fmea/revisions/'+revisionId);
  const revisions=await request('/api/process-fmea/documents/'+detail.document_id+'/revisions');
  await basisOptions(detail.item_id,detail.basis_item_revision_id,detail.basis_revision_snapshot);
  await flowOptions(detail.item_id,detail);
  fill(detail);
  el('fmeaRevisionSelect').innerHTML=revisions.map(r=>'<option value="'+r.id+'">'+esc(r.revision_code+' · '+labels[r.status])+'</option>').join('');
  el('fmeaRevisionSelect').value=detail.id;
  await historyAndChanges(detail,revisions);
}
async function startNew(){
  el('fmeaImportMessage').hidden=true;
  el('fmeaItemKeyword').value='';el('fmeaItemDisplay').textContent='선택된 품목 없음';
  el('fmeaItemResults').hidden=true;el('fmeaItemResults').innerHTML='';
  importedFlowVersion=null;selected=null;rows=[];currentFlow=null;flowChoices=[];dirty=false;el('fmeaEditor').hidden=false;el('fmeaEditorTitle').textContent='신규 공정 FMEA';
  ['fmeaNumber','fmeaCompany','fmeaModelYear','fmeaNote'].forEach(id=>el(id).value='');MesRevisionNumber.setInput(el('fmeaCode'),'');
  el('fmeaItem').value='';el('fmeaTeam').value=root.dataset.team;el('fmeaAuthor').value=root.dataset.author;el('fmeaDate').value=root.dataset.today;
  el('fmeaStatus').textContent='신규 · 초안';el('fmeaRevisionSelect').innerHTML='';el('fmeaHistoryNote').textContent='';el('fmeaChangeReason').textContent='';
  ['fmeaOwner','fmeaDue','fmeaMassDate','fmeaVehicle'].forEach(id=>el(id).value='');
  el('fmeaHistory').innerHTML='';el('fmeaDiffRows').innerHTML='';el('fmeaDiffWarning').textContent='';el('fmeaChanges').open=false;
  await flowOptions(null);flowWarning();
  await basisOptions(null);renderRows();message('품목과 FMEA 번호·개정번호를 입력한 뒤 초안을 저장해 주세요.');
}
function payload(){
  return {company:el('fmeaCompany').value,model_year:el('fmeaModelYear').value,team:el('fmeaTeam').value,prepared_by:el('fmeaAuthor').value,date_prepared:el('fmeaDate').value,
    basis_item_revision_id:el('fmeaBasis').value?Number(el('fmeaBasis').value):null,note:el('fmeaNote').value,
    flow_revision_id:el('fmeaFlow').value?Number(el('fmeaFlow').value):null,import_flow_version:importedFlowVersion,
    process_owner:el('fmeaOwner').value,completion_due_date:el('fmeaDue').value||null,mass_production_date:el('fmeaMassDate').value||null,
    rows:rows.map(r=>{const data={id:r.id||null,process_code:r.process_code||null,flow_step_id:r.flow_step_id||null,action_not_applicable:!!r.action_not_applicable};textFields.concat(scores,dateFields).forEach(f=>data[f]=r[f]);return data;})};
}
async function task(fn,onError=null){
  if(busy)return;busy=true;updateControls();
  try{await fn();}catch(error){if(onError)onError(error);else message(error.message,true);}finally{busy=false;updateControls();}
}
if(el('fmeaNew'))el('fmeaNew').addEventListener('click',()=>{if(abandon())task(startNew);});
el('fmeaSearch').addEventListener('click',()=>task(list));
el('fmeaReset').addEventListener('click',()=>{el('fmeaKeyword').value='';el('fmeaState').value='';task(list);});
el('fmeaKeyword').addEventListener('keydown',event=>{if(event.key==='Enter')task(list);});
el('fmeaList').addEventListener('click',event=>{const button=event.target.closest('[data-select-document]');if(button&&!busy&&abandon())task(async()=>{await selectRevision(Number(button.dataset.revisionId));message('공정 FMEA를 조회했습니다.');});});
el('fmeaRevisionSelect').addEventListener('change',()=>{const id=Number(el('fmeaRevisionSelect').value);if(!abandon()){el('fmeaRevisionSelect').value=selected.id;return;}task(()=>selectRevision(id));});
root.querySelector('.pf-fields').addEventListener('input',event=>{if(event.target.id!=='fmeaItemKeyword'&&editable())markDirty();});
async function searchItems(){
 const keyword=el('fmeaItemKeyword').value.trim();
 if(!keyword){el('fmeaItemResults').hidden=true;message('조회할 품번을 입력해 주세요.',true);return;}
 options=await request('/api/process-fmea/options');
 const key=keyword.toLocaleLowerCase(),matches=options.items.filter(item=>item.is_active==='Y'&&item.part_no.toLocaleLowerCase().includes(key));
 el('fmeaItemResults').hidden=false;
 el('fmeaItemResults').innerHTML='<div class="pf-item-result-head"><span>완제품 조회 결과</span><button type="button" id="fmeaItemResultsClose" class="pf-btn light">닫기</button></div>'+ (matches.length?'<table><thead><tr><th>품번</th><th>품명</th><th>선택</th></tr></thead><tbody>'+matches.map(item=>'<tr><td>'+esc(item.part_no)+'</td><td>'+esc(item.part_name)+'</td><td><button type="button" class="pf-btn primary" data-pick-item="'+item.id+'">선택</button></td></tr>').join('')+'</tbody></table>':'<p>일치하는 사용 중인 완제품이 없습니다.</p>');
}
el('fmeaItemSearch').addEventListener('click',()=>{if(!selected&&editable()&&!busy)task(searchItems);});
el('fmeaItemKeyword').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();if(!selected&&editable()&&!busy)task(searchItems);}});
el('fmeaItemKeyword').addEventListener('input',()=>{el('fmeaItemResults').hidden=true;});
el('fmeaItemResults').addEventListener('click',event=>{
 if(event.target.closest('#fmeaItemResultsClose')){el('fmeaItemResults').hidden=true;return;}
 const button=event.target.closest('[data-pick-item]');if(!button||selected||!editable()||busy)return;
 const item=options.items.find(x=>x.id===Number(button.dataset.pickItem)&&x.is_active==='Y');if(!item)return;
 const changed=Number(el('fmeaItem').value)!==item.id;
 if(changed&&rows.length&&!confirm('품목을 변경하면 아직 저장하지 않은 분석행과 공정 선택을 초기화합니다. 변경할까요?'))return;
 task(async()=>{
  await basisOptions(item.id);await flowOptions(item.id);
  el('fmeaItem').value=item.id;el('fmeaItemKeyword').value=item.part_no;
  el('fmeaItemDisplay').textContent=item.part_no+' · '+item.part_name;
  el('fmeaItemResults').hidden=true;el('fmeaVehicle').value=item.vehicle_model||'';
  currentFlow=null;importedFlowVersion=null;if(changed)rows=[];
  flowWarning();renderRows();markDirty();message('완제품을 선택했습니다. 기준 공정흐름도를 선택해 주세요.');
 });
});
el('fmeaFlow').addEventListener('change',()=>{
 if(!editable()||busy)return;
 importedFlowVersion=null;
 const next=flowChoices.find(x=>x.id===Number(el('fmeaFlow').value))||null;
 if(!next){currentFlow=null;flowWarning();renderRows();markDirty();return;}
 rows.forEach(row=>{
  const step=next.steps.find(x=>x.step_key_id===row.flow_step_key_id);
  if(step){row.flow_step_id=step.id;row.flow_step_no=step.step_no;row.flow_step_name=step.step_name;}
 });
 next.steps.forEach(step=>{if(!rows.some(row=>row.flow_step_id===step.id))rows.push(newRow(step));});
 currentFlow=next;flowWarning();renderRows();markDirty();
});
el('fmeaHistory').addEventListener('click',event=>{
 const button=event.target.closest('[data-history-revision]');
 if(button&&!busy&&abandon())task(()=>selectRevision(Number(button.dataset.historyRevision)));
});
el('fmeaRows').addEventListener('input',event=>{
  const field=event.target.dataset.field,index=Number(event.target.dataset.row);if(!field||field==='action_not_applicable'||field==='flow_step_id'||!editable()||busy)return;
  rows[index][field]=scores.includes(field)?(event.target.value?Number(event.target.value):null):dateFields.includes(field)?(event.target.value||null):event.target.value;
  el('fmeaRows').querySelector('[data-rpn="'+index+'"]').textContent=rpn(rows[index]);
  el('fmeaRows').querySelector('[data-new-rpn="'+index+'"]').textContent=rows[index].action_not_applicable?'N/A':rpn(rows[index],'new_');markDirty();requestAnimationFrame(balanceRows);
});
el('fmeaRows').addEventListener('change',event=>{
 if(!editable()||busy)return;
 const field=event.target.dataset.field,index=Number(event.target.dataset.row);
 if(field==='flow_step_id'){
  const step=currentFlow?.steps.find(x=>x.id===Number(event.target.value));if(!step)return;
  Object.assign(rows[index],{flow_step_id:step.id,flow_step_key_id:step.step_key_id,flow_step_no:step.step_no,flow_step_name:step.step_name});
  markDirty();renderRows();
 }else if(field==='action_not_applicable'){
  const row=rows[index],checked=event.target.checked;
  const actionFields=['recommended_actions','responsibility','target_date','actions_taken','completion_date','new_severity','new_occurrence','new_detection'];
  if(checked&&actionFields.some(f=>row[f]!==null&&row[f]!==''&&row[f]!==undefined)){
   if(!confirm('조치 해당없음으로 바꾸면 이 초안의 조치 입력을 비웁니다. 기존 개정 이력은 보존됩니다. 계속할까요?')){event.target.checked=false;return;}
  }
  row.action_not_applicable=checked;
  if(checked)actionFields.forEach(f=>row[f]=scores.includes(f)||dateFields.includes(f)?null:'');
  markDirty();renderRows();
 }
});
el('fmeaRows').addEventListener('click',event=>{
  const add=event.target.closest('[data-add-flow-row]');
  if(add&&editable()&&!busy){if(rows.length>=500){message('분석행은 최대 500개입니다.',true);return;}const step=currentFlow.steps.find(x=>x.id===Number(add.dataset.addFlowRow));rows.push(newRow(step));markDirty();renderRows();return;}
  const button=event.target.closest('[data-remove-row]');if(!button||!editable()||busy)return;
  const index=Number(button.dataset.removeRow);
  if(rows[index].id&&!confirm('이 초안에서 행을 제외할까요? 저장된 행은 이력으로 보존됩니다.'))return;
  rows.splice(index,1);dirty=true;renderRows();
});
if(el('fmeaAddRow'))el('fmeaAddRow').addEventListener('click',()=>{if(rows.length>=500){message('분석행은 최대 500개입니다.',true);return;}rows.push(newRow(currentFlow?.steps[0]||null));dirty=true;renderRows();});
if(el('fmeaImport'))el('fmeaImport').addEventListener('click',()=>{
 if(!canWrite||!loaded||busy)return;
 if(selected&&selected.status!=='DRAFT'){importMessage('엑셀 분석행은 초안에서만 불러올 수 있습니다. 개정 등록으로 초안을 만든 뒤 불러와 주세요.',true);return;}
 if(!editable()){importMessage('사용 중인 완제품의 FMEA 초안에서 불러와 주세요. 현재 문서는 조회 전용입니다.',true);return;}
 if(!Number(el('fmeaItem').value)){importMessage('품번 조회 후 완제품을 먼저 선택해 주세요.',true);return;}
 if(!currentFlow||currentFlow.status!=='CURRENT'){importMessage('기준 공정흐름도에서 현재 사용 개정을 먼저 선택해 주세요. 목록에 없으면 공정흐름도를 등록하고 현재 사용으로 적용해야 합니다.',true);return;}
 importMessage('파일을 선택해 주세요. 갑지의 분석행만 검증하여 불러옵니다.');
 el('fmeaImportFile').click();
});
if(el('fmeaImportFile'))el('fmeaImportFile').addEventListener('change',()=>{
 const file=el('fmeaImportFile').files[0];el('fmeaImportFile').value='';
 if(!file||!editable()||busy)return;
 if(!file.name.toLowerCase().endsWith('.xlsx')||file.size>10*1024*1024){importMessage('10MB 이하의 .xlsx 파일을 선택해 주세요.',true);return;}
 const itemId=Number(el('fmeaItem').value);
 if(!itemId||!currentFlow||currentFlow.status!=='CURRENT'){importMessage('완제품과 현재 사용 공정흐름도를 먼저 선택해 주세요.',true);return;}
 if(rows.length&&!confirm('검증에 성공하면 현재 초안의 분석행을 엑셀 내용으로 교체합니다. 불러올까요?'))return;
 task(async()=>{
  importMessage('엑셀 분석행을 검증하고 있습니다.');
  const form=new FormData();form.append('file',file);form.append('item_id',itemId);
  form.append('flow_revision_id',currentFlow.id);form.append('flow_version',currentFlow.version);
  if(selected){form.append('revision_id',selected.id);form.append('revision_version',selected.version);}
  const response=await fetch('/api/process-fmea/import-excel',{method:'POST',body:form});
  const data=await MesResponse.read(response);
  const imported=data.rows.map(row=>{
   const step=currentFlow.steps.find(x=>x.id===row.flow_step_id);
   if(!step)throw Error('공정 연결이 변경되었습니다. 다시 조회해 주세요. 기존 분석행은 변경되지 않았습니다.');
   return {...newRow(step),...row};
  });
  rows=imported;importedFlowVersion=data.flow_version;dirty=true;renderRows();
  importMessage('갑지 분석행 '+data.row_count+'개를 불러왔습니다. 기본정보는 유지했습니다. 내용을 확인한 뒤 초안 저장해 주세요.');
 },error=>importMessage(error.message,true));
});
if(el('fmeaSave'))el('fmeaSave').addEventListener('click',()=>task(async()=>{
  const body=payload();let result;
  if(selected){body.version=selected.version;result=await request('/api/process-fmea/revisions/'+selected.id,'PUT',body);}
  else{body.item_id=Number(el('fmeaItem').value);if(!body.item_id||el('fmeaItemKeyword').value.trim()!==options.items.find(x=>x.id===body.item_id)?.part_no){message('품번 조회 후 완제품을 선택해 주세요.',true);return;}body.document_no=el('fmeaNumber').value;body.revision_code=MesRevisionNumber.read(el('fmeaCode'));result=await request('/api/process-fmea/documents','POST',body);}
  // 저장 성공 직후 서버 값을 반영하여 후속 목록 조회 실패가 중복 저장을 만들지 않게 합니다.
  fill(result);await selectRevision(result.id);await list();message('초안을 저장했습니다. 확인 후 현재 사용 적용해 주세요.');
}));
if(el('fmeaActivate'))el('fmeaActivate').addEventListener('click',()=>{
  if(dirty){message('입력을 먼저 초안 저장한 뒤 적용해 주세요.',true);return;}
  if(!confirm('이 개정을 현재 사용으로 적용할까요? 적용 후 수정은 개정 등록으로만 가능합니다.'))return;
  task(async()=>{const result=await request('/api/process-fmea/revisions/'+selected.id+'/activate','POST',{version:selected.version});fill(result);await selectRevision(result.id);await list();message('현재 사용으로 적용했습니다. 이전 현재 사용본은 구버전으로 보존됩니다.');});
});
if(el('fmeaRevise'))el('fmeaRevise').addEventListener('click',async()=>{
  if(busy||!selected)return;
  const code=await MesRevisionNumber.ask('공정 FMEA 개정 등록');if(!code)return;
  const reason=prompt('개정 사유를 입력해 주세요.');if(!reason?.trim())return;
  task(async()=>{const result=await request('/api/process-fmea/revisions/'+selected.id+'/revise','POST',{version:selected.version,revision_code:MesRevisionNumber.code(code),change_reason:reason.trim()});fill(result);await selectRevision(result.id);await list();message('기존 분석표를 복사한 새 초안을 만들었습니다. 내용을 수정하고 저장해 주세요.');});
});
if(el('fmeaRetire'))el('fmeaRetire').addEventListener('click',()=>{
  if(dirty){message('수정 중인 입력을 저장하거나 다시 조회한 뒤 폐기해 주세요.',true);return;}
  const reason=prompt('폐기 사유를 입력해 주세요. 기존 분석표와 이력은 보존됩니다.');if(!reason?.trim())return;
  task(async()=>{const result=await request('/api/process-fmea/revisions/'+selected.id+'/retire','POST',{version:selected.version,reason:reason.trim()});fill(result);await selectRevision(result.id);await list();message('개정을 폐기 처리했습니다. 기존 이력은 보존되며 구버전을 자동 적용하지 않습니다.');});
});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
task(async()=>{
  options=await request('/api/process-fmea/options');
  el('fmeaItem').value='';
  loaded=true;await list();message('공정흐름도를 먼저 적용한 뒤 FMEA를 작성하세요. 기존 FMEA는 그대로 조회할 수 있습니다.');
});
})();
