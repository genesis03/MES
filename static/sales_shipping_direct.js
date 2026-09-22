(() => {
  const directType = item => ['SAMPLE','DEVELOPMENT'].includes(String(item?.order_type || '').toUpperCase());
  const targetQty = item => Number(item?.requested_qty ?? item?.remaining_qty ?? 0);

  const baseRenderItems = renderItems;
  renderItems = function(){
    baseRenderItems();
    if(!currentOrder) return;
    currentOrder.items.forEach(item => {
      if(!directType(item)) return;
      const tr = document.querySelector(`#itemBody tr[data-item-id="${item.id}"]`);
      if(!tr) return;
      const cells = tr.querySelectorAll('td');
      const rows = allocationFor(item.id);
      const qty = itemAllocatedQty(item.id);
      if(cells[10]) cells[10].textContent = `${rows.length} LOT / ${fmt(qty)}`;
      if(cells[11]) cells[11].textContent = viewMode ? '-' : '포장 생략';
      const btn = tr.querySelector('.lot-scan-btn');
      if(btn) btn.textContent = viewMode ? '출고 LOT 상세' : '생산 LOT 스캔';
    });
  };

  const baseOpenLotModal = openLotModal;
  openLotModal = async function(itemId){
    const item = currentOrder?.items?.find(x => x.id === itemId);
    baseOpenLotModal(itemId);
    if(!item || !directType(item)) return;
    document.getElementById('lotModalTitle').textContent = viewMode ? '직출고 LOT 상세' : '생산 LOT 직출고 / 배정';
    document.getElementById('lotScanInput').placeholder = '미포장 생산 LOT 스캔 후 Enter';
    if(viewMode){
      const rows = allocationFor(item.id);
      const outboundLots = [...new Set(rows.map(x => x.package_lot_no).filter(Boolean))];
      document.getElementById('modalWaitingBoxes').textContent = `${outboundLots.length || rows.length} BOX`;
      document.getElementById('modalWaitingQty').textContent = fmt(itemAllocatedQty(item.id));
      document.getElementById('modalHelp').innerHTML = '※ 샘플/개발도 BOX별로 포장 LOT(=출고 LOT)가 부여됩니다. 원 생산 LOT 계보는 출고 내역 조회에서 확인할 수 있습니다.';
      return;
    }
    try{
      const rows = await getJson(`/api/sales/shipping-entry/direct-lots?sales_order_item_id=${item.id}`);
      const standard = Number(rows?.[0]?.standard_box_qty || item.moq || 0);
      const expectedBoxes = standard > 0 ? Math.ceil(targetQty(item) / standard) : 1;
      document.getElementById('modalWaitingBoxes').textContent = `${expectedBoxes} BOX 예정`;
      document.getElementById('modalWaitingQty').textContent = fmt(rows.reduce((s,x)=>s+Number(x.available_qty||0),0));
      document.getElementById('modalHelp').innerHTML = `※ ${item.order_type === 'SAMPLE' ? '샘플' : '개발'} 수주는 포장 공정을 생략하지만 출고 시 BOX 단위로 포장 LOT(=출고 LOT)를 생성합니다.<br>※ 금회 출고 지정수량: <strong>${fmt(targetQty(item))} ${esc(item.unit || 'EA')}</strong><br>※ 기준 BOX 수량: <strong>${fmt(standard || targetQty(item))}</strong> / 예상 BOX: <strong>${expectedBoxes}</strong><br>※ 출고 확정 시 BOX마다 YYMMDD+01+3자리 순번의 LOT가 자동 발번됩니다.`;
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
        ? `생산 LOT 직출고 배정 완료: ${fmt(data.allocated_qty)} ${item.unit} / ${data.box_count || 1} BOX 예정`
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
      const outboundText = (data.outbound_lots || []).length ? `\n포장/출고 LOT: ${(data.outbound_lots || []).join(', ')}` : '';
      alert(`${data.message}\n출고번호: ${data.shipment_no}${outboundText}\nBOX: ${data.box_count || 0}\n출고수량: ${fmt(data.total_qty)}`);
      await loadShipmentByNo(data.shipment_no);
    }catch(e){
      alert(e.message);
      updateSummary();
    }
  };

  const baseLoadShipmentDetail = loadShipmentDetail;
  loadShipmentDetail = async function(shipmentId){
    const detail = await getJson(`/api/shipping/inquiry/${shipmentId}`);
    const hasDirect = (detail.items || []).some(row => (row.direct_lots || []).length > 0);
    if(!hasDirect) return baseLoadShipmentDetail(shipmentId);

    allocations.clear();
    viewingShipment = detail;
    editShipmentMode = requestedEditShipmentId > 0 && Number(shipmentId) === requestedEditShipmentId;
    setEntryMode(true);
    const items = (detail.items || []).map(row => {
      const directLots = (row.direct_lots || []).map(lot => ({
        id: `${lot.production_lot_id}-${lot.box_no || 0}-${lot.outbound_lot_no || ''}`,
        production_lot_id: lot.production_lot_id,
        package_lot_no: lot.outbound_lot_no || lot.lot_no,
        source_lot_no: lot.source_lot_no || lot.lot_no,
        box_qty: num(lot.shipped_qty),
        packing_date: '',
        box_no: lot.box_no || null,
        direct: true
      }));
      allocations.set(row.sales_order_item_id, directLots);
      return {
        id: row.sales_order_item_id,
        order_id: row.sales_order_id,
        order_no: row.order_no || '',
        order_type: row.order_type || 'NORMAL',
        transaction_type: row.transaction_type || 'PAID',
        part_no: row.part_no,
        part_name: row.part_name || '',
        moq: 0,
        unit: row.unit || 'EA',
        order_qty: num(row.order_qty),
        shipped_qty: num(row.shipped_qty),
        remaining_qty: Math.max(num(row.order_qty) - num(row.shipped_qty), 0),
        waiting_qty: 0,
        waiting_box_count: 0,
        requested_qty: num(row.shipped_qty)
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
    document.getElementById('shipmentNo').value = detail.shipment_no;
    document.getElementById('shipmentDate').value = detail.shipment_date || '';
    if(editShipmentMode){
      document.getElementById('shipmentDate').disabled = true;
      document.getElementById('shipmentDate').title = '샘플/개발 직출고는 출고 LOT 번호에 일자가 포함되어 있어 일자를 변경할 수 없습니다.';
    }
    document.getElementById('customerName').value = detail.customer_name || '';
    document.getElementById('deliveryDueDate').value = currentOrder.delivery_due_date || '';
    document.getElementById('managerName').value = currentOrder.manager_name || '';
    document.getElementById('shipmentStatus').value = detail.status || 'CONFIRMED';
    document.getElementById('orderStatus').value = currentOrder.status || '';
    document.getElementById('shipmentNote').value = detail.note || '';
    if(document.getElementById('orderNoInput')) document.getElementById('orderNoInput').value = currentOrder.order_no;
    currentItemId = null;
    renderItems();
    updateSummary();
    if(editShipmentMode){
      document.getElementById('confirmBtn').disabled = false;
      document.getElementById('confirmBtn').textContent = '출고 수정 저장';
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    const help = document.getElementById('shippingHelp');
    if(help) help.innerHTML += '<br>※ 샘플/개발 수주는 포장 공정을 생략하지만 출고 시 BOX마다 포장 LOT(=출고 LOT)가 자동 발번됩니다.';
  });
})();