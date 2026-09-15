const $ = (id) => document.getElementById(id);

let orders = [];
let selectedOrderIds = [];
let currentOrder = null;
let currentItemId = null;
let viewingShipment = null;
let viewMode = false;
const allocations = new Map();

function today(){ return new Date().toISOString().slice(0,10); }
function num(v){ return Number(v || 0); }
function fmt(v){ return num(v).toLocaleString('ko-KR', {maximumFractionDigits: 3}); }
function esc(v){ return String(v ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[s])); }

async function getJson(url, options={}){
  const res = await fetch(url, options);
  const data = await res.json().catch(()=>({}));
  if(!res.ok) throw new Error(data.detail || data.message || '처리에 실패했습니다.');
  return data;
}

function allocationFor(itemId){
  if(!allocations.has(itemId)) allocations.set(itemId, []);
  return allocations.get(itemId);
}

function buildOrderBundle(orderIds){
  const selected = orderIds.map(id => orders.find(o => o.id === id)).filter(Boolean);
  if(!selected.length) return null;
  const customerId = selected[0].customer_id;
  if(selected.some(o => o.customer_id !== customerId)){
    throw new Error('서로 다른 판매처의 수주는 한 건의 출고전표로 묶을 수 없습니다.');
  }

  const items = selected.flatMap(order => (order.items || []).map(item => ({
    ...item,
    order_id: order.id,
    order_no: order.order_no,
  })));
  const dueDates = selected.map(o => o.delivery_due_date).filter(Boolean).sort();
  const managers = [...new Set(selected.map(o => o.manager_name).filter(Boolean))];
  const statuses = [...new Set(selected.map(o => o.status).filter(Boolean))];
  return {
    id: selected[0].id,
    order_ids: selected.map(o => o.id),
    order_no: selected.map(o => o.order_no).join(', '),
    order_nos: selected.map(o => o.order_no),
    customer_id: customerId,
    customer_name: selected[0].customer_name,
    delivery_due_date: dueDates[0] || '',
    manager_name: managers.join(', '),
    status: selected.length === 1 ? (selected[0].status || '') : `${selected.length}건`,
    statuses,
    items,
  };
}

function applySelectedOrders(orderIds){
  if(viewMode) return;
  const uniqueIds = [...new Set((orderIds || []).map(Number).filter(Boolean))];
  const bundle = buildOrderBundle(uniqueIds);
  allocations.clear();
  currentItemId = null;
  viewingShipment = null;
  if(!bundle){
    clearOrder();
    return;
  }
  selectedOrderIds = uniqueIds;
  currentOrder = bundle;
  $('shipmentNo').value = '';
  $('customerName').value = currentOrder.customer_name || '';
  $('deliveryDueDate').value = currentOrder.delivery_due_date || '';
  $('managerName').value = currentOrder.manager_name || '';
  $('orderStatus').value = currentOrder.status || '';
  $('shipmentStatus').value = 'LOT 배정 대기';
  $('shipmentNote').value = '';
  renderItems();
  updateSummary();
}

async function loadOrders(preserveOrderIds=null){
  orders = await getJson('/api/sales/shipping-entry/open-orders');
  const select = $('orderSelect');
  if(select){
    select.disabled = viewMode;
    select.innerHTML = '<option value="">미출고 수주 선택</option>' + orders.map(o =>
      `<option value="${o.id}">${esc(o.order_no)} | ${esc(o.customer_name)} | ${o.items.length}품목</option>`
    ).join('');
  }

  if(viewMode) return;
  const ids = Array.isArray(preserveOrderIds) ? preserveOrderIds : (preserveOrderIds ? [preserveOrderIds] : []);
  if(ids.length && ids.every(id => orders.some(o => o.id === id))){
    applySelectedOrders(ids);
  }else{
    clearOrder();
  }
}

function setEntryMode(isView){
  viewMode = isView;
  if($('orderSelect')) $('orderSelect').disabled = isView;
  $('shipmentDate').disabled = isView;
  $('shipmentNote').readOnly = isView;
  $('reloadBtn').disabled = isView;
  $('confirmBtn').disabled = true;
  $('shippingHelp').innerHTML = isView
    ? '※ 기존 출고건 조회 상태입니다. 이 화면에서는 출고내용을 확인만 할 수 있으며 수정/삭제는 출고 내역 조회 메뉴에서 처리합니다.'
    : '※ 동일 판매처의 여러 수주를 한 출고전표로 묶을 수 있습니다.<br>※ 품번별 출고대기 LOT를 스캔합니다. 뒤 LOT를 스캔해도 실제 배정은 선입 LOT부터 필요한 완전 BOX 수량만큼 자동 배정됩니다.<br>※ 포장 BOX 단위로 출고하며, 수주 잔량보다 큰 BOX는 부분 수량으로 쪼개서 출고하지 않습니다.';
}

