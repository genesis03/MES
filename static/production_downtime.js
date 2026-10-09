let editingDowntimeId=null,downtimeRunId=null,downtimeBusy=false;
function downtimeEscape(value){return String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function renderDowntimes(){
  const rows=currentRun?.downtimes||[],editable=currentRun?.status==='IN_PROGRESS';
  document.getElementById('downtimeAddBtn').disabled=!editable;
  document.getElementById('downtimeTotal').textContent=rows.length?`(${rows.length}건 / ${rows.reduce((sum,row)=>sum+row.minutes,0)}분)`:'';
  document.getElementById('downtimeRows').innerHTML=rows.map(row=>`<tr><td>${downtimeEscape(row.type_code+'. '+row.type_name)}</td><td>${downtimeEscape(row.started_at)}<br>${downtimeEscape(row.ended_at)}</td><td>${row.minutes}</td><td style="white-space:pre-wrap;min-width:120px;max-width:260px;overflow-wrap:anywhere">${downtimeEscape(row.action)}</td><td>${row.quality_confirmed?'확인':'미확인'}</td><td>${editable?`<div class="row-actions"><button type="button" class="btn secondary" onclick="openDowntime(${row.id})">수정</button><button type="button" class="btn danger" onclick="deleteDowntime(${row.id})">삭제</button></div>`:''}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">등록된 비가동 내역이 없습니다.</td></tr>';
}
function downtimeDuration(){const start=new Date(document.getElementById('downtimeStart').value),end=new Date(document.getElementById('downtimeEnd').value),minutes=(end-start)/60000;document.getElementById('downtimeMinutes').value=Number.isFinite(minutes)&&minutes>0?minutes:'';}
async function openDowntime(id=null){
  if(downtimeBusy||!currentRun||currentRun.status!=='IN_PROGRESS')return;
  if(!(await saveDetails(false)))return;
  downtimeRunId=currentRun.id;editingDowntimeId=id;
  try{
    const types=await jf('/api/production-run/downtime-types');
    if(currentRun?.id!==downtimeRunId)return;
    const row=id?(currentRun.downtimes||[]).find(r=>r.id===id):null;
    if(id&&!row)throw Error('비가동 내역을 다시 조회해 주세요.');
    if(row&&!types.some(t=>t.code===row.type_code))types.push({code:row.type_code,name:row.type_name+' (사용중지)'});
    document.getElementById('downtimeType').innerHTML='<option value="">선택</option>'+types.map(t=>`<option value="${downtimeEscape(t.code)}">${downtimeEscape(t.code+'. '+t.name)}</option>`).join('');
    document.getElementById('downtimeType').value=row?.type_code||'';
    document.getElementById('downtimeStart').value=dtLocal(row?.started_at||currentRun.start_time);
    document.getElementById('downtimeEnd').value=dtLocal(row?.ended_at||currentRun.end_time||'');
    document.getElementById('downtimeAction').value=row?.action||'';
    document.getElementById('downtimeQuality').value=row?.quality_confirmed?'1':'0';
    document.getElementById('downtimeContext').textContent=`${currentRun.part_no} / ${currentRun.equipment_name} / ${currentRun.operator_name}`;
    msg(document.getElementById('downtimeMsg'),'');downtimeDuration();
    document.getElementById('downtimeModal').classList.add('show');
  }catch(e){msg(runMsg,e.message);}
}
async function saveDowntime(){
  if(downtimeBusy)return;
  if(currentRun?.id!==downtimeRunId)return msg(document.getElementById('downtimeMsg'),'선택한 가동 건이 변경되었습니다. 다시 열어 주세요.');
  const payload={type_code:document.getElementById('downtimeType').value,started_at:document.getElementById('downtimeStart').value,ended_at:document.getElementById('downtimeEnd').value,action:document.getElementById('downtimeAction').value,quality_confirmed:document.getElementById('downtimeQuality').value==='1'};
  if(!payload.type_code||!payload.started_at||!payload.ended_at)return msg(document.getElementById('downtimeMsg'),'유형과 시작·종료시간을 입력해 주세요.');
  downtimeBusy=true;document.getElementById('downtimeSaveBtn').disabled=true;
  try{await jf(`/api/production-run/${downtimeRunId}/downtimes${editingDowntimeId?'/'+editingDowntimeId:''}`,{method:editingDowntimeId?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});closeModal('downtimeModal');await selectRun(downtimeRunId);msg(runMsg,'비가동 내역을 저장했습니다.',true);}catch(e){msg(document.getElementById('downtimeMsg'),e.message);}finally{downtimeBusy=false;document.getElementById('downtimeSaveBtn').disabled=false;}
}
async function deleteDowntime(id){if(downtimeBusy||!currentRun||currentRun.status!=='IN_PROGRESS'||!confirm('선택한 비가동 내역을 삭제하시겠습니까?'))return;const runId=currentRun.id;downtimeBusy=true;try{await jf(`/api/production-run/${runId}/downtimes/${id}`,{method:'DELETE'});await selectRun(runId);msg(runMsg,'비가동 내역을 삭제했습니다.',true);}catch(e){msg(runMsg,e.message);}finally{downtimeBusy=false;}}
for(const id of ['downtimeStart','downtimeEnd'])document.getElementById(id).addEventListener('input',downtimeDuration);
