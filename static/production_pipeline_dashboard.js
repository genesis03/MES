(() => {
  const $=id=>document.getElementById(id), esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=v=>v==null?'미집계':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:2});
  const quantityKeys=['sales_order_qty','plan_qty','work_order_qty','machining_qty','subcontract_plating_qty','inspection_qty','assembly_qty','vision_inspection_qty','packed_ready_qty','stock_allocated_qty','packed_allocated_qty','new_production_required_qty'];
  // Keep vision calculations/API intact; enable this flag when the column is needed.
  const showVision=false;
  const visibleQuantityKeys=quantityKeys.filter(key=>showVision||key!=='vision_inspection_qty');
  const visionHeader=document.querySelector('[data-pipeline-vision]');
  if(visionHeader)visionHeader.hidden=!showVision;
  $('ppStageHeading').colSpan=showVision?9:8;
  $('ppStageHeading').textContent=showVision?'수주 → 계획 → 지시 → 가공 → 외주도금 → 검사 → 조립 → 비전 → 포장':'수주 → 계획 → 지시 → 가공 → 외주도금 → 검사 → 조립 → 포장';
  document.querySelector('#ppTable col[span]').span=visibleQuantityKeys.length+1;
  function meter(value,maximum,suffix='',tone=''){
    if(value==null||!Number.isFinite(Number(value)))return '<span class="qp-meter-missing">미집계</span>';
    const width=maximum>0?Math.min(100,Math.max(0,Number(value)/maximum*100)):0;
    const label=fmt(value)+suffix;
    return `<span class="qp-meter ${tone}" role="img" aria-label="${esc(label)}" title="${suffix?'100%':fmt(maximum)} 기준"><span class="qp-meter-fill" style="width:${width}%"></span><span class="qp-meter-value">${esc(label)}</span></span>`;
  }
  async function load(){
    $('ppLoad').disabled=true;$('ppMessage').textContent='조회 중입니다.';
    try{const response=await fetch('/api/production/pipeline-data?'+new URLSearchParams({part_no:$('ppPart').value.trim()}));const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'조회에 실패했습니다.');
      $('ppRows').innerHTML=data.items.map(r=>{
        const maximum=Math.max(0,...visibleQuantityKeys.map(k=>Number(r[k])||0));
        return `<tr><td title="${esc(r.part_no)} · ${esc(r.part_name)}">${esc(r.part_no)}<br>${esc(r.part_name)}</td>${visibleQuantityKeys.map(k=>`<td>${meter(r[k],maximum,'',k==='new_production_required_qty'?'qp-meter-needed':'')}</td>`).join('')}<td>${meter(r.shipping_ready_rate,100,'%','qp-meter-ready')}</td></tr>`;
      }).join('')||`<tr><td colspan="${visibleQuantityKeys.length+2}">미출하 수주가 없습니다.</td></tr>`;
      $('ppNotes').replaceChildren(...data.notes.map(note=>{const p=document.createElement('p');p.textContent=note;return p;}));
      $('ppMessage').textContent=`${data.items.length}개 품번 · ${data.orders.length}개 수주 항목`;
    }catch(e){$('ppMessage').textContent=e.message;}finally{$('ppLoad').disabled=false;}
  }
  $('ppLoad').onclick=load;load();
})();