function clearOrder(){
  currentOrder = null;
  selectedOrderIds = [];
  currentItemId = null;
  viewingShipment = null;
  allocations.clear();
  $('shipmentNo').value = '';
  $('shipmentNo').placeholder = '출고확정 시 자동발번 / 기존번호 조회';
  $('customerName').value = '';
  $('deliveryDueDate').value = '';
  $('managerName').value = '';
  $('shipmentStatus').value = 'LOT 배정 대기';
  $('orderStatus').value = '';
  $('shipmentNote').value = '';
  $('sumOrderQty').textContent = '0';
  $('sumShippedQty').textContent = '0';
  $('sumRemainingQty').textContent = '0';
  $('sumAllocatedQty').textContent = '0';
  $('itemBody').innerHTML = '<tr><td colspan="13" class="empty">미출고 수주를 선택해 주세요.</td></tr>';
  $('confirmBtn').disabled = true;
  if($('orderNoInput')) $('orderNoInput').value = '';
}

async function newEntry(){
  setEntryMode(false);
  viewingShipment = null;
  allocations.clear();
  $('shipmentDate').value = today();
  await loadOrders();
}

function selectOrder(){
  if(viewMode || !$('orderSelect')) return;
  const id = Number($('orderSelect').value || 0);
  if(!id){ clearOrder(); return; }
  applySelectedOrders([id]);
}

function itemAllocatedQty(itemId){
  return allocationFor(itemId).reduce((sum, row) => sum + num(row.box_qty), 0);
}

function renderItems(){
  const body = $('itemBody');
  if(!currentOrder || !currentOrder.items.length){
    body.innerHTML = '<tr><td colspan="13" class="empty">출고 가능한 수주 품목이 없습니다.</td></tr>';
    return;
  }

  body.innerHTML = currentOrder.items.map(item => {
    const lots = allocationFor(item.id);
    const allocated = itemAllocatedQty(item.id);
    const done = viewMode ? lots.length > 0 : allocated > 0;
    const statusText = viewMode ? '출고완료' : (done ? '배정완료' : '배정대기');
    const actionText = viewMode ? 'LOT 상세' : 'LOT 스캔';
    const actionClass = viewMode ? 'btn-light' : 'btn-primary';
    return `<tr data-item-id="${item.id}" class="${currentItemId === item.id ? 'row-selected' : ''}">
      <td><span class="status-badge ${done ? 'status-done' : 'status-wait'}">${statusText}</span></td>
      <td>${esc(item.order_no || '')}</td>
      <td>${esc(item.part_no)}</td>
      <td class="left">${esc(item.part_name || '')}</td>
      <td>${fmt(item.moq)}</td>
      <td>${esc(item.unit || 'EA')}</td>
      <td>${fmt(item.order_qty)}</td>
      <td>${fmt(item.shipped_qty)}</td>
      <td>${fmt(item.remaining_qty)}</td>
      <td><strong>${fmt(allocated)}</strong></td>
      <td>${lots.length}</td>
      <td>${viewMode ? '-' : `${fmt(item.waiting_qty)} / ${item.waiting_box_count} BOX`}</td>
      <td><button class="btn ${actionClass} lot-scan-btn" data-item-id="${item.id}">${actionText}</button></td>
    </tr>`;
  }).join('');

  body.querySelectorAll('.lot-scan-btn').forEach(btn => {
    btn.addEventListener('click', () => openLotModal(Number(btn.dataset.itemId)));
  });
}

function updateSummary(){
  if(!currentOrder) return;
  const orderQty = currentOrder.items.reduce((s,x)=>s+num(x.order_qty),0);
  const shippedQty = currentOrder.items.reduce((s,x)=>s+num(x.shipped_qty),0);
  const remainingQty = currentOrder.items.reduce((s,x)=>s+num(x.remaining_qty),0);
  const allocatedQty = currentOrder.items.reduce((s,x)=>s+itemAllocatedQty(x.id),0);
  $('sumOrderQty').textContent = fmt(orderQty);
  $('sumShippedQty').textContent = fmt(shippedQty);
  $('sumRemainingQty').textContent = fmt(remainingQty);
  $('sumAllocatedQty').textContent = fmt(allocatedQty);
  if(viewMode){
    $('shipmentStatus').value = viewingShipment?.status || 'CONFIRMED';
    $('confirmBtn').disabled = true;
  }else{
    $('shipmentStatus').value = allocatedQty > 0 ? 'LOT 배정중' : 'LOT 배정 대기';
    $('confirmBtn').disabled = allocatedQty <= 0;
  }
}

