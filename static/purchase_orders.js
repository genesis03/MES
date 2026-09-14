(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let vendor = null;
  let vendorVersion = 0;
  let vendorTimer = null;
  let rows = [];
  let saving = false;
  let editingId = null;

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
  const selectFromTemplate = (templateId, label, value = '') => {
    const source = $(templateId);
    const node = document.createElement('select');
    node.setAttribute('aria-label', label);
    [...source.options].forEach(option => node.append(option.cloneNode(true)));
    node.value = value || '';
    if (!node.value && node.options.length === 2) node.selectedIndex = 1;
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
    $('po-vendor-query').value = item.partner_code ? item.partner_code + ' · ' + item.partner_name : item.partner_name;
    $('po-vendor-results').replaceChildren();
    $('po-vendor-hint').textContent = (item.partner_code ? item.partner_code + ' / ' : '') + item.partner_name;
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
      $('po-vendor-hint').textContent = data.items.length ? '검색 결과를 선택하세요.' : '일치하는 공급사 거래처가 없습니다.';
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
      row.status.textContent = data.items.length ? '검색 결과에서 품목을 선택하세요.' : '품목 마스터에 일치하는 자료가 없습니다.';
    }
    return null;
  }

  function renumber() { rows.forEach((row, index) => { row.seq.textContent = String(index + 1); }); }
  function addRow(item = null) {
    const tr = document.createElement('tr');
    const seq = cell(tr, String(rows.length + 1)); seq.className = 'po-seq';
    const query = input('text', '품번 또는 품명 검색'); query.autocomplete = 'off'; query.placeholder = '품번 / 품명';
    const suggestions = document.createElement('div'); suggestions.className = 'po-suggestions';
    const status = document.createElement('div'); status.className = 'po-row-status';
    const searchCell = cell(tr); searchCell.append(query, suggestions, status);
    const name = input('text', '품명'); name.readOnly = true; cell(tr).append(name);
    const spec = input('text', '규격'); spec.readOnly = true; cell(tr).append(spec);
    const unit = input('text', '단위'); unit.readOnly = true; cell(tr).append(unit);
    const qty = input('number', '발주수량'); qty.min = '0.000001'; qty.step = 'any'; cell(tr).append(qty);
    const warehouse = selectFromTemplate('po-warehouse-template', '입고창고', item?.warehouse_code || ''); cell(tr).append(warehouse);
    const location = selectFromTemplate('po-location-template', '저장위치', item?.storage_location || ''); cell(tr).append(location);
    const date = input('date', '품목 납기일'); cell(tr).append(date);
    const note = input('text', '품목 비고'); note.maxLength = 500; cell(tr).append(note);
    const row = {tr, seq, query, suggestions, status, name, spec, unit, qty, warehouse, location, date, note,
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
    if (item) {
      row.part = {part_no:item.part_no, part_name:item.part_name || '', spec:item.spec || '', unit:item.unit || ''};
      query.value = item.part_no || '';
      name.value = item.part_name || '';
      spec.value = item.spec || '';
      unit.value = item.unit || '';
      qty.value = item.order_qty ?? '';
      warehouse.value = item.warehouse_code || '';
      location.value = item.storage_location || '';
      date.value = item.delivery_date || '';
      note.value = item.note || '';
    }
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
    rows = []; vendor = null; editingId = null;
    for (let n = 0; n < 3; n++) addRow();
    message('');
    history.replaceState(null, '', '/purchase/orders');
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
      if (!row.warehouse.value) throw new Error(row.seq.textContent + '행의 입고창고를 선택하세요.');
      if (!row.location.value) throw new Error(row.seq.textContent + '행의 저장위치를 선택하세요.');
    }
  }

  async function saveOrder(event) {
    event.preventDefault();
    if (saving || $('po-save').disabled) return;
    const used = activeRows();
    if (!used.length) { message('품목을 한 행 이상 입력하세요.'); return; }
    saving = true; $('po-save').disabled = true; message('확인 중…');
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
          part_no: row.part.part_no,
          order_qty: Number(row.qty.value),
          warehouse_code: row.warehouse.value,
          storage_location: row.location.value,
          delivery_date: row.date.value || null,
          note: row.note.value.trim() || null
        }))
      };
      const wasEdit = Boolean(editingId);
      message(wasEdit ? '수정 저장 중…' : '저장 중…');
      const data = await request(wasEdit ? '/api/purchase/orders/' + editingId : '/api/purchase/orders', {
        method: wasEdit ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
      });
      editingId = data.id;
      $('po-number').value = data.po_no;
      $('po-state').value = data.status === 'ORDERED' ? '발주완료' : data.status;
      message(data.po_no + (wasEdit ? ' 수정되었습니다.' : ' 저장되었습니다.'));
    } catch (error) {
      message(error.message + ' 통신 오류라면 발주 조회에서 저장 여부를 먼저 확인하세요.');
    } finally { saving = false; $('po-save').disabled = false; }
  }

  async function loadOrder(id) {
    try {
      const data = await request('/api/purchase/orders/' + id);
      $('po-lines').replaceChildren();
      rows.forEach(row => clearTimeout(row.timer));
      rows = [];
      editingId = data.id;
      vendor = {id:data.partner_id, partner_name:data.partner_name, partner_code:'', manager_name:data.manager_name || ''};
      $('po-date').value = data.order_date;
      $('po-requested-date').value = data.delivery_due_date || '';
      $('po-vendor-query').value = data.partner_name;
      $('po-vendor-hint').textContent = '불러온 거래처: ' + data.partner_name;
      $('po-manager').value = data.manager_name || '';
      $('po-number').value = data.po_no;
      $('po-state').value = data.status === 'ORDERED' ? '발주완료' : data.status;
      $('po-note').value = data.note || '';
      (data.items || []).forEach(item => addRow(item));
      if (!rows.length) addRow();
      $('po-save').disabled = !data.editable;
      message(data.editable ? data.po_no + ' 수정 모드입니다.' : data.po_no + '은 입고 이력이 있어 직접 수정할 수 없습니다.');
    } catch (error) { message(error.message); }
  }

  function init() {
    $('po-vendor-query').addEventListener('input', searchVendorSoon);
    $('po-add-row').addEventListener('click', () => addRow().query.focus());
    $('po-new').addEventListener('click', resetOrder);
    $('po-form').addEventListener('submit', saveOrder);
    resetOrder();
    const editId = Number(new URLSearchParams(location.search).get('edit'));
    if (Number.isInteger(editId) && editId > 0) loadOrder(editId);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
