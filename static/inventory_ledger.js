(() => {
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=v=>v==null?'—':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:6});
  const canWrite=window.inventoryLedgerCanWrite;
  let loaded=null,selected=null,details=null,loading=false,saving=false,detailVersion=0;
  const drafts=new Map();
  const today=new Date();const localDay=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  $('ilMonth').value=localDay.slice(0,7);
  async function request(path,options={}){
    const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-MES-Menu-Path':'/inventory/ledger',...options.headers}});
    const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'입력값을 확인하세요.');return data;
  }
  function query(extra={}){return new URLSearchParams({month:loaded.month,source:loaded.source,...extra});}
  function updateDraftStatus(message=''){
    if(!canWrite)return;
    $('ilBulkSave').disabled=saving||drafts.size===0;
    $('ilBulkStatus').textContent=message||`${drafts.size}개 품번 입력 중`;
  }
  function inputCell(row,index,field,label){
    if(!canWrite||!row.linked)return field==='opening_qty'&&row[field]==null?'기초 미입력':fmt(row[field]);
    const value=drafts.get(row.item_id)?.[field]??'';
    return `<input class="il-cell-input ${value!==''?'is-dirty':''}" data-input-row="${index}" data-field="${field}" type="number" min="0" step="0.000001" value="${esc(value)}" aria-label="${esc(row.part_no+' '+label+' 입력')}"><small class="il-saved">저장: ${row[field]==null?'미입력':fmt(row[field])}</small>`;
  }
  function setDraft(row,field,value){
    const draft={...(drafts.get(row.item_id)||{})};
    if(value==='')delete draft[field];else draft[field]=value;
    if(Object.keys(draft).length)drafts.set(row.item_id,draft);else drafts.delete(row.item_id);
  }
  function bindInputs(){
    document.querySelectorAll('.il-cell-input').forEach(input=>{
      input.oninput=()=>{
        const row=loaded.rows[Number(input.dataset.inputRow)];
        // Bad intermediate numeric input must not silently become a blank/no-op.
        const value=input.validity.badInput?'!invalid':input.value;
        setDraft(row,input.dataset.field,value);input.classList.toggle('is-dirty',value!=='');updateDraftStatus();
      };
      input.onkeydown=event=>{
        if(!['Enter','Tab'].includes(event.key))return;
        const all=[...document.querySelectorAll('.il-cell-input')];
        const candidates=event.key==='Enter'?all.filter(el=>el.dataset.field===input.dataset.field):all;
        const next=candidates[candidates.indexOf(input)+(event.shiftKey?-1:1)];
        if(next){event.preventDefault();next.focus();next.select();}else if(event.key==='Enter')event.preventDefault();
      };
      input.onpaste=event=>{
        const text=event.clipboardData.getData('text');if(!text)return;
        const fields=['opening_qty','actual_qty','count_carry_qty'];
        const startField=fields.indexOf(input.dataset.field),startRow=Number(input.dataset.inputRow);
        const rows=text.replace(/\r/g,'').replace(/\n$/,'').split('\n').map(line=>line.split('\t'));
        const changes=[];
        for(let y=0;y<rows.length;y++)for(let x=0;x<rows[y].length;x++){
          const row=loaded.rows[startRow+y],field=fields[startField+x];
          if(!row?.linked||!field){event.preventDefault();updateDraftStatus('미연결 품번이 있거나 붙여넣기 범위가 입력 가능한 행·열을 벗어납니다.');return;}
          const value=rows[y][x].trim().replace(/,/g,'');
          if(value!==''&&!/^\d+(?:\.\d{1,6})?$/.test(value)){event.preventDefault();updateDraftStatus('0 이상의 숫자만 붙여넣으세요. 소수점은 최대 6자리입니다.');return;}
          changes.push({row,field,value});
        }
        event.preventDefault();for(const change of changes)setDraft(change.row,change.field,change.value);
        showRows();document.querySelector(`.il-cell-input[data-input-row="${startRow}"][data-field="${input.dataset.field}"]`)?.focus();updateDraftStatus();
      };
    });
  }
  function showRows(){
    const stageColumns=['production_qty','production_defect_qty','setup_qty','subcontract_in_qty','subcontract_defect_qty','loss_qty','sample_qty','packing_qty','raw_in_qty'];
    const columns=['구분','품번','단위','기초','입고',...stageColumns,'출고','기말(산출)','실재고','이월수량','차이'];
    $('ilHead').classList.add('il-grouped-head');
      $('ilHead').innerHTML=`<tr><th rowspan="3" scope="col">구분</th><th rowspan="3" scope="col">품번</th><th rowspan="3" scope="col">단위</th><th rowspan="3" scope="col">기초</th><th rowspan="3" scope="col">입고</th><th colspan="9" scope="colgroup" aria-label="공정별 내역"></th><th rowspan="3" scope="col">출고</th><th rowspan="3" scope="col">기말(산출)</th><th colspan="2" scope="colgroup">재고실사</th><th rowspan="3" scope="col">차이<br>(실사-산출)</th></tr><tr><th colspan="3" scope="colgroup" class="il-stage">생산</th><th colspan="4" scope="colgroup" class="il-stage">외주가공</th><th rowspan="2" scope="col" class="il-stage">포장</th><th rowspan="2" scope="col" class="il-stage">원재료</th><th rowspan="2" scope="col">실재고</th><th rowspan="2" scope="col">이월수량</th></tr><tr><th scope="col">생산</th><th scope="col">불량</th><th scope="col">Setup</th><th scope="col">입고</th><th scope="col">불량</th><th scope="col">LOSS</th><th scope="col">샘플</th></tr>`;
    $('ilRows').innerHTML=loaded.rows.map((r,i)=>{
      const title=`실사일: ${r.count_date||'미입력'} / 해당 일 장부재고: ${fmt(r.expected_at_count)}`;
      return `<tr data-row="${i}" class="${selected?.key===r.key?'selected':''}"><td>${esc(r.category_name)}</td><td class="il-part" title="${esc(r.part_name)}"><button data-detail="${i}">${esc(r.part_no)}</button>${r.linked?'':'<br>품번 미연결 / '+esc(r.process)}</td><td>${esc(r.unit)}</td><td>${inputCell(r,i,'opening_qty','기초')}</td><td>${fmt(r.in_qty)}</td>${stageColumns.map(k=>r[k]).map(v=>`<td>${fmt(v)}</td>`).join('')}<td>${fmt(r.out_qty)}</td><td class="${r.closing_qty!=null&&Number(r.closing_qty)<0?'il-negative':''}">${fmt(r.closing_qty)}</td><td title="${esc(title)}">${inputCell(r,i,'actual_qty','실재고')}</td><td>${inputCell(r,i,'count_carry_qty','이월수량')}</td><td title="${esc(title)}" class="${r.difference!=null&&Number(r.difference)!==0?'il-negative':''}">${fmt(r.difference)}</td></tr>`;
    }).join('')||`<tr><td colspan="${columns.length}">조회 결과가 없습니다.</td></tr>`;
    document.querySelectorAll('[data-detail]').forEach(el=>el.onclick=()=>openDetail(loaded.rows[Number(el.dataset.detail)]));
    bindInputs();
  }
  async function load(){
    if(loading||saving)return;
    if(drafts.size){
      if(!confirm('저장하지 않은 입력값이 있습니다. 입력값을 버리고 다시 조회할까요?')){
        if(loaded){$('ilMonth').value=loaded.month;$('ilSource').value=loaded.source;$('ilCategory').value=loaded.filters.category;$('ilPart').value=loaded.filters.part_no;}
        return;
      }
      drafts.clear();
    }
    loading=true;$('ilLoad').disabled=true;$('ilStatus').textContent='조회 중';
    ['ilMonth','ilSource','ilCategory','ilPart','ilExport'].forEach(id=>$(id).disabled=true);
    detailVersion++;
    try{
      const q=new URLSearchParams({month:$('ilMonth').value,source:$('ilSource').value,part_no:$('ilPart').value.trim(),category:$('ilCategory').value});
      const contextChanged=loaded&&(loaded.month!==q.get('month')||loaded.source!==q.get('source'));
      loaded=await request('/api/inventory/ledger?'+q);
      if(canWrite&&contextChanged){$('ilCarryOverwrite').checked=false;$('ilBulkNote').value='일괄 입력';}
      loaded.filters={part_no:q.get('part_no'),category:q.get('category')};
      if(canWrite){$('ilBulkDate').min=loaded.first;$('ilBulkDate').max=loaded.last;if(!$('ilBulkDate').value.startsWith(loaded.month))$('ilBulkDate').value='';updateDraftStatus();}
      const kinds=[...new Set(loaded.rows.map(r=>r.category))].sort();const current=$('ilCategory').value;
      for(const kind of kinds)if(![...$('ilCategory').options].some(o=>o.value===kind))$('ilCategory').add(new Option(loaded.rows.find(r=>r.category===kind)?.category_name||kind,kind));
      $('ilCategory').value=current;
      $('ilWarnings').innerHTML=loaded.warnings.map(w=>`<p class="il-warning">${esc(w)}</p>`).join('');
      const key=selected?.key;selected=null;details=null;$('ilDetail').hidden=true;showRows();
      $('ilStatus').textContent=`${loaded.rows.length}개 품번 · 기초 미입력 ${loaded.rows.filter(r=>r.opening_qty==null).length}개`;
      if(key){const row=loaded.rows.find(r=>r.key===key);if(row)await openDetail(row);}
    }catch(e){$('ilStatus').textContent=e.message;loaded=null;$('ilRows').replaceChildren();$('ilDetail').hidden=true;}
    finally{loading=false;$('ilLoad').disabled=false;['ilMonth','ilSource','ilCategory','ilPart','ilExport'].forEach(id=>$(id).disabled=false);}
  }
  async function openDetail(row){
    selected=row;const version=++detailVersion;
    try{
      const result=await request('/api/inventory/ledger/detail?'+query(row.linked?{item_id:row.item_id}:{raw_part:row.part_no,process:row.process}));
      if(version!==detailVersion)return;
      details=result;
      $('ilDetail').hidden=false;$('ilDetailTitle').textContent=`${row.part_no} · ${row.part_name} (${row.unit})`;
      $('ilEdit').hidden=!canWrite||!row.linked;$('ilEntry').hidden=!canWrite||!row.linked;
      $('ilTotals').innerHTML=Object.entries(row.totals).map(([k,v])=>`<span>${esc(k)} ${fmt(v)}</span>`).join('');
      $('ilDays').innerHTML=details.daily.map(d=>`<tr><td><button data-day="${d.date}">${d.date}</button></td><td>${fmt(d.in_qty)}</td><td>${fmt(d.out_qty)}</td><td>${fmt(d.balance)}</td></tr>`).join('');
      document.querySelectorAll('[data-day]').forEach(el=>el.onclick=()=>renderEvents(el.dataset.day));
      $('ilAudit').replaceChildren();renderEvents();document.querySelectorAll('#ilRows tr[data-row]').forEach(tr=>tr.classList.toggle('selected',loaded.rows[Number(tr.dataset.row)].key===selected.key));
    }catch(e){$('ilStatus').textContent=e.message;}
  }
  function renderEvents(day=''){
    $('ilEvidenceTitle').textContent=day?`${day} 상세`:'전체 상세';
    $('ilEvents').innerHTML=details.events.filter(e=>!day||e.date===day).map(e=>`<tr><td>${e.date}</td><td>${esc(e.category)}</td><td>${e.direction==='IN'?'입고':'출고'}</td><td>${fmt(e.qty)}</td><td>${esc(e.document_no)}</td><td>${esc(e.lot_no)}</td><td class="left">${esc(e.note)}</td><td>${canWrite&&e.entry_id?`<button data-void="${e.entry_id}">보완 취소</button>`:''}</td></tr>`).join('')||'<tr><td colspan="8">수불 내역이 없습니다.</td></tr>';
    document.querySelectorAll('[data-void]').forEach(el=>el.onclick=async()=>{
      if(drafts.size){updateDraftStatus('표의 입력 내용을 먼저 저장하세요.');return;}
      const reason=prompt('수불 보완 취소 사유를 입력하세요.');if(!reason?.trim())return;
      el.disabled=true;try{await request(`/api/inventory/ledger/entries/${el.dataset.void}/void`,{method:'POST',body:JSON.stringify({reason})});await load();}catch(e){$('ilStatus').textContent=e.message;el.disabled=false;}
    });
  }
  function openBalance(row){
    if(drafts.size){updateDraftStatus('표의 입력 내용을 먼저 저장하세요.');return;}
    selected=row;detailVersion++;$('ilBalancePart').textContent=`${loaded.month} · ${loaded.source==='mes'?'내부 MES':'외부 연동'} · ${row.part_no} (${row.unit})`;
    $('ilOpening').value=row.opening_qty??'';$('ilActual').value=row.actual_qty??'';$('ilCountCarry').value=row.count_carry_qty??'0';$('ilCountDate').value=row.count_date??'';$('ilNote').value=row.note;
    $('ilCountDate').min=loaded.first;$('ilCountDate').max=loaded.last;$('ilBalanceMessage').textContent='';$('ilBalanceDialog').showModal();
  }
  $('ilBalanceForm').onsubmit=async event=>{
    event.preventDefault();if(!selected||!canWrite)return;$('ilSaveBalance').disabled=true;
    try{await request('/api/inventory/ledger/balance',{method:'PUT',body:JSON.stringify({month:loaded.month,source:loaded.source,item_id:selected.item_id,opening_qty:$('ilOpening').value||null,actual_qty:$('ilActual').value||null,count_carry_qty:$('ilCountCarry').value||'0',count_date:$('ilCountDate').value||null,note:$('ilNote').value,version:selected.version})});$('ilBalanceDialog').close();await load();}
    catch(e){$('ilBalanceMessage').textContent=e.message;}finally{$('ilSaveBalance').disabled=false;}
  };
  $('ilCarry').onclick=async()=>{
    const [y,m]=loaded.month.split('-').map(Number);const prev=new Date(y,m-2,1);const month=`${prev.getFullYear()}-${String(prev.getMonth()+1).padStart(2,'0')}`;
    try{const data=await request('/api/inventory/ledger?'+new URLSearchParams({month,source:loaded.source}));const row=data.rows.find(r=>r.item_id===selected.item_id);
      if(!row)throw new Error('전월 자료가 없습니다.');
      const actualAtEnd=row.actual_qty!=null&&row.count_date===data.last;const value=actualAtEnd?row.actual_total_qty:row.closing_qty;
      if(value==null)throw new Error('전월 기초 또는 월말 실사수량이 없습니다.');
      if(Number(value)<0)throw new Error('전월 계산재고가 음수입니다. 누락된 수불과 실사수량을 확인하세요.');
      if(!confirm(`${month} ${actualAtEnd?'월말 실사수량':'계산재고'} ${fmt(value)}를 기초로 불러올까요?${loaded.source==='external'?' 외부 자료에 누락된 수불이 없는지 확인하세요.':''}`))return;
      $('ilOpening').value=value;$('ilNote').value=`${month} ${actualAtEnd?'월말 실사':'계산재고'} 이월`;
    }catch(e){$('ilBalanceMessage').textContent=e.message;}
  };
  function lockBulk(locked){
    for(const id of ['ilBulkSave','ilCarryAll','ilCarryOverwrite','ilLoad','ilExport','ilMonth','ilSource','ilCategory','ilPart','ilEdit','ilEntry','ilBulkDate','ilBulkNote'])if($(id))$(id).disabled=locked;
    document.querySelectorAll('.il-cell-input').forEach(input=>input.disabled=locked);
    if(!locked)updateDraftStatus();
  }
  if(canWrite){
    $('ilBulkSave').onclick=async()=>{
      if(saving||loading||!loaded||!drafts.size)return;
      for(const [itemId,patch] of drafts)for(const [field,value] of Object.entries(patch)){
        if(!/^(?:\d+(?:\.\d{0,6})?|\.\d{1,6})$/.test(value)){
          updateDraftStatus('0 이상의 숫자를 입력하세요. 소수점은 최대 6자리입니다.');
          document.querySelector(`.il-cell-input[data-input-row="${loaded.rows.findIndex(row=>row.item_id===itemId)}"][data-field="${field}"]`)?.focus();return;
        }
      }
      if(!$('ilBulkDate').checkValidity()){$('ilBulkDate').reportValidity();return;}
      const hasActual=[...drafts.values()].some(patch=>'actual_qty' in patch);
      if(hasActual&&!$('ilBulkDate').value){updateDraftStatus('실재고를 저장할 공통 실사일을 선택하세요.');$('ilBulkDate').focus();return;}
      if(!$('ilBulkNote').value.trim()){updateDraftStatus('공통 변경 사유를 입력하세요.');$('ilBulkNote').focus();return;}
      const rows=[...drafts].map(([itemId,patch])=>({item_id:itemId,version:loaded.rows.find(row=>row.item_id===itemId).version,...patch}));
      saving=true;lockBulk(true);updateDraftStatus('일괄 저장 중');let message='';
      try{
        const result=await request('/api/inventory/ledger/balances',{method:'PUT',body:JSON.stringify({month:loaded.month,source:loaded.source,count_date:$('ilBulkDate').value||null,note:$('ilBulkNote').value,rows})});
        drafts.clear();saving=false;await load();message=`${result.saved}개 품번 저장 완료${loaded?'':' · 조회 버튼으로 다시 확인하세요.'}`;
      }catch(e){message=e.message+' 입력값은 유지됩니다.';}
      finally{saving=false;lockBulk(false);updateDraftStatus(message);}
    };
    $('ilCarryAll').onclick=async()=>{
      if(saving||loading||!loaded)return;
      const [year,month]=loaded.month.split('-').map(Number);const day=new Date(year,month-2,1);
      const previous=`${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}`;
      saving=true;lockBulk(true);let message='';
      try{
        const data=await request('/api/inventory/ledger?'+new URLSearchParams({month:previous,source:loaded.source}));
        const byId=new Map(data.rows.filter(row=>row.linked).map(row=>[row.item_id,row]));
        const plan=[];let kept=0,missing=0;
        for(const row of loaded.rows){
          if(!row.linked){missing++;continue;}
          if(drafts.get(row.item_id)?.opening_qty!==undefined||(!$('ilCarryOverwrite').checked&&row.opening_qty!=null)){kept++;continue;}
          const prev=byId.get(row.item_id);
          const qty=prev?.actual_qty!=null&&prev.count_date===data.last?prev.actual_total_qty:prev?.closing_qty;
          if(qty==null||Number(qty)<0){missing++;continue;}
          plan.push({row,qty});
        }
        if(!plan.length){message=`불러올 기초재고 없음 · 기존/입력값 유지 ${kept}개 · 전월 자료 없음/음수/미연결 ${missing}개`;return;}
        const prompt=`조회된 품번 중 ${plan.length}개에 ${previous} 마감재고를 채울까요? 월말 실사+이월수량을 우선 사용하고, 없으면 계산재고를 사용합니다. 기존/입력값 유지 ${kept}개, 전월 자료 없음·음수·미연결 ${missing}개. 아직 저장하지 않으며 '입력 내용 저장'을 눌러야 저장됩니다.${loaded.source==='external'?' 외부 자료의 누락 수불을 확인하세요.':''}`;
        if(!confirm(prompt))return;
        for(const {row,qty} of plan)setDraft(row,'opening_qty',qty);
        if(!$('ilBulkNote').value.trim()||$('ilBulkNote').value==='일괄 입력')$('ilBulkNote').value=`${previous} 마감재고 일괄 불러오기`;
        showRows();message=`${plan.length}개 품번 불러옴 · 유지 ${kept}개 · 제외 ${missing}개 · 저장 전`;
      }catch(e){message=e.message;}
      finally{saving=false;lockBulk(false);updateDraftStatus(message);}
    };
  }
  window.addEventListener('beforeunload',event=>{if(drafts.size){event.preventDefault();event.returnValue='';}});
  $('ilEntry').onclick=()=>{
    if(drafts.size){updateDraftStatus('표의 입력 내용을 먼저 저장하세요.');return;}
    $('ilEntryForm').reset();$('ilEntryDate').min=loaded.first;$('ilEntryDate').max=loaded.last;$('ilEntryDate').value=localDay.slice(0,7)===loaded.month?localDay:loaded.last;
    $('ilEntryMessage').textContent='';$('ilEntryDialog').showModal();
  };
  $('ilEntryCategory').onchange=()=>{const kind=$('ilEntryCategory').value;if(kind!=='기타')$('ilDirection').value=['구매입고','외주입고'].includes(kind)?'IN':'OUT';};
  $('ilEntryForm').onsubmit=async event=>{
    event.preventDefault();$('ilSaveEntry').disabled=true;
    try{await request('/api/inventory/ledger/entries',{method:'POST',body:JSON.stringify({source:loaded.source,item_id:selected.item_id,entry_date:$('ilEntryDate').value,direction:$('ilDirection').value,category:$('ilEntryCategory').value,qty:$('ilEntryQty').value,document_no:$('ilDocument').value,lot_no:$('ilLot').value,reason:$('ilReason').value})});$('ilEntryDialog').close();await load();}
    catch(e){$('ilEntryMessage').textContent=e.message;}finally{$('ilSaveEntry').disabled=false;}
  };
  $('ilHistory').onclick=async()=>{
    if(!selected?.linked){$('ilAudit').textContent='품번 연결 후 이력을 관리할 수 있습니다.';return;}
    try{const rows=await request('/api/inventory/ledger/history?'+query({item_id:selected.item_id}));
      const fields={opening_qty:'기초재고',actual_qty:'실사수량',count_carry_qty:'실사 이월수량',count_date:'실사일',note:'사유',entry_date:'일자',direction:'입출고',category:'유형',qty:'수량',document_no:'전표',lot_no:'LOT',reason:'사유'};
      const description=v=>Object.entries(fields).filter(([key])=>key in v).map(([key,label])=>`${label}: ${key==='direction'?(v[key]==='IN'?'입고':'출고'):(v[key]??'미입력')}`).join(' · ');
      $('ilAudit').innerHTML='<h3 style="margin:16px 0">수정 이력</h3>'+rows.map(r=>`<div class="il-panel"><strong>${esc(r.at)} · ${esc(r.by)} · ${{BALANCE:'기초·실사 저장',ENTRY:'수불 보완',VOID:'보완 취소'}[r.action]||''}</strong><p class="il-help">이전: ${esc(description(r.before))}<br>변경: ${esc(description(r.after))}</p></div>`).join('')+(rows.length?'':'이력이 없습니다.');
    }catch(e){$('ilStatus').textContent=e.message;}
  };
  document.querySelectorAll('[data-close]').forEach(el=>el.onclick=() => $(el.dataset.close).close());
  $('ilEdit').onclick=()=>openBalance(selected);$('ilAllEvents').onclick=()=>renderEvents();
  for(const id of ['ilMonth','ilSource','ilCategory'])$(id).onchange=()=>{selected=null;load();};
  $('ilLoad').onclick=()=>{selected=null;load();};$('ilPart').onkeydown=e=>{if(e.key==='Enter')$('ilLoad').click();};
  $('ilExport').onclick=()=>{if(loaded)location.href='/api/inventory/ledger/export?'+query(loaded.filters);};
  load();
})();