function currentItem(){
  return currentOrder?.items.find(x => x.id === currentItemId) || null;
}

function openLotModal(itemId){
  const item = currentOrder?.items.find(x => x.id === itemId);
  if(!item) return;
  currentItemId = itemId;
  $('modalPartNo').textContent = `${item.part_no}${item.order_no ? ` / ${item.order_no}` : ''}`;
  $('modalPartName').textContent = item.part_name || '';
  $('modalMoq').textContent = fmt(item.moq);
  $('modalOrderQty').textContent = fmt(item.order_qty);
  $('modalShippedQty').textContent = fmt(item.shipped_qty);
  $('modalRemainingQty').textContent = fmt(item.remaining_qty);
  $('modalWaitingBoxes').textContent = viewMode ? allocationFor(item.id).length : item.waiting_box_count;
  $('modalWaitingQty').textContent = viewMode ? fmt(itemAllocatedQty(item.id)) : fmt(item.waiting_qty);
  $('scanMessage').textContent = '';
  $('scanMessage').className = 'scan-msg';
  $('lotScanInput').value = '';
  $('scanLine').style.display = viewMode ? 'none' : 'flex';
  $('resetAllocationBtn').style.display = viewMode ? 'none' : '';
  $('lotModalTitle').textContent = viewMode ? '출고 LOT 상세' : 'LOT No 스캔 / 배정';
  $('modalHelp').innerHTML = viewMode
    ? '※ 확정된 출고건에 실제 사용된 출고대기 LOT입니다.'
    : '※ 같은 품번이 여러 수주에 있어도 이번 출고전표 전체 기준으로 선입 LOT부터 자동 배정합니다.<br>※ 출고 확정 시 서버에서 FIFO를 다시 검증합니다.';
  renderAllocatedLots();
  renderItems();
  $('lotModal').classList.add('show');
  if(!viewMode) setTimeout(()=>$('lotScanInput').focus(), 50);
}

function closeLotModal(){
  $('lotModal').classList.remove('show');
  currentItemId = null;
  renderItems();
  updateSummary();
}

function renderAllocatedLots(){
  const rows = currentItemId ? allocationFor(currentItemId) : [];
  const body = $('allocatedLotBody');
  body.innerHTML = rows.length ? rows.map((x,index)=>`<tr>
    <td>${index+1}</td>
    <td>${esc(x.package_lot_no)}</td>
    <td>${esc(x.packing_date || '')}</td>
    <td>${fmt(x.box_qty)}</td>
    <td>${fmt(x.box_qty)}</td>
  </tr>`).join('') : '<tr><td colspan="5" class="empty">LOT 내역이 없습니다.</td></tr>';
  $('allocatedBoxCount').textContent = rows.length;
  $('allocatedQty').textContent = fmt(rows.reduce((s,x)=>s+num(x.box_qty),0));
}

function reservedBoxIdsForItem(item){
  if(!currentOrder || !item) return [];
  const ids = [];
  currentOrder.items.forEach(other => {
    if(other.id === item.id || other.part_no !== item.part_no) return;
    allocationFor(other.id).forEach(row => ids.push(row.id));
  });
  return [...new Set(ids)];
}

async function scanLot(){
  if(viewMode) return;
  const item = currentItem();
  if(!item) return;
  const lotNo = $('lotScanInput').value.trim();
  if(!lotNo){
    $('scanMessage').textContent = 'LOT 번호를 스캔해 주세요.';
    $('scanMessage').className = 'scan-msg error';
    return;
  }

  const rows = allocationFor(item.id);
  try{
    const data = await getJson('/api/sales/shipping-entry/scan', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        sales_order_item_id:item.id,
        lot_no:lotNo,
        selected_box_ids:rows.map(x=>x.id),
        reserved_box_ids:reservedBoxIdsForItem(item)
      })
    });
    allocations.set(item.id, Array.isArray(data.allocations) ? data.allocations : []);
    const autoCount = Number(data.auto_added_count || 0);
    const fullText = data.is_full_allocated ? ' / 필요수량 배정 완료' : '';
    $('scanMessage').textContent = `FIFO 자동배정: ${autoCount} BOX 추가 / 누계 ${fmt(data.allocated_qty)} ${item.unit}${fullText}`;
    $('scanMessage').className = 'scan-msg ok';
    $('lotScanInput').value = '';
    renderAllocatedLots();
    renderItems();
    updateSummary();
  }catch(e){
    $('scanMessage').textContent = e.message;
    $('scanMessage').className = 'scan-msg error';
    $('lotScanInput').select();
  }
  $('lotScanInput').focus();
}

