(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let vendor = null;
  let lines = [];
  let vendorSearchId = 0;
  let itemSearchId = 0;
  let saving = false;
  const today = () => {
    const now = new Date();
    return [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-');
  };
  const cell = (row, value) => {
    const td = row.insertCell();
    td.textContent = value == null ? '' : String(value);
    return td;
  };
  const action = (label, handler) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'po-btn';
    button.textContent = label;
    button.addEventListener('click', handler);
    return button;
  };
  const notice = value => { $('po-message').textContent = value; };
  async function getJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(row => row.msg).join(' / ') : data.detail;
      throw new Error(detail || '요청에 실패했습니다.');
    }
    return data;
  }
  function resetOrder() {
    $('po-form').reset();
    $('po-date').value = today();
    $('po-number').value = '저장 시 자동 발번';
    $('po-save').disabled = false;
    $('po-vendor-query').value = '';
    $('po-vendor-results').replaceChildren();
    $('po-selected-vendor').textContent = '선택된 거래처 없음';
    $('po-item-picker').hidden = true;
    $('po-item-results').replaceChildren();
    $('po-lines').replaceChildren();
    const empty = document.createElement('tr');
    cell(empty, '품목을 추가하세요.').colSpan = 8;
    $('po-lines').append(empty);
    vendor = null;
    lines = [];
    notice('');
  }
  async function searchVendors() {
    const id = ++vendorSearchId;
    const keyword = $('po-vendor-query').value.trim();
    const box = $('po-vendor-results');
    box.textContent = '검색 중…';
    try {
      const data = await getJson('/api/purchase/vendors/search?' + new URLSearchParams({keyword, limit: '30'}));
      if (id !== vendorSearchId) return;
      box.replaceChildren();
      if (!data.items.length) { box.textContent = '검색 결과가 없습니다.'; return; }
      data.items.forEach(row => {
        box.append(action(row.partner_code + ' · ' + row.partner_name, () => {
          vendor = row;
          $('po-vendor-query').value = row.partner_code + ' · ' + row.partner_name;
          $('po-selected-vendor').textContent = '선택: ' + row.partner_name;
          if (!$('po-manager').value.trim()) $('po-manager').value = row.manager_name || '';
          box.replaceChildren();
        }));
      });
      if (data.total > data.items.length) {
        const more = document.createElement('div');
        more.className = 'po-muted';
        more.textContent = '결과가 더 있습니다. 검색어를 구체적으로 입력하세요.';
        box.append(more);
      }
    } catch (error) {
      if (id === vendorSearchId) box.textContent = error.message;
    }
  }
  async function searchItems() {
    const id = ++itemSearchId;
    const body = $('po-item-results');
    body.replaceChildren();
    const keyword = $('po-item-query').value.trim();
    try {
      const data = await getJson('/api/purchase/items/search?' + new URLSearchParams({keyword, limit: '100'}));
      if (id !== itemSearchId) return;
      if (!data.items.length) {
        const tr = body.insertRow(); cell(tr, '검색 결과가 없습니다.').colSpan = 5; return;
      }
      data.items.forEach(item => {
        const tr = body.insertRow();
        [item.part_no, item.part_name, item.spec, item.unit].forEach(value => cell(tr, value));
        cell(tr, '').append(action('추가', () => addLine(item)));
      });
      if (data.total > data.items.length) {
        const tr = body.insertRow();
        cell(tr, '결과가 더 있습니다. 검색어를 구체적으로 입력하세요.').colSpan = 5;
      }
    } catch (error) {
      if (id === itemSearchId) { const tr = body.insertRow(); cell(tr, error.message).colSpan = 5; }
    }
  }
  function addLine(item) {
    if (!lines.length) $('po-lines').replaceChildren();
    const tr = document.createElement('tr');
    [item.part_no, item.part_name, item.spec, item.unit].forEach(value => cell(tr, value));
    const qty = document.createElement('input');
    qty.type = 'number'; qty.min = '0.000001'; qty.step = 'any'; qty.required = true; qty.setAttribute('aria-label', '발주수량');
    const date = document.createElement('input');
    date.type = 'date'; date.value = $('po-requested-date').value; date.setAttribute('aria-label', '품목 납기일');
    const note = document.createElement('input');
    note.type = 'text'; note.maxLength = 500; note.setAttribute('aria-label', '품목 비고');
    cell(tr, '').append(qty); cell(tr, '').append(date); cell(tr, '').append(note);
    const line = {item, tr, qty, date, note};
    cell(tr, '').append(action('삭제', () => {
      lines = lines.filter(entry => entry !== line);
      tr.remove();
      if (!lines.length) {
        const empty = $('po-lines').insertRow();
        cell(empty, '품목을 추가하세요.').colSpan = 8;
      }
    }));
    lines.push(line);
    $('po-lines').append(tr);
    qty.focus();
  }
  async function saveOrder(event) {
    event.preventDefault();
    if (saving || $('po-save').disabled) return;
    if (!vendor) { notice('거래처 검색 결과에서 거래처를 선택하세요.'); return; }
    if (!lines.length) { notice('발주 품목을 추가하세요.'); return; }
    if (lines.some(line => !Number.isFinite(Number(line.qty.value)) || Number(line.qty.value) <= 0)) {
      notice('모든 품목의 발주수량을 0보다 크게 입력하세요.'); return;
    }
    const payload = {
      order_date: $('po-date').value,
      delivery_due_date: $('po-requested-date').value || null,
      partner_id: vendor.id,
      partner_name: vendor.partner_name,
      manager_name: $('po-manager').value.trim() || null,
      note: $('po-note').value.trim() || null,
      items: lines.map(line => ({
        part_no: line.item.part_no,
        order_qty: Number(line.qty.value),
        delivery_date: line.date.value || null,
        note: line.note.value.trim() || null
      }))
    };
    saving = true;
    $('po-save').disabled = true;
    notice('저장 중…');
    try {
      const result = await getJson('/api/purchase/orders', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      $('po-number').value = result.po_no;
      notice(result.po_no + ' 발주가 저장되었습니다. 새 발주는 신규를 누르세요.');
      loadOrders();
    } catch (error) {
      $('po-save').disabled = false;
      notice(error.message + ' 통신 오류라면 목록에서 저장 여부를 먼저 확인하세요.');
    } finally { saving = false; }
  }
  async function loadOrders() {
    const body = $('po-list');
    body.replaceChildren();
    try {
      const supplier = $('po-list-query').value.trim();
      const data = await getJson('/api/purchase/orders?' + new URLSearchParams({supplier, limit: '1000'}));
      if (!data.data.length) { const tr = body.insertRow(); cell(tr, '등록된 발주가 없습니다.').colSpan = 10; return; }
      data.data.forEach(order => {
        const tr = body.insertRow();
        [order.po_no, order.order_date, order.due_date, order.supplier_name, order.manager_name,
         order.part_no, order.part_name, order.order_qty, order.delivery_date, order.status].forEach(value => cell(tr, value));
      });
    } catch (error) {
      const tr = body.insertRow(); cell(tr, error.message).colSpan = 10;
    }
  }
  $('po-vendor-query').addEventListener('input', () => {
    vendorSearchId++;
    vendor = null;
    $('po-vendor-results').replaceChildren();
    $('po-selected-vendor').textContent = '검색 결과에서 거래처를 선택하세요.';
  });
  $('po-vendor-query').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); searchVendors(); }
  });
  $('po-vendor-search').addEventListener('click', searchVendors);
  $('po-item-toggle').addEventListener('click', () => {
    $('po-item-picker').hidden = !$('po-item-picker').hidden;
    if (!$('po-item-picker').hidden) { $('po-item-query').focus(); searchItems(); }
  });
  $('po-item-query').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); searchItems(); }
  });
  $('po-item-query').addEventListener('input', () => { itemSearchId++; $('po-item-results').replaceChildren(); });
  $('po-item-search').addEventListener('click', searchItems);
  $('po-new').addEventListener('click', resetOrder);
  $('po-form').addEventListener('submit', saveOrder);
  $('po-list-search').addEventListener('click', loadOrders);
  $('po-list-query').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); loadOrders(); }
  });
  resetOrder();
  loadOrders();
})();

