(() => {
  const byId = (id) => document.getElementById(id);
  const escLookup = (v) => String(v ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[s]));
  const fmtLookup = (v) => Number(v || 0).toLocaleString('ko-KR', {maximumFractionDigits: 3});

  function visibleOrderText(){
    if(typeof currentOrder === 'undefined' || !currentOrder) return '';
    const nos = currentOrder.order_nos || (currentOrder.order_no ? [currentOrder.order_no] : []);
    if(nos.length <= 2) return nos.join(', ');
    return `${nos[0]} 외 ${nos.length - 1}건`;
  }

  function syncVisibleOrderNo(){
    const input = byId('orderNoInput');
    if(!input) return;
    input.value = visibleOrderText();
    input.title = (typeof currentOrder !== 'undefined' && currentOrder?.order_no) ? currentOrder.order_no : '';
    input.readOnly = (typeof viewMode !== 'undefined' && viewMode);
  }

  function orderMatches(row){
    const no = byId('orderLookupNo')?.value.trim().toLowerCase() || '';
    const part = byId('orderLookupPart')?.value.trim().toLowerCase() || '';
    const customer = byId('orderLookupCustomer')?.value.trim().toLowerCase() || '';
    const start = byId('orderLookupStart')?.value || '';
    const end = byId('orderLookupEnd')?.value || '';

    if(no && !String(row.order_no || '').toLowerCase().includes(no)) return false;
    if(customer && !String(row.customer_name || '').toLowerCase().includes(customer)) return false;
    if(part && !(row.items || []).some(x => String(x.part_no || '').toLowerCase().includes(part))) return false;
    if(start && String(row.order_date || '') < start) return false;
    if(end && String(row.order_date || '') > end) return false;
    return true;
  }

  function checkedOrderIds(){
    return [...document.querySelectorAll('.order-lookup-check:checked')].map(x => Number(x.value));
  }

  function validateSameCustomer(changed){
    if(!changed.checked) return true;
    const checked = [...document.querySelectorAll('.order-lookup-check:checked')];
    const customerIds = [...new Set(checked.map(x => x.dataset.customerId))];
    if(customerIds.length > 1){
      changed.checked = false;
      alert('같은 판매처의 수주만 한 건의 출고전표로 묶을 수 있습니다.');
      return false;
    }
    return true;
  }

  function updateSelectedCount(){
    const ids = checkedOrderIds();
    const count = byId('orderLookupSelectedCount');
    if(count) count.textContent = `선택 : ${ids.length}건`;
  }

  function existingRequestedQty(item){
    const current = currentOrder?.items?.find(x => x.id === item.id);
    if(current && current.requested_qty !== undefined && current.requested_qty !== null){
      return Number(current.requested_qty || 0);
    }
    return Number(item.remaining_qty || 0);
  }

  function quantityEditor(order){
    return (order.items || []).map(item => {
      const remain = Number(item.remaining_qty || 0);
      const value = existingRequestedQty(item);
      return `<div style="display:grid;grid-template-columns:minmax(90px,1fr) 90px auto;gap:6px;align-items:center;margin:2px 0;">
        <span style="text-align:left;white-space:nowrap;">${escLookup(item.part_no)}</span>
        <input type="number" class="order-lookup-qty" data-order-id="${order.id}" data-item-id="${item.id}" data-remaining="${remain}" min="0" max="${remain}" step="any" value="${value}" style="width:90px;height:30px;padding:4px 6px;text-align:right;border:1px solid #94a3b8;border-radius:4px;">
        <span style="white-space:nowrap;color:#64748b;">/ ${fmtLookup(remain)}</span>
      </div>`;
    }).join('');
  }

  function renderOrderLookup(){
    const body = byId('orderLookupBody');
    if(!body) return;
    const rows = (typeof orders !== 'undefined' ? orders : []).filter(orderMatches);
    const existing = new Set(typeof selectedOrderIds !== 'undefined' ? selectedOrderIds : []);

    const table = body.closest('table');
    const lastHead = table?.querySelector('thead tr th:last-child');
    if(lastHead) lastHead.textContent = '금회출고 / 미출고';

    body.innerHTML = rows.length ? rows.map(row => {
      const partNos = (row.items || []).map(x => x.part_no).join(', ');
      return `<tr class="lookup-result-row" data-id="${row.id}">
        <td><input type="checkbox" class="order-lookup-check" value="${row.id}" data-customer-id="${row.customer_id}" ${existing.has(row.id) ? 'checked' : ''}></td>
        <td>${escLookup(row.order_no)}</td>
        <td class="left">${escLookup(row.customer_name)}</td>
        <td>${escLookup(row.order_date || '')}</td>
        <td>${escLookup(row.delivery_due_date || '')}</td>
        <td>${escLookup(row.status || '')}</td>
        <td class="left">${escLookup(partNos)}</td>
        <td>${quantityEditor(row)}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="8" class="empty">조회된 미출고 수주가 없습니다.</td></tr>';

    body.querySelectorAll('.order-lookup-check').forEach(check => {
      check.addEventListener('change', () => {
        validateSameCustomer(check);
        updateSelectedCount();
      });
    });
    body.querySelectorAll('.lookup-result-row').forEach(tr => {
      tr.addEventListener('dblclick', e => {
        if(e.target.closest('input')) return;
        const check = tr.querySelector('.order-lookup-check');
        check.checked = !check.checked;
        validateSameCustomer(check);
        updateSelectedCount();
      });
    });
    body.querySelectorAll('.order-lookup-qty').forEach(input => {
      input.addEventListener('focus', () => input.select());
      input.addEventListener('change', () => {
        let value = Number(input.value);
        const remain = Number(input.dataset.remaining || 0);
        if(!Number.isFinite(value)) value = 0;
        if(value < 0 || value > remain + 1e-9){
          alert(`금회 출고수량은 0 이상, 미출고 잔량 ${fmtLookup(remain)} 이하로 입력해 주세요.`);
          input.value = remain;
        }
      });
    });
    byId('orderLookupCount').textContent = `조회 : ${rows.length}건`;
    updateSelectedCount();
  }

  function collectRequestedQty(selectedIds){
    const selectedSet = new Set(selectedIds);
    const result = new Map();
    for(const input of document.querySelectorAll('.order-lookup-qty')){
      const orderId = Number(input.dataset.orderId || 0);
      if(!selectedSet.has(orderId)) continue;
      const itemId = Number(input.dataset.itemId || 0);
      const remain = Number(input.dataset.remaining || 0);
      const value = Number(input.value);
      if(!Number.isFinite(value) || value < 0 || value > remain + 1e-9){
        throw new Error(`금회 출고수량은 0 이상, 미출고 잔량 ${fmtLookup(remain)} 이하로 입력해 주세요.`);
      }
      result.set(itemId, value);
    }
    return result;
  }

  function applyCheckedOrders(){
    const ids = checkedOrderIds();
    if(!ids.length){
      alert('출고할 수주를 1건 이상 선택해 주세요.');
      return;
    }
    try{
      const qtyMap = collectRequestedQty(ids);
      if(typeof applySelectedOrders !== 'function') throw new Error('수주 선택 기능을 불러오지 못했습니다.');
      applySelectedOrders(ids);
      (currentOrder?.items || []).forEach(item => {
        item.requested_qty = qtyMap.has(item.id) ? Number(qtyMap.get(item.id) || 0) : 0;
      });
      syncVisibleOrderNo();
      renderItems();
      updateSummary();
      closeOrderLookup();
    }catch(e){
      alert(e.message || '수주 적용에 실패했습니다.');
    }
  }

  function openOrderLookup(){
    if(typeof viewMode !== 'undefined' && viewMode) return;
    byId('orderLookupNo').value = '';
    byId('orderLookupModal').classList.add('show');
    renderOrderLookup();
    byId('orderLookupNo').focus();
  }

  function closeOrderLookup(){
    byId('orderLookupModal').classList.remove('show');
  }

  function requestedQty(item){
    if(!item) return 0;
    if(item.requested_qty === undefined || item.requested_qty === null){
      item.requested_qty = Number(item.remaining_qty || 0);
    }
    return Number(item.requested_qty || 0);
  }

  // 금회 출고수량은 수주번호 조회/선택 팝업에서 지정한다.
  // 출고 입력 본문에서는 지정된 수량을 확인만 하고 LOT를 배정한다.
  renderItems = function(){
    const body = byId('itemBody');
    if(!currentOrder || !currentOrder.items.length){
      body.innerHTML = '<tr><td colspan="13" class="empty">미출고 수주를 선택해 주세요.</td></tr>';
      return;
    }

    body.innerHTML = currentOrder.items.map(item => {
      const lots = allocationFor(item.id);
      const allocated = itemAllocatedQty(item.id);
      const target = requestedQty(item);
      const exact = target > 0 && Math.abs(allocated - target) <= 1e-9;
      const done = viewMode ? lots.length > 0 : exact;
      const statusText = viewMode ? '출고완료' : (target <= 0 ? '출고제외' : (exact ? '배정완료' : (allocated > 0 ? '배정중' : '배정대기')));
      const actionText = viewMode ? 'LOT 상세' : 'LOT 스캔';
      const actionClass = viewMode ? 'btn-light' : 'btn-primary';
      const shipQtyCell = viewMode ? fmt(allocated) : fmt(target);
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
        <td><strong>${shipQtyCell}</strong></td>
        <td>${lots.length} / ${fmt(allocated)}</td>
        <td>${viewMode ? '-' : `${fmt(item.waiting_qty)} / ${item.waiting_box_count} BOX`}</td>
        <td><button class="btn ${actionClass} lot-scan-btn" data-item-id="${item.id}" ${!viewMode && target <= 0 ? 'disabled' : ''}>${actionText}</button></td>
      </tr>`;
    }).join('');

    body.querySelectorAll('.lot-scan-btn').forEach(btn => {
      btn.addEventListener('click', () => openLotModal(Number(btn.dataset.itemId)));
    });
  };

  updateSummary = function(){
    if(!currentOrder) return;
    const orderQty = currentOrder.items.reduce((s,x)=>s+num(x.order_qty),0);
    const shippedQty = currentOrder.items.reduce((s,x)=>s+num(x.shipped_qty),0);
    const remainingQty = currentOrder.items.reduce((s,x)=>s+num(x.remaining_qty),0);
    const requestedTotal = currentOrder.items.reduce((s,x)=>s+requestedQty(x),0);
    const allocatedTotal = currentOrder.items.reduce((s,x)=>s+itemAllocatedQty(x.id),0);
    byId('sumOrderQty').textContent = fmt(orderQty);
    byId('sumShippedQty').textContent = fmt(shippedQty);
    byId('sumRemainingQty').textContent = fmt(remainingQty);
    byId('sumAllocatedQty').textContent = fmt(viewMode ? allocatedTotal : requestedTotal);
    if(viewMode){
      byId('shipmentStatus').value = viewingShipment?.status || 'CONFIRMED';
      byId('confirmBtn').disabled = true;
      return;
    }
    const targetItems = currentOrder.items.filter(item => requestedQty(item) > 0);
    const ready = targetItems.length > 0 && targetItems.every(item => Math.abs(itemAllocatedQty(item.id) - requestedQty(item)) <= 1e-9);
    byId('shipmentStatus').value = ready ? '출고 가능' : (allocatedTotal > 0 ? 'LOT 배정중' : 'LOT 배정 대기');
    byId('confirmBtn').disabled = !ready;
  };

  const baseOpenLotModal = openLotModal;
  openLotModal = function(itemId){
    const item = currentOrder?.items?.find(x => x.id === itemId);
    if(!viewMode && item && requestedQty(item) <= 0){
      alert('수주번호 조회에서 금회 출고수량을 먼저 입력해 주세요.');
      return;
    }
    baseOpenLotModal(itemId);
    if(item && !viewMode){
      byId('modalHelp').innerHTML = `※ 금회 출고 지정수량: <strong>${fmt(requestedQty(item))} ${esc(item.unit || 'EA')}</strong><br>※ 뒤 LOT를 스캔해도 선입 LOT부터 지정수량까지 완전 BOX 기준으로 자동 배정합니다.<br>※ 지정수량과 LOT 배정수량이 정확히 일치해야 출고 처리할 수 있습니다.`;
    }
  };

  scanLot = async function(){
    if(viewMode) return;
    const item = currentItem();
    if(!item) return;
    const target = requestedQty(item);
    if(target <= 0){
      byId('scanMessage').textContent = '수주번호 조회에서 금회 출고수량을 먼저 입력해 주세요.';
      byId('scanMessage').className = 'scan-msg error';
      return;
    }
    const lotNo = byId('lotScanInput').value.trim();
    if(!lotNo){
      byId('scanMessage').textContent = 'LOT 번호를 스캔해 주세요.';
      byId('scanMessage').className = 'scan-msg error';
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
          reserved_box_ids:reservedBoxIdsForItem(item),
          requested_qty:target
        })
      });
      allocations.set(item.id, Array.isArray(data.allocations) ? data.allocations : []);
      const autoCount = Number(data.auto_added_count || 0);
      const exact = data.is_full_allocated === true;
      byId('scanMessage').textContent = exact
        ? `FIFO 자동배정 완료: ${autoCount} BOX 추가 / ${fmt(data.allocated_qty)} ${item.unit}`
        : `FIFO 자동배정: ${autoCount} BOX 추가 / ${fmt(data.allocated_qty)} / 지정 ${fmt(target)} ${item.unit}`;
      byId('scanMessage').className = exact ? 'scan-msg ok' : 'scan-msg';
      byId('lotScanInput').value = '';
      renderAllocatedLots();
      renderItems();
      updateSummary();
    }catch(e){
      byId('scanMessage').textContent = e.message;
      byId('scanMessage').className = 'scan-msg error';
      byId('lotScanInput').select();
    }
    byId('lotScanInput').focus();
  };

  confirmShipment = async function(){
    if(viewMode) return;
    if(!currentOrder) return alert('미출고 수주를 선택해 주세요.');
    const targetItems = currentOrder.items.filter(item => requestedQty(item) > 0);
    if(!targetItems.length) return alert('수주번호 조회에서 금회 출고수량을 1개 품목 이상 입력해 주세요.');

    const notReady = targetItems.find(item => Math.abs(itemAllocatedQty(item.id) - requestedQty(item)) > 1e-9);
    if(notReady){
      return alert(`${notReady.order_no || ''} / ${notReady.part_no}: 금회 출고수량 ${fmt(requestedQty(notReady))}과 LOT 배정수량 ${fmt(itemAllocatedQty(notReady.id))}이 일치하지 않습니다.`);
    }

    const items = targetItems.map(item => ({
      sales_order_item_id:item.id,
      packing_box_ids:allocationFor(item.id).map(x=>x.id),
      requested_qty:requestedQty(item)
    }));
    const orderCount = currentOrder.order_ids?.length || 1;
    const totalRequested = targetItems.reduce((s,item)=>s+requestedQty(item),0);
    if(!confirm(`수주 ${orderCount}건 / 품목 ${items.length}개 / 총 ${fmt(totalRequested)}을 1건의 출고전표로 처리하시겠습니까?`)) return;

    byId('confirmBtn').disabled = true;
    try{
      const data = await getJson('/api/sales/shipping-entry/confirm', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({shipment_date:byId('shipmentDate').value, items, note:byId('shipmentNote').value.trim() || null})
      });
      alert(`${data.message}\n출고번호: ${data.shipment_no}\n수주건수: ${data.order_count || orderCount}\n품목수: ${data.item_count}\n출고수량: ${fmt(data.total_qty)}`);
      await loadShipmentByNo(data.shipment_no);
    }catch(e){
      alert(e.message);
      updateSummary();
    }
  };

  document.addEventListener('DOMContentLoaded', () => {
    const hiddenSelect = byId('orderSelect');
    if(hiddenSelect) hiddenSelect.style.display = 'none';

    byId('orderLookupBtn')?.addEventListener('click', openOrderLookup);
    byId('orderNoInput')?.addEventListener('keydown', e => {
      if(e.key === 'Enter'){
        e.preventDefault();
        openOrderLookup();
      }
    });
    byId('orderLookupSearch')?.addEventListener('click', renderOrderLookup);
    byId('orderLookupClose')?.addEventListener('click', closeOrderLookup);
    byId('orderLookupChoose')?.addEventListener('click', applyCheckedOrders);
    ['orderLookupNo','orderLookupPart','orderLookupCustomer'].forEach(id => byId(id)?.addEventListener('keydown', e => {
      if(e.key === 'Enter'){
        e.preventDefault();
        renderOrderLookup();
      }
    }));
    byId('orderLookupModal')?.addEventListener('click', e => {
      if(e.target === byId('orderLookupModal')) closeOrderLookup();
    });

    hiddenSelect?.addEventListener('change', () => setTimeout(() => {
      if(currentOrder?.items) currentOrder.items.forEach(item => { if(item.requested_qty == null) item.requested_qty = Number(item.remaining_qty || 0); });
      syncVisibleOrderNo();
      renderItems();
      updateSummary();
    }, 0));
    byId('newEntryBtn')?.addEventListener('click', () => setTimeout(syncVisibleOrderNo, 0));
    byId('shipmentLookupModal')?.addEventListener('click', e => {
      if(e.target.closest('.choose-shipment')) setTimeout(syncVisibleOrderNo, 100);
    });

    setTimeout(() => {
      syncVisibleOrderNo();
      if(currentOrder?.items) currentOrder.items.forEach(item => { if(item.requested_qty == null) item.requested_qty = Number(item.remaining_qty || 0); });
      renderItems();
      updateSummary();
    }, 300);
  });
})();