function resetCurrentAllocation(){
  if(viewMode) return;
  const item = currentItem();
  if(!item) return;
  allocations.set(item.id, []);
  $('scanMessage').textContent = '현재 수주 품목의 LOT 배정을 초기화했습니다.';
  $('scanMessage').className = 'scan-msg';
  renderAllocatedLots();
  renderItems();
  updateSummary();
  $('lotScanInput').focus();
}

async function confirmShipment(){
  if(viewMode) return;
  if(!currentOrder) return alert('미출고 수주를 선택해 주세요.');
  const items = currentOrder.items
    .map(item => ({sales_order_item_id:item.id, packing_box_ids:allocationFor(item.id).map(x=>x.id)}))
    .filter(row => row.packing_box_ids.length > 0);
  if(!items.length) return alert('출고할 LOT를 먼저 배정해 주세요.');
  const orderCount = currentOrder.order_ids?.length || 1;
  if(!confirm(`수주 ${orderCount}건 / 품목 ${items.length}개를 1건의 출고전표로 처리하시겠습니까?`)) return;

  $('confirmBtn').disabled = true;
  try{
    const data = await getJson('/api/sales/shipping-entry/confirm', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({shipment_date:$('shipmentDate').value, items, note:$('shipmentNote').value.trim() || null})
    });
    alert(`${data.message}\n출고번호: ${data.shipment_no}\n수주건수: ${data.order_count || orderCount}\n품목수: ${data.item_count}\n출고수량: ${fmt(data.total_qty)}`);
    await loadShipmentByNo(data.shipment_no);
  }catch(e){
    alert(e.message);
    updateSummary();
  }
}

async function searchShipmentLookup(autoLoadSingle=false){
  const keyword = $('shipmentLookupKeyword').value.trim();
  const params = new URLSearchParams();
  if(keyword) params.set('shipment_no', keyword);
  const data = await getJson('/api/shipping/inquiry?' + params.toString());
  const rows = data.items || [];
  if(autoLoadSingle && rows.length === 1){
    await loadShipmentDetail(rows[0].id);
    closeShipmentLookup();
    return;
  }
  const body = $('shipmentLookupBody');
  body.innerHTML = rows.length ? rows.map(row => `<tr>
    <td>${esc(row.shipment_no)}</td><td>${esc(row.shipment_date)}</td><td>${esc((row.order_nos || [row.order_no]).join(', '))}</td>
    <td class="left">${esc(row.customer_name)}</td><td>${esc((row.part_nos || []).join(', '))}</td>
    <td>${fmt(row.total_qty)}</td><td><button class="btn btn-primary choose-shipment" data-id="${row.id}">선택</button></td>
  </tr>`).join('') : '<tr><td colspan="7" class="empty">조회된 출고건이 없습니다.</td></tr>';
  body.querySelectorAll('.choose-shipment').forEach(btn => btn.addEventListener('click', async()=>{
    await loadShipmentDetail(Number(btn.dataset.id));
    closeShipmentLookup();
  }));
}

async function openShipmentLookup(){
  $('shipmentLookupKeyword').value = $('shipmentNo').value.trim();
  $('shipmentLookupModal').classList.add('show');
  await searchShipmentLookup(false);
  $('shipmentLookupKeyword').focus();
}

function closeShipmentLookup(){
  $('shipmentLookupModal').classList.remove('show');
}

async function loadShipmentByNo(shipmentNo){
  const data = await getJson('/api/shipping/inquiry?shipment_no=' + encodeURIComponent(shipmentNo));
  const exact = (data.items || []).find(x => String(x.shipment_no).toLowerCase() === String(shipmentNo).toLowerCase());
  if(!exact) throw new Error('해당 출고번호를 찾을 수 없습니다.');
  await loadShipmentDetail(exact.id);
}

