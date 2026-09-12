(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let vendor = null;
  let vendorVersion = 0;
  let vendorTimer = null;
  let rows = [];
  let saving = false;

  const today = () => {
    const d = new Date();
    return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
  };
  const cell = (tr, value = '') => {
    const td = tr.insertCell();
    td.textContent = value == null ? '' : String(value);
    return td;
  };
  const button = (text, fn, className = 'po-btn') => {
    const node = document.createElement('button');
    node.type = 'button'; node.className = className; node.textContent = text;
    node.addEventListener('click', fn);
    return node;
  };
  const input = (type, label) => {
    const node = document.createElement('input');
    node.type = type; node.setAttribute('aria-label', label);
    return node;
  };
  const message = text => { $('po-message').textContent = text; };
  async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(x => x.msg).join(' / ') : data.detail;
      throw new Error(detail || '처리하지 못했습니다.');
    }
    return data;
  }
  function showSuggestions(box, items, label, choose) {
    box.replaceChildren();
    items.forEach(item => box.append(button(label(item), () => choose(item))));
  }
  function exactVendor(items, query) {
    const value = query.trim().toLocaleLowerCase();
    return items.find(x => x.partner_code.toLocaleLowerCase() === value) ||
      (items.filter(x => x.partner_name.toLocaleLowerCase() === value).length === 1
        ? items.find(x => x.partner_name.toLocaleLowerCase() === value) : null);
  }
  function selectVendor(item) {
    vendor = item;
    $('po-vendor-query').value = item.partner_code + ' · ' + item.partner_name;
    $('po-vendor-results').replaceChildren();
    $('po-vendor-hint').textContent = item.partner_code + ' / ' + item.partner_name;
    if (!$('po-manager').value.trim()) $('po-manager').value = item.manager_name || '';
  }
  async function lookupVendor(query, version, autoOnly = false) {
    if (!query.trim()) return null;
    const data = await request('/api/purchase/vendors/search?' + new URLSearchParams({keyword: query.trim(), limit: '100'}));
    if (version !== vendorVersion) return null;
    const exact = exactVendor(data.items, query);
    if (exact) { selectVendor(exact); return exact; }
    if (!autoOnly) {
      showSuggestions($('po-vendor-results'), data.items,
        item => item.partner_code + ' · ' + item.partner_name, selectVendor);
      $('po-vendor-hint').textContent = data.items.length
        ? '검색 결과를 선택하세요.' : '일치하는 공급사 거래처가 없습니다.';
    }
    return null;
  }
  function searchVendorSoon() {
    clearTimeout(vendorTimer);
    vendor = null;
    const version = ++vendorVersion;
    $('po-vendor-results').replaceChildren();
    $('po-vendor-hint').textContent = '거래처 검색 중…';
    const query = $('po-vendor-query').value;
    if (!query.trim()) { $('po-vendor-hint').textContent = '거래처 코드 또는 이름을 입력하세요.'; return; }
    vendorTimer = setTimeout(() => lookupVendor(query, version).catch(err => {
      if (version === vendorVersion) $('po-vendor-hint').textContent = err.message;
    }), 250);
  }
  function exactPart(items, query) {
    const value = query.trim().toLocaleLowerCase();
    return items.find(x => x.part_no.toLocaleLowerCase() === value) ||
      (items.filter(x => x.part_name.toLocaleLowerCase() === value).length === 1
        ? items.find(x => x.part_name.toLocaleLowerCase() === value) : null);
  }
  function selectPart(row, part) {
    row.part = part;
    row.query.value = part.part_no;
    row.name.value = part.part_name || '';
    row.spec.value = part.spec || '';
    row.unit.value = part.unit || '';
    if (!row.date.value) row.date.value = $('po-requested-date').value;
    row.suggestions.replaceChildren();
    row.status.textContent = '';
    if (rows[rows.length - 1] === row) addRow();
  }
  async function lookupPart(row, query, version, autoOnly = false) {
    if (!query.trim()) return null;
    const data = await request('/api/purchase/items/search?' + new URLSearchParams({keyword: query.trim(), limit: '100'}));
    if (version !== row.version || !rows.includes(row)) return null;
    const exact = exactPart(data.items, query);
    if (exact) { selectPart(row, exact); return exact; }
    if (!autoOnly) {
      showSuggestions(row.suggestions, data.items,
        item => [item.part_no, item.part_name, item.spec].filter(Boolean).join(' · '),
        item => selectPart(row, item));
      row.status.textContent = data.items.length
        ? '검색 결과에서 품목을 선택하세요.' : '품목 마스터에 일치하는 자료가 없습니다.';
    }
    return null;
  }
  function renumber() { rows.forEach((row, index) => { row.seq.textContent = String(index + 1); }); }
  function addRow() {
    const tr = document.createElement('tr');
    const seq = cell(tr, String(rows.length + 1)); seq.className = 'po-seq';
    const query = input('text', '품번 또는 품명 검색');
    query.autocomplete = 'off'; query.placeholder = '품번 / 품명';
    const suggestions = document.createElement('div'); suggestions.className = 'po-suggestions';
    const status = document.createElement('div'); status.className = 'po-row-status';
    const searchCell = cell(tr); searchCell.append(query, suggestions, status);
    const name = input('text', '품명'); name.readOnly = true; cell(tr).append(name);
    const spec = input('text', '규격'); spec.readOnly = true; cell(tr).append(spec);
    const unit = input('text', '단위'); unit.readOnly = true; cell(tr).append(unit);
    const qty = input('number', '발주수량'); qty.min = '0.000001'; qty.step = 'any'; cell(tr).append(qty);
    const date = input('date', '품목 납기일'); cell(tr).append(date);
    const note = input('text', '품목 비고'); note.maxLength = 500; cell(tr).append(note);
    const row = {tr, seq, query, suggestions, status, name, spec, unit, qty, date, note,
      part: null, version: 0, timer: null};
    cell(tr).append(button('×', () => {
      clearTimeout(row.timer);
      row.version++;
      rows = rows.filter(x => x !== row);
      tr.remove(); renumber();
      if (!rows.length) addRow();
    }, 'po-remove'));
    query.addEventListener('input', () => {
      clearTimeout(row.timer);
      row.version++;
      row.part = null;
      name.value = ''; spec.value = ''; unit.value = '';
      suggestions.replaceChildren(); status.textContent = '';
      const value = query.value.trim(), version = row.version;
      if (value) row.timer = setTimeout(() => lookupPart(row, value, version).catch(err => {
        if (version === row.version) status.textContent = err.message;
      }), 250);
    });
    rows.push(row);
    $('po-lines').append(tr);
    return row;
  }
  function activeRows() {
    return rows.filter(row => row.query.value.trim() || row.qty.value || row.date.value || row.note.value.trim());
  }
  function resetOrder() {
    $('po-form').reset();
    $('po-date').value = today();
    $('po-number').value = '저장 시 자동 발번';
    $('po-state').value = '발주완료';
    $('po-save').disabled = false;
    $('po-vendor-results').replaceChildren();
    $('po-vendor-hint').textContent = '등록된 공급사 거래처에서 조회합니다.';
    $('po-lines').replaceChildren();
    clearTimeout(vendorTimer); vendorVersion++;
    rows.forEach(row => clearTimeout(row.timer));
    rows = []; vendor = null;
    for (let n = 0; n < 3; n++) addRow();
    message('');
  }
  async function resolveEntries(used) {
    if (!vendor) {
      const query = $('po-vendor-query').value;
      await lookupVendor(query, vendorVersion, true);
      if (!vendor) throw new Error('거래처 코드 또는 이름을 확인하고 검색 결과에서 선택하세요.');
    }
    for (const row of used) {
      if (!row.part) await lookupPart(row, row.query.value, row.version, true);
      if (!row.part) throw new Error((row.seq.textContent || '') + '행의 품번이 품목 마스터에 없습니다.');
      if (!row.qty.value || !Number.isFinite(Number(row.qty.value)) || Number(row.qty.value) <= 0)
        throw new Error(row.seq.textContent + '행의 발주수량을 입력하세요.');
    }
  }
  async function saveOrder(event) {
    event.preventDefault();
    if (saving || $('po-save').disabled) return;
    const used = activeRows();
    if (!used.length) { message('품목을 한 행 이상 입력하세요.'); return; }
    saving = true;
    $('po-save').disabled = true;
    message('확인 중…');
    try {
      await resolveEntries(used);
      const payload = {
        order_date: $('po-date').value,
        delivery_due_date: $('po-requested-date').value || null,
        partner_id: vendor.id,
        partner_name: vendor.partner_name,
        manager_name: $('po-manager').value.trim() || null,
        note: $('po-note').value.trim() || null,
        items: used.map(row => ({
          part_no: row.part.part_no, order_qty: Number(row.qty.value),
          delivery_date: row.date.value || null, note: row.note.value.trim() || null
        }))
      };
      message('저장 중…');
      const data = await request('/api/purchase/orders', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      $('po-number').value = data.po_no;
      message(data.po_no + ' 저장되었습니다. 새 발주는 신규를 누르세요.');
      loadOrders();
    } catch (error) {
      $('po-save').disabled = false;
      message(error.message + ' 통신 오류라면 목록에서 저장 여부를 먼저 확인하세요.');
    } finally { saving = false; }
  }
  async function loadOrders() {
    const body = $('po-list'); body.replaceChildren();
    try {
      const supplier = $('po-list-query').value.trim();
      const data = await request('/api/purchase/orders?' + new URLSearchParams({supplier, limit: '1000'}));
      if (!data.data.length) {
        const tr = body.insertRow(); cell(tr, '등록된 발주가 없습니다.').colSpan = 10; return;
      }
      data.data.forEach(order => {
        const tr = body.insertRow();
        [order.po_no, order.order_date, order.due_date, order.supplier_name, order.manager_name,
          order.part_no, order.part_name, order.order_qty, order.delivery_date, order.status].forEach(value => cell(tr, value));
      });
    } catch (error) {
      const tr = body.insertRow(); cell(tr, error.message).colSpan = 10;
    }
  }
  $('po-vendor-query').addEventListener('input', searchVendorSoon);
  $('po-add-row').addEventListener('click', () => addRow().query.focus());
  $('po-new').addEventListener('click', resetOrder);
  $('po-form').addEventListener('submit', saveOrder);
  $('po-list-search').addEventListener('click', loadOrders);
  $('po-list-query').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); loadOrders(); }
  });
  resetOrder();
  loadOrders();
})();

