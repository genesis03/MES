const $ = (id) => document.getElementById(id);

let orders = [];
let currentOrder = null;
let currentItemId = null;
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

async function loadOrders(preserveOrderId=null){
  orders = await getJson('/api/sales/shipping-entry/open-orders');
  const select = $('orderSelect');
  select.innerHTML = '<option value="">미출고 수주 선택</option>' + orders.map(o =>
    `<option value="${o.id}">${esc(o.order_no)} | ${esc(o.customer_name)} | ${o.items.length}품목</option>`
  ).join('');

  if(preserveOrderId && orders.some(o => o.id === preserveOrderId)){
    select.value = String(preserveOrderId);
    selectOrder();
  }else{
    clearOrder();
  }
}

function clearOrder(){
  currentOrder = null;
  currentItemId = null;
  allocations.clear();
  $('shipmentNo').value = '출고확정 시 자동발번';
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
  $('itemBody').innerHTML = '<tr><td colspan="12" class="empty">미출고 수주를 선택해 주세요.</td></tr>';
  $('confirmBtn').disabled = true;
}

function selectOrder(){
  const id = Number($('orderSelect').value || 0);
  currentOrder = orders.find(o => o.id === id) || null;
  allocations.clear();
  currentItemId = null;

  if(!currentOrder){ clearOrder(); return; }

  $('customerName').value = currentOrder.customer_name || '';
  $('deliveryDueDate').value = currentOrder.delivery_due_date || '';
  $('managerName').value = currentOrder.manager_name || '';
  $('orderStatus').value = currentOrder.status || '';
  $('shipmentStatus').value = 'LOT 배정 대기';
  $('shipmentNote').value = '';
  renderItems();
  updateSummary();
}

function itemAllocatedQty(itemId){
  return allocationFor(itemId).reduce((sum, row) => sum + num(row.box_qty), 0);
}

function renderItems(){
  const body = $('itemBody');
  if(!currentOrder || !currentOrder.items.length){
    body.innerHTML = '<tr><td colspan="12" class="empty">출고 가능한 수주 품목이 없습니다.</td></tr>';
    return;
  }

  body.innerHTML = currentOrder.items.map(item => {
    const lots = allocationFor(item.id);
    const allocated = itemAllocatedQty(item.id);
    const done = lots.length > 0;
    return `<tr data-item-id="${item.id}" class="${currentItemId === item.id ? 'row-selected' : ''}">
      <td><span class="status-badge ${done ? 'status-done' : 'status-wait'}">${done ? '배정완료' : '배정대기'}</span></td>
      <td>${esc(item.part_no)}</td>
      <td class="left">${esc(item.part_name || '')}</td>
      <td>${fmt(item.moq)}</td>
      <td>${esc(item.unit || 'EA')}</td>
      <td>${fmt(item.order_qty)}</td>
      <td>${fmt(item.shipped_qty)}</td>
      <td>${fmt(item.remaining_qty)}</td>
      <td><strong>${fmt(allocated)}</strong></td>
      <td>${lots.length}</td>
      <td>${fmt(item.waiting_qty)} / ${item.waiting_box_count} BOX</td>
      <td><button class="btn btn-primary lot-scan-btn" data-item-id="${item.id}">LOT 스캔</button></td>
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
  $('shipmentStatus').value = allocatedQty > 0 ? 'LOT 배정중' : 'LOT 배정 대기';
  $('confirmBtn').disabled = allocatedQty <= 0;
}

function currentItem(){
  return currentOrder?.items.find(x => x.id === currentItemId) || null;
}

function openLotModal(itemId){
  const item = currentOrder?.items.find(x => x.id === itemId);
  if(!item) return;
  currentItemId = itemId;
  $('modalPartNo').textContent = item.part_no;
  $('modalPartName').textContent = item.part_name || '';
  $('modalMoq').textContent = fmt(item.moq);
  $('modalOrderQty').textContent = fmt(item.order_qty);
  $('modalShippedQty').textContent = fmt(item.shipped_qty);
  $('modalRemainingQty').textContent = fmt(item.remaining_qty);
  $('modalWaitingBoxes').textContent = item.waiting_box_count;
  $('modalWaitingQty').textContent = fmt(item.waiting_qty);
  $('scanMessage').textContent = '';
  $('scanMessage').className = 'scan-msg';
  $('lotScanInput').value = '';
  renderAllocatedLots();
  renderItems();
  $('lotModal').classList.add('show');
  setTimeout(()=>$('lotScanInput').focus(), 50);
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
  </tr>`).join('') : '<tr><td colspan="5" class="empty">스캔된 LOT가 없습니다.</td></tr>';
  $('allocatedBoxCount').textContent = rows.length;
  $('allocatedQty').textContent = fmt(rows.reduce((s,x)=>s+num(x.box_qty),0));
}

async function scanLot(){
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
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        sales_order_item_id:item.id,
        lot_no:lotNo,
        selected_box_ids:rows.map(x=>x.id)
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
  const item = currentItem();
  if(!item) return;
  allocations.set(item.id, []);
  $('scanMessage').textContent = '현재 품번의 LOT 배정을 초기화했습니다.';
  $('scanMessage').className = 'scan-msg';
  renderAllocatedLots();
  renderItems();
  updateSummary();
  $('lotScanInput').focus();
}

async function confirmShipment(){
  if(!currentOrder) return alert('미출고 수주를 선택해 주세요.');
  const items = currentOrder.items
    .map(item => ({sales_order_item_id:item.id, packing_box_ids:allocationFor(item.id).map(x=>x.id)}))
    .filter(row => row.packing_box_ids.length > 0);
  if(!items.length) return alert('출고할 LOT를 먼저 배정해 주세요.');
  if(!confirm(`선택한 ${items.length}개 품목을 출고 처리하시겠습니까?`)) return;

  $('confirmBtn').disabled = true;
  try{
    const data = await getJson('/api/sales/shipping-entry/confirm', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        shipment_date:$('shipmentDate').value,
        sales_order_id:currentOrder.id,
        items,
        note:$('shipmentNote').value.trim() || null
      })
    });
    alert(`${data.message}\n출고번호: ${data.shipment_no}\n품목수: ${data.item_count}\n출고수량: ${fmt(data.total_qty)}`);
    $('shipmentNo').value = data.shipment_no;
    const previousOrderId = currentOrder.id;
    allocations.clear();
    await loadOrders(previousOrderId);
  }catch(e){
    alert(e.message);
  }finally{
    updateSummary();
  }
}

document.addEventListener('DOMContentLoaded',()=>{
  $('shipmentDate').value = today();
  $('orderSelect').addEventListener('change', selectOrder);
  $('reloadBtn').addEventListener('click',()=>loadOrders(currentOrder?.id || null).catch(e=>alert(e.message)));
  $('confirmBtn').addEventListener('click', confirmShipment);
  $('scanBtn').addEventListener('click', scanLot);
  $('lotScanInput').addEventListener('keydown', e => {
    if(e.key === 'Enter'){
      e.preventDefault();
      scanLot();
    }
  });
  $('resetAllocationBtn').addEventListener('click', resetCurrentAllocation);
  $('allocationDoneBtn').addEventListener('click', closeLotModal);
  $('modalCloseX').addEventListener('click', closeLotModal);
  $('lotModal').addEventListener('click', e => {
    if(e.target === $('lotModal')) closeLotModal();
  });
  loadOrders().catch(e=>alert(e.message));
});
