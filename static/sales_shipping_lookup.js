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

  function renderOrderLookup(){
    const body = byId('orderLookupBody');
    if(!body) return;
    const rows = (typeof orders !== 'undefined' ? orders : []).filter(orderMatches);
    const existing = new Set(typeof selectedOrderIds !== 'undefined' ? selectedOrderIds : []);
    body.innerHTML = rows.length ? rows.map(row => {
      const partNos = (row.items || []).map(x => x.part_no).join(', ');
      const remain = (row.items || []).reduce((s,x) => s + Number(x.remaining_qty || 0), 0);
      return `<tr class="lookup-result-row" data-id="${row.id}">
        <td><input type="checkbox" class="order-lookup-check" value="${row.id}" data-customer-id="${row.customer_id}" ${existing.has(row.id) ? 'checked' : ''}></td>
        <td>${escLookup(row.order_no)}</td>
        <td class="left">${escLookup(row.customer_name)}</td>
        <td>${escLookup(row.order_date || '')}</td>
        <td>${escLookup(row.delivery_due_date || '')}</td>
        <td>${escLookup(row.status || '')}</td>
        <td class="left">${escLookup(partNos)}</td>
        <td>${fmtLookup(remain)}</td>
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
    byId('orderLookupCount').textContent = `조회 : ${rows.length}건`;
    updateSelectedCount();
  }

  function applyCheckedOrders(){
    const ids = checkedOrderIds();
    if(!ids.length){
      alert('출고할 수주를 1건 이상 선택해 주세요.');
      return;
    }
    try{
      if(typeof applySelectedOrders !== 'function') throw new Error('수주 선택 기능을 불러오지 못했습니다.');
      applySelectedOrders(ids);
      syncVisibleOrderNo();
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

    hiddenSelect?.addEventListener('change', () => setTimeout(syncVisibleOrderNo, 0));
    byId('newEntryBtn')?.addEventListener('click', () => setTimeout(syncVisibleOrderNo, 0));
    byId('shipmentLookupModal')?.addEventListener('click', e => {
      if(e.target.closest('.choose-shipment')) setTimeout(syncVisibleOrderNo, 100);
    });

    setTimeout(syncVisibleOrderNo, 300);
  });
})();
