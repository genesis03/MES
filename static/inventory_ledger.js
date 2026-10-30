(() => {
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=v=>v==null?'—':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:6});
  const canWrite=window.inventoryLedgerCanWrite;
  let loaded=null,selected=null,details=null,types=false,loading=false,detailVersion=0;
  const today=new Date();const localDay=`${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  $('ilMonth').value=localDay.slice(0,7);
  async function request(path,options={}){
    const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-MES-Menu-Path':'/inventory/ledger',...options.headers}});
    const data=await r.json();if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:'입력값을 확인하세요.');return data;
  }
  function query(extra={}){return new URLSearchParams({month:loaded.month,source:loaded.source,...extra});}
  function showRows(){
    const kindColumns=types?[...new Set(loaded.rows.flatMap(r=>Object.keys(r.totals)))].sort():[];
    const stageColumns=['production_qty','production_defect_qty','setup_qty','subcontract_in_qty','subcontract_defect_qty','loss_qty','sample_qty','packing_qty','raw_in_qty'];
    const columns=['구분','품번','단위','기초','입고',...(types?kindColumns:stageColumns),'출고','기말(산출)','실재고','이월수량','차이'];
    $('ilHead').classList.toggle('il-grouped-head',!types);
    if(types){
      $('ilHead').innerHTML='<tr>'+columns.map(c=>`<th>${esc(c)}</th>`).join('')+'</tr>';
    }else{
      $('ilHead').innerHTML=`<tr><th rowspan="3" scope="col">구분</th><th rowspan="3" scope="col">품번</th><th rowspan="3" scope="col">단위</th><th rowspan="3" scope="col">기초</th><th rowspan="3" scope="col">입고</th><th colspan="9" scope="colgroup" aria-label="공정별 내역"></th><th rowspan="3" scope="col">출고</th><th rowspan="3" scope="col">기말(산출)</th><th colspan="2" scope="colgroup">재고실사</th><th rowspan="3" scope="col">차이<br>(실사-산출)</th></tr><tr><th colspan="3" scope="colgroup" class="il-stage">생산</th><th colspan="4" scope="colgroup" class="il-stage">외주가공</th><th rowspan="2" scope="col" class="il-stage">포장</th><th rowspan="2" scope="col" class="il-stage">원재료</th><th rowspan="2" scope="col">실재고</th><th rowspan="2" scope="col">이월수량</th></tr><tr><th scope="col">생산</th><th scope="col">불량</th><th scope="col">Setup</th><th scope="col">입고</th><th scope="col">불량</th><th scope="col">LOSS</th><th scope="col">샘플</th></tr>`;
    }
    $('ilRows').innerHTML=loaded.rows.map((r,i)=>{
      const actual=r.actual_qty==null?'—':fmt(r.actual_qty);
      const title=`실사일: ${r.count_date||'미입력'} / 해당 일 장부재고: ${fmt(r.expected_at_count)}`;
      return `<tr data-row="${i}" class="${selected?.key===r.key?'selected':''}"><td>${esc(r.category_name)}</td><td class="il-part" title="${esc(r.part_name)}"><button data-detail="${i}">${esc(r.part_no)}</button>${r.linked?'':'<br>품번 미연결 / '+esc(r.process)}</td><td>${esc(r.unit)}</td><td>${canWrite&&r.linked?`<button data-edit="${i}" title="기초·실사 입력">${r.opening_qty==null?'입력':fmt(r.opening_qty)}</button>`:(r.opening_qty==null?'기초 미입력':fmt(r.opening_qty))}</td><td>${fmt(r.in_qty)}</td>${(types?kindColumns.map(k=>r.totals[k]??'0'):stageColumns.map(k=>r[k])).map(v=>`<td>${fmt(v)}</td>`).join('')}<td>${fmt(r.out_qty)}</td><td class="${r.closing_qty!=null&&Number(r.closing_qty)<0?'il-negative':''}">${fmt(r.closing_qty)}</td><td title="${esc(title)}">${canWrite&&r.linked?`<button data-edit="${i}" title="기초·실사 입력">${r.actual_qty==null?'입력':actual}</button>`:actual}</td><td>${fmt(r.count_carry_qty)}</td><td title="${esc(title)}" class="${r.difference!=null&&Number(r.difference)!==0?'il-negative':''}">${fmt(r.difference)}</td></tr>`;
    }).join('')||`<tr><td colspan="${columns.length}">조회 결과가 없습니다.</td></tr>`;
    document.querySelectorAll('[data-detail]').forEach(el=>el.onclick=()=>openDetail(loaded.rows[Number(el.dataset.detail)]));
    document.querySelectorAll('[data-edit]').forEach(el=>el.onclick=()=>openBalance(loaded.rows[Number(el.dataset.edit)]));
  }
  async function load(){
    if(loading)return;loading=true;$('ilLoad').disabled=true;$('ilStatus').textContent='조회 중';
    ['ilMonth','ilSource','ilCategory','ilPart','ilExport'].forEach(id=>$(id).disabled=true);
    detailVersion++;
    try{
      const q=new URLSearchParams({month:$('ilMonth').value,source:$('ilSource').value,part_no:$('ilPart').value.trim(),category:$('ilCategory').value});
      loaded=await request('/api/inventory/ledger?'+q);
      loaded.filters={part_no:q.get('part_no'),category:q.get('category')};
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
      $('ilAudit').replaceChildren();renderEvents();showRows();
    }catch(e){$('ilStatus').textContent=e.message;}
  }
  function renderEvents(day=''){
    $('ilEvidenceTitle').textContent=day?`${day} 상세`:'전체 상세';
    $('ilEvents').innerHTML=details.events.filter(e=>!day||e.date===day).map(e=>`<tr><td>${e.date}</td><td>${esc(e.category)}</td><td>${e.direction==='IN'?'입고':'출고'}</td><td>${fmt(e.qty)}</td><td>${esc(e.document_no)}</td><td>${esc(e.lot_no)}</td><td class="left">${esc(e.note)}</td><td>${canWrite&&e.entry_id?`<button data-void="${e.entry_id}">보완 취소</button>`:''}</td></tr>`).join('')||'<tr><td colspan="8">수불 내역이 없습니다.</td></tr>';
    document.querySelectorAll('[data-void]').forEach(el=>el.onclick=async()=>{
      const reason=prompt('수불 보완 취소 사유를 입력하세요.');if(!reason?.trim())return;
      el.disabled=true;try{await request(`/api/inventory/ledger/entries/${el.dataset.void}/void`,{method:'POST',body:JSON.stringify({reason})});await load();}catch(e){$('ilStatus').textContent=e.message;el.disabled=false;}
    });
  }
  function openBalance(row){
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
  $('ilEntry').onclick=()=>{
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
  $('ilView').onclick=()=>{types=!types;$('ilView').textContent=types?'월간 집계 보기':'유형별 수량 보기';if(loaded)showRows();};
  $('ilExport').onclick=()=>{if(loaded)location.href='/api/inventory/ledger/export?'+query(loaded.filters);};
  load();
})();