async function loadShipmentDetail(shipmentId){
  const detail = await getJson(`/api/shipping/inquiry/${shipmentId}`);
  allocations.clear();
  viewingShipment = detail;
  setEntryMode(true);

  const items = (detail.items || []).map(row => {
    const boxes = (row.boxes || []).map(box => ({
      id: box.packing_box_id,
      package_lot_no: box.waiting_lot_no,
      box_qty: num(box.shipped_qty),
      packing_date: ''
    }));
    allocations.set(row.sales_order_item_id, boxes);
    return {
      id: row.sales_order_item_id,
      order_id: row.sales_order_id,
      order_no: row.order_no || '',
      part_no: row.part_no,
      part_name: row.part_name || '',
      moq: 0,
      unit: row.unit || 'EA',
      order_qty: num(row.order_qty),
      shipped_qty: num(row.shipped_qty),
      remaining_qty: Math.max(num(row.order_qty) - num(row.shipped_qty), 0),
      waiting_qty: 0,
      waiting_box_count: 0
    };
  });

  const dueDates = (detail.items || []).map(x => x.delivery_due_date).filter(Boolean).sort();
  const managers = [...new Set((detail.items || []).map(x => x.manager_name).filter(Boolean))];
  const statuses = [...new Set((detail.items || []).map(x => x.order_status).filter(Boolean))];
  currentOrder = {
    id: detail.sales_order_id,
    order_ids: detail.sales_order_ids || [],
    order_no: (detail.order_nos || []).join(', '),
    order_nos: detail.order_nos || [],
    customer_name: detail.customer_name,
    delivery_due_date: dueDates[0] || '',
    manager_name: managers.join(', '),
    status: (detail.order_count || 0) > 1 ? `${detail.order_count}건` : (statuses[0] || ''),
    items
  };
  selectedOrderIds = [...(detail.sales_order_ids || [])];

  $('shipmentNo').value = detail.shipment_no;
  $('shipmentDate').value = detail.shipment_date || '';
  $('customerName').value = detail.customer_name || '';
  $('deliveryDueDate').value = currentOrder.delivery_due_date || '';
  $('managerName').value = currentOrder.manager_name || '';
  $('shipmentStatus').value = detail.status || 'CONFIRMED';
  $('orderStatus').value = currentOrder.status || '';
  $('shipmentNote').value = detail.note || '';
  if($('orderNoInput')) $('orderNoInput').value = currentOrder.order_no;
  currentItemId = null;
  renderItems();
  updateSummary();
}

document.addEventListener('DOMContentLoaded',()=>{
  $('shipmentDate').value = today();
  if($('orderSelect')) $('orderSelect').addEventListener('change', selectOrder);
  $('reloadBtn').addEventListener('click',()=>loadOrders(selectedOrderIds).catch(e=>alert(e.message)));
  $('confirmBtn').addEventListener('click', confirmShipment);
  $('scanBtn').addEventListener('click', scanLot);
  $('lotScanInput').addEventListener('keydown', e => { if(e.key === 'Enter'){ e.preventDefault(); scanLot(); } });
  $('resetAllocationBtn').addEventListener('click', resetCurrentAllocation);
  $('allocationDoneBtn').addEventListener('click', closeLotModal);
  $('modalCloseX').addEventListener('click', closeLotModal);
  $('lotModal').addEventListener('click', e => { if(e.target === $('lotModal')) closeLotModal(); });

  $('shipmentLookupBtn').addEventListener('click',()=>openShipmentLookup().catch(e=>alert(e.message)));
  $('newEntryBtn').addEventListener('click',()=>newEntry().catch(e=>alert(e.message)));
  $('shipmentNo').addEventListener('keydown', e => {
    if(e.key === 'Enter'){
      e.preventDefault();
      const value = $('shipmentNo').value.trim();
      if(value) loadShipmentByNo(value).catch(e=>alert(e.message));
      else openShipmentLookup().catch(e=>alert(e.message));
    }
  });
  $('shipmentLookupSearchBtn').addEventListener('click',()=>searchShipmentLookup(false).catch(e=>alert(e.message)));
  $('shipmentLookupKeyword').addEventListener('keydown', e => { if(e.key === 'Enter'){ e.preventDefault(); searchShipmentLookup(false).catch(err=>alert(err.message)); } });
  $('shipmentLookupClose').addEventListener('click', closeShipmentLookup);
  $('shipmentLookupModal').addEventListener('click', e => { if(e.target === $('shipmentLookupModal')) closeShipmentLookup(); });

  setEntryMode(false);
  loadOrders().catch(e=>alert(e.message));
});
