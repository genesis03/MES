(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let currentOrder = null;
  let selectedOrderId = null;
  let currentOutbound = null;
  let orderRows = [];

  const today = () => {
    const d = new Date();
    return [d.getFullYear(), String(d.getMonth()+1).padStart(2,'0'), String(d.getDate()).padStart(2,'0')].join('-');
  };
  const fmt = v => Number(v || 0).toLocaleString(undefined, {maximumFractionDigits:6});
  const esc = v => { const e=document.createElement('span'); e.textContent=v??''; return e.innerHTML; };
  const msg = text => { $('ob-message').textContent = text || ''; };

  async function request(url, options) {
    const r = await fetch(url, options);
    let data = {};
    try { data = await r.json(); } catch (_) {}
    if (!r.ok) throw new Error(data.detail || '처리하지 못했습니다.');
    return data;
  }

  function resetView() {
    currentOrder = null; selectedOrderId = null; currentOutbound = null;
    $('ob-number').value = '출고 시 자동 발번'; $('ob-date').value = today(); $('ob-status').value = '발주를 불러오세요';
    $('ob-order-no').value=''; $('ob-partner').value=''; $('ob-process').value=''; $('ob-location').value=''; $('ob-manager').value=''; $('ob-order-date').value='';
    $('ob-items').innerHTML='<tr><td colspan="10">외주가공 발주를 불러오세요.</td></tr>';
    $('ob-confirm').disabled=true; $('ob-cancel').disabled=true; $('ob-slip').disabled=true; $('ob-label').disabled=true;
    $('ob-date').disabled=false; msg('');
  }

  function renderItems(items) {
    if (!items?.length) { $('ob-items').innerHTML='<tr><td colspan="10">품목이 없습니다.</td></tr>'; return; }
    $('ob-items').innerHTML = items.map((x,i) => {
      const lotTotal = (x.lots||[]).reduce((s,l)=>s+Number(l.outbound_qty ?? l.allocated_qty ?? l.lot_qty ?? 0),0);
      return `<tr><td>${i+1}</td><td>${esc(x.order_part_no)}</td><td class="left">${esc(x.order_part_name)}</td><td>${esc(x.previous_part_no)}</td><td class="left">${esc(x.spec)}</td><td>${esc(x.unit)}</td><td>${fmt(x.outbound_qty)}</td><td>${Number(x.lot_count||0)}</td><td>${fmt(lotTotal)}</td><td class="left">${esc(x.note)}</td></tr>`;
    }).join('');
  }

  function applyOrder(data) {
    currentOrder = data;
    currentOutbound = data.outbound || null;
    $('ob-order-no').value = data.order_no || '';
    $('ob-partner').value = data.partner_name || '';
    $('ob-process').value = data.processing_type_name || '';
    $('ob-location').value = data.external_storage_location || '';
    $('ob-manager').value = data.manager_name || '';
    $('ob-order-date').value = data.order_date || '';

    if (currentOutbound) {
      $('ob-number').value = currentOutbound.outbound_no;
      $('ob-date').value = currentOutbound.outbound_date;
      $('ob-status').value = currentOutbound.status_name || '출고완료';
      $('ob-date').disabled = true;
      $('ob-confirm').disabled = true;
      $('ob-cancel').disabled = currentOutbound.status !== 'OUTBOUND';
      $('ob-slip').disabled = false; $('ob-label').disabled = false;
      renderItems(currentOutbound.items);
    } else {
      $('ob-number').value = '출고 시 자동 발번';
      $('ob-date').value = today();
      $('ob-status').value = '출고대기';
      $('ob-date').disabled = false;
      $('ob-confirm').disabled = false;
      $('ob-cancel').disabled = true;
      $('ob-slip').disabled = true; $('ob-label').disabled = true;
      renderItems(data.items);
    }
  }

  async function loadOrder(orderId) {
    msg('발주를 불러오는 중...');
    try {
      const data = await request(`/api/subcontract/outbound/order/${orderId}`);
      applyOrder(data); msg(`${data.order_no}을 불러왔습니다.`);
    } catch (e) { msg(e.message); }
  }

  async function loadOrderList() {
    $('ob-orders').innerHTML='<tr><td colspan="8">조회 중...</td></tr>';
    const p = new URLSearchParams(); const q=$('ob-search').value.trim(); if(q)p.set('keyword',q);
    try {
      const data=await request('/api/subcontract/outbound/orders?'+p.toString()); orderRows=data.items||[]; selectedOrderId=null; $('ob-modal-apply').disabled=true;
      if(!orderRows.length){$('ob-orders').innerHTML='<tr><td colspan="8">조회된 외주가공 발주가 없습니다.</td></tr>';return;}
      $('ob-orders').innerHTML=orderRows.map((x,i)=>`<tr class="ob-order-row" data-id="${Number(x.order_id)}"><td>${i+1}</td><td>${esc(x.order_no)}</td><td>${esc(x.order_date)}</td><td class="left">${esc(x.partner_name)}</td><td>${esc(x.processing_type_name)}</td><td>${Number(x.item_count||0)}</td><td>${fmt(x.total_qty)}</td><td class="${x.outbound_status==='OUTBOUND'?'st-done':'st-wait'}">${esc(x.outbound_status_name)}</td></tr>`).join('');
      document.querySelectorAll('.ob-order-row').forEach(tr=>tr.addEventListener('click',()=>{document.querySelectorAll('.ob-order-row').forEach(x=>x.classList.remove('selected'));tr.classList.add('selected');selectedOrderId=Number(tr.dataset.id);$('ob-modal-apply').disabled=false;}));
    } catch(e){$('ob-orders').innerHTML=`<tr><td colspan="8" style="color:#dc2626">${esc(e.message)}</td></tr>`;}
  }

  function openModal(){ $('ob-modal').hidden=false; $('ob-search').value=''; loadOrderList(); setTimeout(()=>$('ob-search').focus(),0); }
  function closeModal(){ $('ob-modal').hidden=true; }

  async function createOutbound(){
    if(!currentOrder) return;
    const outboundDate=$('ob-date').value; if(!outboundDate){msg('출고일자를 입력하세요.');return;}
    if(!confirm(`${currentOrder.order_no}을 ${outboundDate} 출고 처리하시겠습니까?`)) return;
    $('ob-confirm').disabled=true; msg('출고 처리 중...');
    try{
      const data=await request('/api/subcontract/outbound',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({order_id:currentOrder.order_id,outbound_date:outboundDate})});
      currentOutbound=data; $('ob-number').value=data.outbound_no; $('ob-status').value=data.status_name; $('ob-date').disabled=true; $('ob-cancel').disabled=false; $('ob-slip').disabled=false; $('ob-label').disabled=false; renderItems(data.items); msg(`${data.outbound_no} 출고 완료되었습니다.`);
    }catch(e){$('ob-confirm').disabled=false;msg(e.message);}
  }

  async function cancelOutbound(){
    if(!currentOutbound || currentOutbound.status!=='OUTBOUND') return;
    if(!confirm(`${currentOutbound.outbound_no} 출고를 취소하시겠습니까?\n후공정에서 사용된 LOT가 있으면 취소할 수 없습니다.`)) return;
    $('ob-cancel').disabled=true; msg('출고 취소 중...');
    try{
      const data=await request(`/api/subcontract/outbound/${currentOutbound.id}/cancel`,{method:'POST'}); currentOutbound=data; $('ob-status').value=data.status_name; $('ob-date').disabled=true; $('ob-confirm').disabled=false; $('ob-cancel').disabled=true; $('ob-slip').disabled=true; $('ob-label').disabled=true; msg(`${data.outbound_no} 출고를 취소했습니다. 같은 발주를 다시 출고할 수 있습니다.`);
    }catch(e){$('ob-cancel').disabled=false;msg(e.message);}
  }

  function printWindow(title, bodyHtml, css=''){
    const w=window.open('','_blank','width=1000,height=800'); if(!w){alert('팝업 차단을 해제해 주세요.');return;}
    w.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title><style>body{font-family:Arial,sans-serif;padding:20px;color:#111}table{width:100%;border-collapse:collapse;font-size:12px}th,td{border:1px solid #333;padding:6px;text-align:center}th{background:#eee}.left{text-align:left}${css}</style></head><body>${bodyHtml}</body></html>`);w.document.close();w.focus();setTimeout(()=>w.print(),200);
  }

  function printSlip(){
    if(!currentOutbound) return;
    let rows=''; currentOutbound.items.forEach(item=>{(item.lots||[]).forEach(lot=>{rows+=`<tr><td>${esc(item.previous_part_no)}</td><td class="left">${esc(item.order_part_name)}</td><td>${esc(lot.lot_no)}</td><td>${fmt(lot.outbound_qty)}</td><td>${esc(item.unit)}</td></tr>`;});});
    printWindow('외주가공 출고전표',`<h2>외주가공 출고전표</h2><p>출고번호: ${esc(currentOutbound.outbound_no)} &nbsp; 출고일자: ${esc(currentOutbound.outbound_date)}</p><p>발주번호: ${esc(currentOutbound.order_no)} &nbsp; 발주처: ${esc(currentOutbound.partner_name)} &nbsp; 가공유형: ${esc(currentOutbound.processing_type_name)}</p><table><thead><tr><th>출고 품번</th><th>품명</th><th>LOT NO</th><th>수량</th><th>단위</th></tr></thead><tbody>${rows}</tbody></table>`);
  }

  function printLabels(){
    if(!currentOutbound) return;
    let cards=''; currentOutbound.items.forEach(item=>{(item.lots||[]).forEach(lot=>{cards+=`<div class="label"><div><b>외주가공 출고</b></div><div>출고일 ${esc(currentOutbound.outbound_date)}</div><div>품번 ${esc(item.previous_part_no)}</div><div class="lot">${esc(lot.lot_no)}</div><div>수량 ${fmt(lot.outbound_qty)} ${esc(item.unit)}</div><div>${esc(currentOutbound.partner_name)}</div></div>`;});});
    printWindow('외주가공 출고 라벨',cards,'.label{width:80mm;min-height:45mm;border:1px solid #111;padding:5mm;margin:0 0 4mm;box-sizing:border-box;page-break-inside:avoid}.lot{font-size:18px;font-weight:bold;margin:4mm 0}');
  }

  function init(){
    const ids=['ob-load','ob-confirm','ob-cancel','ob-slip','ob-label','ob-message','ob-number','ob-date','ob-status','ob-order-no','ob-partner','ob-process','ob-location','ob-manager','ob-order-date','ob-items','ob-modal','ob-search','ob-search-btn','ob-orders','ob-modal-close','ob-modal-apply'];
    const missing=ids.filter(id=>!$(id)); if(missing.length){console.error('Outbound UI missing',missing);return;}
    $('ob-load').addEventListener('click',openModal); $('ob-search-btn').addEventListener('click',loadOrderList); $('ob-search').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();loadOrderList();}}); $('ob-modal-close').addEventListener('click',closeModal); $('ob-modal-apply').addEventListener('click',()=>{if(!selectedOrderId)return;const id=selectedOrderId;closeModal();loadOrder(id);}); $('ob-modal').addEventListener('click',e=>{if(e.target===$('ob-modal'))closeModal();});
    $('ob-confirm').addEventListener('click',createOutbound); $('ob-cancel').addEventListener('click',cancelOutbound); $('ob-slip').addEventListener('click',printSlip); $('ob-label').addEventListener('click',printLabels); resetView();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
