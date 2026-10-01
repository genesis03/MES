(() => {
'use strict';
const root = document.getElementById('fmeaApp');
if (!root) return;
const el = id => document.getElementById(id);
const canWrite = root.dataset.canWrite === 'true';
const labels = {DRAFT:'초안',CURRENT:'현재 사용',SUPERSEDED:'구버전',RETIRED:'폐기'};
let options = {items:[],processes:[]}, selected = null, rows = [], dirty = false, busy = false, loaded = false;
const textFields = ['function_text','failure_mode','effects','classification','causes','prevention_controls','detection_controls','recommended_actions','responsibility','actions_taken','note'];
const scores = ['severity','occurrence','detection','new_severity','new_occurrence','new_detection'];
const dateFields = ['target_date','completion_date'];
const esc = value => String(value ?? '').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function message(text, error=false) {el('fmeaMessage').textContent=text;el('fmeaMessage').className='pf-message '+(error?'error':'success');}
async function request(url, method='GET', body) {
  const response=await fetch(url,{method,headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
  const data=await response.json();
  if(!response.ok) {const detail=data.detail;throw Error(Array.isArray(detail)?detail.map(x=>x.loc.join('.')+': '+x.msg).join('\n'):detail||'요청 처리에 실패했습니다.');}
  return data;
}
function editable(){return canWrite&&loaded&&(!selected||selected.status==='DRAFT')&&(!selected||selected.item_active==='Y');}
function updateControls(){
  const write=editable()&&!busy;
  root.querySelectorAll('.pf-fields input,.pf-fields select,.pf-fields textarea,#fmeaRows input,#fmeaRows textarea,#fmeaRows select').forEach(x=>x.disabled=!write);
  el('fmeaItem').disabled=!!selected||!write;el('fmeaNumber').disabled=!!selected||!write;el('fmeaCode').disabled=!!selected||!write;
  ['fmeaAddRow','fmeaSave'].forEach(id=>{if(el(id))el(id).disabled=!write;});
  if(el('fmeaActivate'))el('fmeaActivate').disabled=!selected||!write;
  if(el('fmeaRevise'))el('fmeaRevise').disabled=busy||!canWrite||!selected||selected.item_active!=='Y'||!['CURRENT','SUPERSEDED'].includes(selected.status);
  if(el('fmeaRetire'))el('fmeaRetire').disabled=busy||!canWrite||!selected||selected.status==='RETIRED';
  if(el('fmeaNew'))el('fmeaNew').disabled=busy||!loaded;
  el('fmeaRevisionSelect').disabled=busy||!selected;
  root.querySelectorAll('[data-remove-row]').forEach(x=>x.disabled=!write);
  root.querySelectorAll('[data-select-document]').forEach(x=>x.disabled=busy);
  ['fmeaSearch','fmeaReset'].forEach(id=>el(id).disabled=busy||!loaded);
  const print=el('fmeaPrint');print.removeAttribute('href');print.setAttribute('aria-disabled','true');
  if(selected&&!dirty&&!busy){print.href='/api/process-fmea/revisions/'+selected.id+'/print';print.setAttribute('aria-disabled','false');}
}
function markDirty(){dirty=true;updateControls();}
function abandon(){return !dirty||confirm('저장하지 않은 입력이 있습니다. 입력을 버리고 이동할까요?');}
function newRow(){const row={id:null,process_code:null};textFields.forEach(f=>row[f]='');scores.concat(dateFields).forEach(f=>row[f]=null);return row;}
function rpn(row,prefix=''){const values=['severity','occurrence','detection'].map(f=>row[prefix+f]);return values.every(v=>Number.isInteger(v)&&v>=1&&v<=10)?values.reduce((a,b)=>a*b,1):'';}
function cell(field,row,index,type='text'){
  const attrs=' data-field="'+field+'" data-row="'+index+'" aria-label="'+esc(field)+'"';
  if(type==='score')return '<td><select'+attrs+'><option value="">미평가</option>'+Array.from({length:10},(_,i)=>'<option value="'+(i+1)+'"'+(row[field]===i+1?' selected':'')+'>'+(i+1)+'</option>').join('')+'</select></td>';
  if(type==='date')return '<input type="date"'+attrs+' value="'+esc(row[field]||'')+'">';
  if(type==='input')return '<input'+attrs+' value="'+esc(row[field])+'" maxlength="'+(field==='classification'?50:100)+'">';
  return '<td><textarea'+attrs+' maxlength="4000">'+esc(row[field])+'</textarea></td>';
}
function renderRows(){
  el('fmeaRows').innerHTML=rows.map((row,i)=>{
    let processOptions='<option value="">공정 선택</option>'+options.processes.map(p=>'<option value="'+esc(p.code)+'"'+(p.code===row.process_code?' selected':'')+(p.is_active!=='Y'?' disabled':'')+'>'+esc(p.name+' · '+p.code+(p.is_active!=='Y'?' · 사용중지':''))+'</option>').join('');
    if(row.process_code&&!options.processes.some(p=>p.code===row.process_code))processOptions+='<option selected value="'+esc(row.process_code)+'">'+esc(row.process_name_snapshot||row.process_code)+'</option>';
    return '<tr><td><select data-field="process_code" data-row="'+i+'" aria-label="공정">'+processOptions+'</select>'+(selected&&selected.status!=='DRAFT'?'<small>당시: '+esc(row.process_name_snapshot)+' · '+esc(row.process_code_snapshot)+'</small>':'')+'</td>'+
      cell('function_text',row,i)+cell('failure_mode',row,i)+cell('effects',row,i)+cell('severity',row,i,'score')+'<td>'+cell('classification',row,i,'input')+'</td>'+
      cell('causes',row,i)+cell('occurrence',row,i,'score')+cell('prevention_controls',row,i)+cell('detection_controls',row,i)+cell('detection',row,i,'score')+
      '<td><output data-rpn="'+i+'">'+rpn(row)+'</output></td>'+cell('recommended_actions',row,i)+
      '<td>'+cell('responsibility',row,i,'input')+cell('target_date',row,i,'date')+'</td>'+
      '<td><textarea data-field="actions_taken" data-row="'+i+'" aria-label="조치 내용" maxlength="4000">'+esc(row.actions_taken)+'</textarea>'+cell('completion_date',row,i,'date')+'</td>'+
      cell('new_severity',row,i,'score')+cell('new_occurrence',row,i,'score')+cell('new_detection',row,i,'score')+
      '<td><output data-new-rpn="'+i+'">'+rpn(row,'new_')+'</output></td>'+cell('note',row,i)+
      '<td><button class="pf-btn light" data-remove-row="'+i+'"'+(!canWrite?' hidden':'')+'>제외</button></td></tr>';
  }).join('');updateControls();
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
  selected=detail;rows=detail.rows;dirty=false;el('fmeaEditor').hidden=false;
  el('fmeaEditorTitle').textContent=detail.part_no+' · '+detail.part_name+' · 공정 FMEA';
  el('fmeaItem').value=detail.item_id;el('fmeaNumber').value=detail.document_no;el('fmeaCode').value=detail.revision_code;
  const fields={fmeaCompany:'company',fmeaModelYear:'model_year',fmeaTeam:'team',fmeaAuthor:'prepared_by',fmeaDate:'date_prepared',fmeaNote:'note'};
  Object.entries(fields).forEach(([id,field])=>el(id).value=detail[field]||'');
  el('fmeaStatus').textContent=labels[detail.status];
  el('fmeaHistoryNote').textContent='당시 품번/품명: '+detail.part_no_snapshot+' · '+detail.part_name_snapshot+' | 등록: '+detail.created_by+' · '+detail.created_at+(detail.activated_at?' | 적용: '+detail.activated_by+' · '+detail.activated_at:'');
  el('fmeaChangeReason').textContent=(detail.change_reason?'개정 사유: '+detail.change_reason:'')+(detail.retire_reason?' | 폐기 사유: '+detail.retire_reason:'');
  renderRows();
}
async function selectRevision(revisionId){
  const detail=await request('/api/process-fmea/revisions/'+revisionId);
  const revisions=await request('/api/process-fmea/documents/'+detail.document_id+'/revisions');
  await basisOptions(detail.item_id,detail.basis_item_revision_id,detail.basis_revision_snapshot);
  fill(detail);
  el('fmeaRevisionSelect').innerHTML=revisions.map(r=>'<option value="'+r.id+'">'+esc(r.revision_code+' · '+labels[r.status])+'</option>').join('');
  el('fmeaRevisionSelect').value=detail.id;
}
async function startNew(){
  selected=null;rows=[newRow()];dirty=false;el('fmeaEditor').hidden=false;el('fmeaEditorTitle').textContent='신규 공정 FMEA';
  ['fmeaNumber','fmeaCode','fmeaCompany','fmeaModelYear','fmeaNote'].forEach(id=>el(id).value='');
  el('fmeaItem').value='';el('fmeaTeam').value=root.dataset.team;el('fmeaAuthor').value=root.dataset.author;el('fmeaDate').value=root.dataset.today;
  el('fmeaStatus').textContent='신규 · 초안';el('fmeaRevisionSelect').innerHTML='';el('fmeaHistoryNote').textContent='';el('fmeaChangeReason').textContent='';
  await basisOptions(null);renderRows();message('품목과 FMEA 번호·개정번호를 입력한 뒤 초안을 저장해 주세요.');
}
function payload(){
  return {company:el('fmeaCompany').value,model_year:el('fmeaModelYear').value,team:el('fmeaTeam').value,prepared_by:el('fmeaAuthor').value,date_prepared:el('fmeaDate').value,
    basis_item_revision_id:el('fmeaBasis').value?Number(el('fmeaBasis').value):null,note:el('fmeaNote').value,
    rows:rows.map(r=>{const data={id:r.id||null,process_code:r.process_code||null};textFields.concat(scores,dateFields).forEach(f=>data[f]=r[f]);return data;})};
}
async function task(fn){
  if(busy)return;busy=true;updateControls();
  try{await fn();}catch(error){message(error.message,true);}finally{busy=false;updateControls();}
}
if(el('fmeaNew'))el('fmeaNew').addEventListener('click',()=>{if(abandon())task(startNew);});
el('fmeaSearch').addEventListener('click',()=>task(list));
el('fmeaReset').addEventListener('click',()=>{el('fmeaKeyword').value='';el('fmeaState').value='';task(list);});
el('fmeaKeyword').addEventListener('keydown',event=>{if(event.key==='Enter')task(list);});
el('fmeaList').addEventListener('click',event=>{const button=event.target.closest('[data-select-document]');if(button&&!busy&&abandon())task(async()=>{await selectRevision(Number(button.dataset.revisionId));message('공정 FMEA를 조회했습니다.');});});
el('fmeaRevisionSelect').addEventListener('change',()=>{const id=Number(el('fmeaRevisionSelect').value);if(!abandon()){el('fmeaRevisionSelect').value=selected.id;return;}task(()=>selectRevision(id));});
root.querySelector('.pf-fields').addEventListener('input',()=>{if(editable())markDirty();});
el('fmeaItem').addEventListener('change',()=>task(async()=>{await basisOptions(Number(el('fmeaItem').value));markDirty();}));
el('fmeaRows').addEventListener('input',event=>{
  const field=event.target.dataset.field,index=Number(event.target.dataset.row);if(!field||!editable()||busy)return;
  rows[index][field]=scores.includes(field)?(event.target.value?Number(event.target.value):null):dateFields.includes(field)?(event.target.value||null):event.target.value;
  el('fmeaRows').querySelector('[data-rpn="'+index+'"]').textContent=rpn(rows[index]);
  el('fmeaRows').querySelector('[data-new-rpn="'+index+'"]').textContent=rpn(rows[index],'new_');markDirty();
});
el('fmeaRows').addEventListener('click',event=>{
  const button=event.target.closest('[data-remove-row]');if(!button||!editable()||busy)return;
  const index=Number(button.dataset.removeRow);
  if(rows[index].id&&!confirm('이 초안에서 행을 제외할까요? 저장된 행은 이력으로 보존됩니다.'))return;
  rows.splice(index,1);dirty=true;renderRows();
});
if(el('fmeaAddRow'))el('fmeaAddRow').addEventListener('click',()=>{if(rows.length>=500){message('분석행은 최대 500개입니다.',true);return;}rows.push(newRow());dirty=true;renderRows();});
if(el('fmeaSave'))el('fmeaSave').addEventListener('click',()=>task(async()=>{
  const body=payload();let result;
  if(selected){body.version=selected.version;result=await request('/api/process-fmea/revisions/'+selected.id,'PUT',body);}
  else{body.item_id=Number(el('fmeaItem').value);body.document_no=el('fmeaNumber').value;body.revision_code=el('fmeaCode').value;result=await request('/api/process-fmea/documents','POST',body);}
  // 저장 성공 직후 서버 값을 반영하여 후속 목록 조회 실패가 중복 저장을 만들지 않게 합니다.
  fill(result);await selectRevision(result.id);await list();message('초안을 저장했습니다. 확인 후 현재 사용 적용해 주세요.');
}));
if(el('fmeaActivate'))el('fmeaActivate').addEventListener('click',()=>{
  if(dirty){message('입력을 먼저 초안 저장한 뒤 적용해 주세요.',true);return;}
  if(!confirm('이 개정을 현재 사용으로 적용할까요? 적용 후 수정은 개정 등록으로만 가능합니다.'))return;
  task(async()=>{const result=await request('/api/process-fmea/revisions/'+selected.id+'/activate','POST',{version:selected.version});fill(result);await selectRevision(result.id);await list();message('현재 사용으로 적용했습니다. 이전 현재 사용본은 구버전으로 보존됩니다.');});
});
if(el('fmeaRevise'))el('fmeaRevise').addEventListener('click',()=>{
  const code=prompt('새 FMEA 개정번호를 입력해 주세요.');if(!code?.trim())return;
  const reason=prompt('개정 사유를 입력해 주세요.');if(!reason?.trim())return;
  task(async()=>{const result=await request('/api/process-fmea/revisions/'+selected.id+'/revise','POST',{version:selected.version,revision_code:code.trim(),change_reason:reason.trim()});fill(result);await selectRevision(result.id);await list();message('기존 분석표를 복사한 새 초안을 만들었습니다. 내용을 수정하고 저장해 주세요.');});
});
if(el('fmeaRetire'))el('fmeaRetire').addEventListener('click',()=>{
  if(dirty){message('수정 중인 입력을 저장하거나 다시 조회한 뒤 폐기해 주세요.',true);return;}
  const reason=prompt('폐기 사유를 입력해 주세요. 기존 분석표와 이력은 보존됩니다.');if(!reason?.trim())return;
  task(async()=>{const result=await request('/api/process-fmea/revisions/'+selected.id+'/retire','POST',{version:selected.version,reason:reason.trim()});fill(result);await selectRevision(result.id);await list();message('개정을 폐기 처리했습니다. 기존 이력은 보존되며 구버전을 자동 적용하지 않습니다.');});
});
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
task(async()=>{
  options=await request('/api/process-fmea/options');
  el('fmeaItem').innerHTML='<option value="">품목 선택</option>'+options.items.map(x=>'<option value="'+x.id+'"'+(x.is_active!=='Y'?' disabled':'')+'>'+esc(x.part_no+' · '+x.part_name+(x.is_active!=='Y'?' · 사용중지':''))+'</option>').join('');
  loaded=true;await list();message('목록에서 공정 FMEA를 선택하거나 신규 등록하세요.');
});
})();