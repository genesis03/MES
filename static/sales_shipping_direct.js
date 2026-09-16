(() => {
  const directType = item => ['SAMPLE','DEVELOPMENT'].includes(String(item?.order_type || '').toUpperCase());
  const targetQty = item => Number(item?.requested_qty ?? item?.remaining_qty ?? 0);

  const baseRenderItems = renderItems;
  renderItems = function(){
    baseRenderItems();
    if(!currentOrder || viewMode) return;
    currentOrder.items.forEach(item => {
      if(!directType(item)) return;
      const tr = document.querySelector(`#itemBody tr[data-item-id="${item.id}"]`);
      if(!tr) return;
      const cells = tr.querySelectorAll('td');
      const rows = allocationFor(item.id);
      const qty = itemAllocatedQty(item.id);
      if(cells[10]) cells[10].textContent = `${rows.length} LOT / ${fmt(qty)}`;
      if(cells[11]) cells[11].textContent = '포장 생략';
      const btn = tr.querySelector('.lot-scan-btn');
      if(btn) btn.textContent = viewMode ? 'LOT 상세' : '생산 LOT 스캔';
    });
  };

  const baseOpenLotModal = openLotModal;
  openLotModal = async function(itemId){
    const item = currentOrder?.items?.find(x => x.id === itemId);
    baseOpenLotModal(itemId);
    if(!item || viewMode || !directType(item)) return;
    try{
      const rows = await getJson(`/api/sales/shipping-entry/direct-lots?sales_order_item_id=${item.id}`);
      document.getElementById('lotModalTitle').textContent = '생산 LOT 직출고 / 배정';
      document.getElementById('modalWaitingBoxes').textContent = `${rows.length} LOT`;
      document.getElementById('modalWaitingQty').textContent = fmt(rows.reduce((s,x)=>s+Number(x.available_qty||0),0));
      document.getElementById('lotScanInput').placeholder = '미포장 생산 LOT 스캔 후 Enter';
      document.getElementById('modalHelp').innerHTML = `※ ${item.order_type === 'SAMPLE' ? '샘플' : '개발'} 수주는 포장 없이 미포장 생산 LOT에서 직접 출고합니다.<br>※ 금회 출고 지정수량: <strong>${fmt(targetQty(item))} ${esc(item.unit || 'EA')}</strong><br>※ 이미 포장에 배정된 수량은 직출고 가능수량에서 제외됩니다.<br>※ 생산 LOT 한 개의 일부 수량만 배정할 수 있습니다.`;
    }catch(e){
      document.getElementById('scanMessage').textContent = e.message;
      document.getElementById('scanMessage').className = 'scan-msg error';
    }
  };

  const baseScanLot = scanLot;
  scanLot = async function(){
    if(viewMode) return;
    const item = currentItem();
    if(!item || !directType(item)) return baseScanLot();
    const target = targetQty(item);
    const lotNo = document.getElementById('lotScanInput').value.trim();
    if(target <= 0){
      document.getElementById('scanMessage').textContent = '수주번호 조회에서 금회 출고수량을 먼저 입력해 주세요.';
      document.getElementById('scanMessage').className = 'scan-msg error';
      return;
    }
    if(!lotNo){
      document.getElementById('scanMessage').textContent = '생산 LOT 번호를 스캔해 주세요.';
      document.getElementById('scanMessage').className = 'scan-msg error';
      return;
    }
    const rows = allocationFor(item.id);
    try{
      const data = await getJson('/api/sales/shipping-entry/direct-scan', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          sales_order_item_id:item.id,
          lot_no:lotNo,
          requested_qty:target,
          selected_lots:rows.map(x=>({production_lot_id:Number(x.production_lot_id || x.id), qty:Number(x.box_qty || 0)}))
        })
      });
      allocations.set(item.id, Array.isArray(data.allocations) ? data.allocations : []);
      const exact = data.is_full_allocated === true;
      document.getElementById('scanMessage').textContent = exact
        ? `생산 LOT 직출고 배정 완료: ${fmt(data.allocated_qty)} ${item.unit}`
        : `생산 LOT 배정: ${fmt(data.allocated_qty)} / 지정 ${fmt(target)} ${item.unit}`;
      document.getElementById('scanMessage').className = exact ? 'scan-msg ok' : 'scan-msg';
      document.getElementById('lotScanInput').value = '';
      renderAllocatedLots();
      renderItems();
      updateSummary();
    }catch(e){
      document.getElementById('scanMessage').textContent = e.message;
      document.getElementById('scanMessage').className = 'scan-msg error';
      document.getElementById('lotScanInput').select();
    }
    document.getElementById('lotScanInput').focus();
  };

  const baseConfirmShipment = confirmShipment;
  confirmShipment = async function(){
    if(viewMode || !currentOrder) return baseConfirmShipment();
    const targetItems = currentOrder.items.filter(item => targetQty(item) > 0);
    const directItems = targetItems.filter(directType);
    if(!directItems.length) return baseConfirmShipment();
    if(directItems.length !== targetItems.length){
      return alert('양산 수주와 샘플/개발 직출고 수주는 한 출고전표에 혼합할 수 없습니다. 출고전표를 나누어 처리해 주세요.');
    }
    const notReady = directItems.find(item => Math.abs(itemAllocatedQty(item.id) - targetQty(item)) > 1e-9);
    if(notReady){
      return alert(`${notReady.order_no || ''} / ${notReady.part_no}: 금회 출고수량과 생산 LOT 배정수량이 일치하지 않습니다.`);
    }
    const items = directItems.map(item => ({
      sales_order_item_id:item.id,
      requested_qty:targetQty(item),
      direct_lots:allocationFor(item.id).map(x=>({production_lot_id:Number(x.production_lot_id || x.id), qty:Number(x.box_qty || 0)}))
    }));
    const total = directItems.reduce((s,item)=>s+targetQty(item),0);
    if(!confirm(`샘플/개발 생산 LOT 직출고 ${items.length}품목 / 총 ${fmt(total)}을 처리하시겠습니까?`)) return;
    document.getElementById('confirmBtn').disabled = true;
    try{
      const data = await getJson('/api/sales/shipping-entry/direct-confirm', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({shipment_date:document.getElementById('shipmentDate').value, items, note:document.getElementById('shipmentNote').value.trim() || null})
      });
      alert(`${data.message}\n출고번호: ${data.shipment_no}\n출고수량: ${fmt(data.total_qty)}`);
      await loadShipmentByNo(data.shipment_no);
    }catch(e){
      alert(e.message);
      updateSummary();
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    const help = document.getElementById('shippingHelp');
    if(help) help.innerHTML += '<br>※ 샘플/개발 수주는 포장 없이 미포장 생산 LOT에서 직접 출고할 수 있습니다. 이미 포장에 배정된 수량은 직출고 대상에서 제외됩니다.';
  });
})();
