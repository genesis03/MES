(() => {
 const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),fmt=v=>v==null?'—':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:6});
 const write=window.materialUsageCanWrite,base='/api/inventory/material-usage';
 const now=new Date(),today=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
 const quantities=['issued_qty','used_qty','scrap_qty','remaining_qty'];
 let current=null,dirty=false,busy=false,page=1,total=0,records=[],requestKey='',listVersion=0;
 $('muStart').value=today.slice(0,8)+'01';$('muEnd').value=today;
 async function api(path,options={}){const r=await fetch(base+path,{...options,headers:{'Content-Type':'application/json','X-MES-Menu-Path':'/inventory/material-usage'}});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'입력값을 확인하세요. 수량은 0 이상, 소수점 최대 6자리로 입력하세요.');return d;}
 function params(){return new URLSearchParams({start:$('muStart').value,end:$('muEnd').value,part_no:$('muFindPart').value.trim(),worker:$('muFindWorker').value.trim(),flag:$('muFlag').value,include_cancelled:$('muCancelled').checked,page,size:50});}
 function editable(){return write&&!current?.cancelled;}
 function changed(){dirty=true;$('muEditMessage').textContent='수정 중 · 저장 전';}
 function checkLeave(){return !dirty||confirm('저장하지 않은 작성 내용이 있습니다. 버리고 다른 기록을 열까요?');}
 function lineValues(){return [...$('muLines').querySelectorAll('tr')].map(tr=>Object.fromEntries([...tr.querySelectorAll('[data-f]')].map(input=>[input.dataset.f,quantities.includes(input.dataset.f)?(input.value===''?null:input.value):input.value.trim()]))).filter(row=>row.part_no||row.lot_no||row.note||quantities.some(key=>row[key]!=null));}
 function renderLines(lines){
  $('muLines').innerHTML=lines.map((row,index)=>`<tr>${['part_no','lot_no','unit',...quantities,'note'].map(key=>`<td><input data-f="${key}" ${quantities.includes(key)?'type="number" min="0" step="0.000001"':'maxlength="'+(key==='note'?1000:100)+'"'} value="${esc(row[key]??(key==='unit'?'EA':''))}" aria-label="${index+1}행 ${key}" ${editable()?'':'readonly'}></td>`).join('')}<td><button type="button" data-remove="${index}" class="mu-btn alt" ${editable()?'':'hidden'}>삭제</button></td></tr>`).join('');
  // Keep the worksheet's balance column between remaining and note.
  [...$('muLines').querySelectorAll('tr')].forEach(tr=>{const cell=document.createElement('td');cell.dataset.balance='';tr.insertBefore(cell,tr.children[7]);});
  $('muLines').querySelectorAll('input').forEach(input=>input.addEventListener('input',()=>{changed();balances();}));
  $('muLines').querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{button.closest('tr').remove();changed();});balances();
 }
 function balances(){for(const tr of $('muLines').querySelectorAll('tr')){const values=['issued_qty','used_qty','scrap_qty','remaining_qty'].map(key=>tr.querySelector(`[data-f="${key}"]`).value);const complete=values.every(v=>v!=='');const diff=complete?Number(values[0])-Number(values[1])-Number(values[2])-Number(values[3]):null;tr.querySelector('[data-balance]').textContent=diff==null?'—':fmt(Number(diff.toFixed(6)));tr.querySelector('[data-balance]').className=diff!=null&&Math.abs(diff)>0.0000001?'mu-bad':'';}}
 function tags(flags){return flags.map(flag=>`<span class="mu-tag">${esc(flag)}</span>`).join('');}
 function fill(record){
  current=record;dirty=false;$('muEditor').hidden=false;$('muEditorTitle').textContent=record?`자재 사용 기록${record.cancelled?' · 취소됨':''}`:'새 수기 작성';
  for(const [id,key] of [['muDate','work_date'],['muWorker','worker'],['muPart','part_no'],['muProductionLot','production_lot'],['muProductionQty','production_qty'],['muEditNote','note']]){$(id).value=record?.[key]??(id==='muDate'?today:'');$(id).readOnly=!editable();}
  renderLines(record?.materials?.length?record.materials:[{}]);
  if(write){$('muSave').hidden=!editable();$('muAdd').hidden=!editable();}
  $('muHistoryButton').hidden=!record;$('muEditMessage').textContent=record?`저장자: ${record.updated_by} · ${record.updated_at}`:'';
  $('muSummary').innerHTML=record?(record.summary.length?'<table class="mu-table"><thead><tr><th>자재</th><th>단위</th><th>총 생산수량</th><th>투입 합계</th><th>사용 합계</th><th>차이</th><th>소모율</th></tr></thead><tbody>'+record.summary.map(g=>`<tr><td>${esc(g.part_no)}</td><td>${esc(g.unit)}</td><td>${fmt(g.production_qty)}</td><td>${fmt(g.issued_qty)}</td><td>${fmt(g.used_qty)}</td><td>${fmt(g.difference)}</td><td>${fmt(g.usage_rate)}${g.usage_rate==null?'':'%'}</td></tr>`).join('')+'</tbody></table>':'자재 기록 없음'):'';
  $('muHistory').replaceChildren();
 }
 async function load(){
  const version=++listVersion;$('muListMessage').textContent='조회 중';
  try{const data=await api('?'+params());if(version!==listVersion)return;records=data.items;total=data.total;
   $('muRows').innerHTML=records.map((r,index)=>{const n=Math.max(1,r.materials.length);return (r.materials.length?r.materials:[null]).map((m,i)=>`<tr>${i===0?`<td rowspan="${n}">${esc(r.work_date)}</td><td rowspan="${n}">${esc(r.worker)}</td><td rowspan="${n}" class="mu-left">${esc(r.part_no)}<br>${esc(r.production_lot)}</td><td rowspan="${n}">${fmt(r.production_qty)}</td>`:''}<td class="mu-left">${esc(m?.part_no)}<br>${esc(m?.lot_no)}</td><td>${esc(m?.unit)}</td>${quantities.map(key=>`<td>${fmt(m?.[key])}</td>`).join('')}<td class="${m?.balance_difference!=null&&Number(m.balance_difference)!==0?'mu-bad':''}">${fmt(m?.balance_difference)}</td><td class="mu-left">${esc(m?.balance_description)}</td>${i===0?`<td rowspan="${n}">${r.cancelled?'취소됨<br>':''}${r.summary.map(g=>`<div title="${esc(g.part_no)}">${fmt(g.difference)}</div>`).join('')}${r.summary.length?'':'—'}</td><td rowspan="${n}" class="mu-left">${esc(r.note)}</td><td rowspan="${n}"><button type="button" class="mu-btn alt" data-open="${index}">열기</button>${write&&!r.cancelled?` <button type="button" class="mu-btn alt" data-cancel="${index}">취소</button>`:''}</td>`:''}</tr>`).join('');}).join('')||'<tr><td colspan="15">작성 기록이 없습니다.</td></tr>';
   $('muListMessage').textContent=`${total}건`;$('muPage').textContent=`${page} / ${Math.max(1,Math.ceil(total/50))}`;$('muPrev').disabled=page===1;$('muNext').disabled=page*50>=total;
   document.querySelectorAll('[data-open]').forEach(el=>el.onclick=()=>{if(!checkLeave())return;fill(records[Number(el.dataset.open)]);$('muEditor').scrollIntoView({block:'start'});});
   document.querySelectorAll('[data-cancel]').forEach(el=>el.onclick=async()=>{if(busy||!checkLeave())return;const r=records[Number(el.dataset.cancel)],reason=prompt('기록 취소 사유를 입력하세요. 실제 재고는 바뀌지 않습니다.');if(!reason?.trim())return;busy=true;el.disabled=true;try{await api(`/${r.id}/cancel`,{method:'POST',body:JSON.stringify({version:r.version,reason})});if(current?.id===r.id){dirty=false;fill(await api('/'+r.id));}await load();}catch(e){$('muListMessage').textContent=e.message;}finally{busy=false;el.disabled=false;}});
  }catch(e){if(version===listVersion)$('muListMessage').textContent=e.message;}
 }
 if(write){
  $('muNew').onclick=()=>{if(!checkLeave())return;requestKey=Date.now().toString(36)+Math.random().toString(36).slice(2);fill(null);};
  $('muAdd').onclick=()=>{const values=[...$('muLines').querySelectorAll('tr')].map(tr=>Object.fromEntries([...tr.querySelectorAll('[data-f]')].map(el=>[el.dataset.f,el.value])));values.push({});renderLines(values);changed();};
  $('muForm').addEventListener('input',changed);
 }
 $('muForm').onsubmit=async event=>{
  event.preventDefault();if(!editable()||busy)return;
  const materials=lineValues();if(materials.some(m=>!m.part_no||!m.unit)){$('muEditMessage').textContent='입력한 자재 행의 품번과 단위를 작성하세요.';return;}
  const data={work_date:$('muDate').value,worker:$('muWorker').value,part_no:$('muPart').value,production_lot:$('muProductionLot').value,production_qty:$('muProductionQty').value,note:$('muEditNote').value,materials:materials.map(m=>{const {balance_difference,...value}=m;return value;})};
  if(current){const reason=prompt('수정 사유를 입력하세요.');if(!reason?.trim())return;data.reason=reason;data.version=current.version;}else data.request_key=requestKey;
  busy=true;$('muSave').disabled=true;
  try{const record=await api(current?'/'+current.id:'',{method:current?'PUT':'POST',body:JSON.stringify(data)});fill(record);$('muEditMessage').textContent='저장 완료 · 실제 재고는 변경되지 않았습니다.';await load();}
  catch(e){$('muEditMessage').textContent=e.message+' 작성 내용은 유지됩니다.';}
  finally{busy=false;$('muSave').disabled=false;}
 };
 $('muHistoryButton').onclick=async()=>{if(!current)return;try{const rows=await api(`/${current.id}/history`);$('muHistory').innerHTML='<h3>수정 이력</h3>'+rows.map(r=>`<details><summary>${esc(r.at)} · ${esc(r.actor)} · ${{CREATE:'신규',EDIT:'수정',CANCEL:'취소'}[r.action]} · ${esc(r.reason)}</summary>${['before','after'].map(key=>{const d=r[key];return `<p>${key==='before'?'이전':'변경 후'}: ${d.work_date?esc(d.work_date+' / '+d.worker+' / '+d.part_no)+' · '+fmt(d.production_qty)+'개 · 자재 '+d.materials.length+'행':d.cancelled?'취소됨':'없음'}</p>`+(d.materials?.length?'<table class="mu-table"><tr><th>자재품번/LOT</th><th>투입</th><th>사용</th><th>폐기</th><th>잔여</th><th>비고</th></tr>'+d.materials.map(m=>`<tr><td>${esc(m.part_no)} / ${esc(m.lot_no)}</td>${quantities.map(k=>`<td>${fmt(m[k])}</td>`).join('')}<td>${esc(m.note)}</td></tr>`).join('')+'</table>':'');}).join('')}</details>`).join('');}catch(e){$('muEditMessage').textContent=e.message;}};
 $('muSearch').onclick=()=>{page=1;load();};$('muPrev').onclick=()=>{page--;load();};$('muNext').onclick=()=>{page++;load();};$('muExport').onclick=()=>location.href=base+'/export?'+params();
 window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
 if(write)$('muNew').click();load();
})();
