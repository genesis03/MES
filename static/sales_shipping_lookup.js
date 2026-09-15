(() => {
  const byId = (id) => document.getElementById(id);
  const escLookup = (v) => String(v ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[s]));
  const fmtLookup = (v) => Number(v || 0).toLocaleString('ko-KR', {maximumFractionDigits: 3});

  function syncVisibleOrderNo(){
    const input = byId('orderNoInput');
    if(!input) return;
    input.value = (typeof currentOrder !== 'undefined' && currentOrder?.order_no) ? currentOrder.order_no : '';
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

  function renderOrderLookup(){
    const body = byId('orderLookupBody');
    if(!body) return;
    const rows = (typeof orders !== 'undefined' ? orders : []).filter(orderMatches);
    body.innerHTML = rows.length ? rows.map(row => {
      const partNos = (row.items || []).map(x => x.part_no).join(', ');
      const remain = (row.items || []).reduce((s,x) => s + Number(x.remaining_qty || 0), 0);
      return `<tr class="lookup-result-row" data-id="${row.id}">
        <td>${escLookup(row.order_no)}</td>
        <td class="left">${escLookup(row.customer_name)}</td>
        <td>${escLookup(row.order_date || '')}</td>
        <td>${escLookup(row.delivery_due_date || '')}</td>
        <td>${escLookup(row.status || '')}</td>
        <td class="left">${escLookup(partNos)}</td>
        <td>${fmtLookup(remain)}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="7" class="empty">조회된 미출고 수주가 없습니다.</td></tr>';

    body.querySelectorAll('.lookup-result-row').forEach(tr => {
      tr.addEventListener('dblclick', () => chooseOrder(Number(tr.dataset.id)));
      tr.addEventListener('click', () => {
        body.querySelectorAll('.lookup-result-row').forEach(x => x.classList.remove('row-selected'));
        tr.classList.add('row-selected');
        byId('orderLookupChoose').dataset.id = tr.dataset.id;
      });
    });
    byId('orderLookupCount').textContent = `건수 : ${rows.length}`;
  }

  function chooseOrder(id){
    const select = byId('orderSelect');
    if(!select || !id) return;
    select.value = String(id);
    select.dispatchEvent(new Event('change', {bubbles:true}));
    syncVisibleOrderNo();
    closeOrderLookup();
  }

  function openOrderLookup(){
    if(typeof viewMode !== 'undefined' && viewMode) return;
    byId('orderLookupNo').value = byId('orderNoInput').value.trim();
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
    byId('orderLookupChoose')?.addEventListener('click', e => chooseOrder(Number(e.currentTarget.dataset.id || 0)));
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