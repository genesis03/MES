const $ = (id) => document.getElementById(id);
let openItems = [];
let boxes = [];

function today(){ return new Date().toISOString().slice(0,10); }
async function getJson(url, options={}){
  const res = await fetch(url, options);
  const data = await res.json().catch(()=>({}));
  if(!res.ok) throw new Error(data.detail || data.message || '처리에 실패했습니다.');
  return data;
}

async function loadOpenItems(){
  openItems = await getJson('/api/sales/shipping/open-items');
  $('orderItemSelect').innerHTML = '<option value="">선택</option>' + openItems.map(x => `<option value="${x.id}">${x.order_no} | ${x.customer_name} | ${x.part_no} | 잔량 ${x.remaining_qty}</option>`).join('');
  clearSelection();
}

function clearSelection(){
  $('customerName').value=''; $('partInfo').value=''; $('orderQty').textContent='0'; $('shippedQty').textContent='0'; $('remainingQty').textContent='0'; $('selectedQty').textContent='0';
  $('boxBody').innerHTML='<tr><td colspan="6">수주 품목을 선택해 주세요.</td></tr>';
}

async function selectOrderItem(){
  const id = Number($('orderItemSelect').value || 0);
  const item = openItems.find(x => x.id === id);
  if(!item){ clearSelection(); return; }
  $('customerName').value = item.customer_name;
  $('partInfo').value = `${item.part_no} / ${item.part_name || ''}`;
  $('orderQty').textContent = item.order_qty;
  $('shippedQty').textContent = item.shipped_qty;
  $('remainingQty').textContent = item.remaining_qty;
  await loadBoxes();
}

async function loadBoxes(){
  const id = Number($('orderItemSelect').value || 0);
  if(!id) return clearSelection();
  boxes = await getJson(`/api/sales/shipping/waiting-boxes?sales_order_item_id=${id}`);
  const body = $('boxBody');
  body.innerHTML = boxes.length ? boxes.map(x => `<tr><td><input type="checkbox" class="box-check" value="${x.id}" data-qty="${x.box_qty}"></td><td>${x.package_lot_no}</td><td>${x.packing_date}</td><td>${x.box_qty}</td><td>${x.part_no}</td><td>${x.part_name || ''}</td></tr>`).join('') : '<tr><td colspan="6">출고 가능한 출고대기 LOT가 없습니다.</td></tr>';
  body.querySelectorAll('.box-check').forEach(el => el.addEventListener('change', updateSelectedQty));
  updateSelectedQty();
}

function updateSelectedQty(){
  const total = [...document.querySelectorAll('.box-check:checked')].reduce((s,x)=>s+Number(x.dataset.qty||0),0);
  $('selectedQty').textContent = total;
}

async function ship(){
  const salesOrderItemId = Number($('orderItemSelect').value || 0);
  const packingBoxIds = [...document.querySelectorAll('.box-check:checked')].map(x=>Number(x.value));
  if(!salesOrderItemId) return alert('미출고 수주품목을 선택해 주세요.');
  if(!packingBoxIds.length) return alert('출고할 출고대기 LOT를 선택해 주세요.');
  try{
    const data = await getJson('/api/sales/shipping', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({shipment_date:$('shipmentDate').value, sales_order_item_id:salesOrderItemId, packing_box_ids:packingBoxIds, note:$('shipmentNote').value.trim()||null})
    });
    alert(`${data.message}\n출고번호: ${data.shipment_no}\n출고수량: ${data.shipped_qty}`);
    $('shipmentNote').value='';
    await Promise.all([loadOpenItems(), loadHistory()]);
  }catch(e){ alert(e.message); }
}

async function loadHistory(){
  const rows = await getJson('/api/sales/shipping/records');
  const body = $('historyBody');
  const flat = rows.flatMap(r => r.items.map(i => ({...r, item:i})));
  body.innerHTML = flat.length ? flat.map(x => `<tr><td>${x.shipment_no}</td><td>${x.shipment_date}</td><td>${x.order_no}</td><td>${x.customer_name}</td><td>${x.item.part_no}</td><td>${x.item.shipped_qty} ${x.item.unit}</td><td>${x.item.package_lots.join(', ')}</td></tr>`).join('') : '<tr><td colspan="7">출고 내역이 없습니다.</td></tr>';
}

document.addEventListener('DOMContentLoaded',()=>{
  $('shipmentDate').value=today();
  $('orderItemSelect').addEventListener('change',()=>selectOrderItem().catch(e=>alert(e.message)));
  $('reloadBoxesBtn').addEventListener('click',()=>loadBoxes().catch(e=>alert(e.message)));
  $('shipBtn').addEventListener('click',ship);
  Promise.all([loadOpenItems(),loadHistory()]).catch(e=>alert(e.message));
});
