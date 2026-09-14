(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let vendor = null;
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

  const input = (type, label) => {
    const node = document.createElement('input');
    node.type = type;
    node.setAttribute('aria-label', label);
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
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(x => x.msg).join(' / ') : data.detail;
      throw new Error(detail || '처리하지 못했습니다.');
    }
    return data;
  }

  function vendorOptions() {
    return [...$('po-vendor-options').options].map(option => ({
      id: Number(option.dataset.id),
      partner_code: option.dataset.code || '',
      partner_name: option.dataset.name || '',
      manager_name: option.dataset.manager || '',
      value: option.value
    }));
  }

  function resolveVendorFromInput() {
    const query = $('po-vendor-query').value.trim().toLocaleLowerCase();
    $('po-vendor-query').classList.remove('po-vendor-selected');
    vendor = null;
    if (!query) {
      $('po-vendor-hint').textContent = '글자를 입력한 뒤 목록에서 거래처를 선택하세요.';
      return;
    }
    const matches = vendorOptions().filter(item =>
      item.value.toLocaleLowerCase() === query ||
      item.partner_code.toLocaleLowerCase() === query ||
      item.partner_name.toLocaleLowerCase() === query
    );
    if (matches.length === 1) {
      vendor = matches[0];
      $('po-vendor-query').value = vendor.value;
      $('po-vendor-query').classList.add('po-vendor-selected');
      $('po-vendor-hint').textContent = `선택됨: ${vendor.partner_code} / ${vendor.partner_name}`;
      if (!$('po-manager').value.trim()) $('po-manager').value = vendor.manager_name || '';
    } else {
      $('po-vendor-hint').textContent = '목록에서 거래처를 선택하세요.';
    }
  }

  function showPartSuggestions(row, items) {
    row.suggestions.replaceChildren();
    items.forEach(part => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = [part.part_no, part.part_name, part.spec].filter(Boolean).join(' · ');
      const choose = event => {
        event.preventDefault();
        event.stopPropagation();
        selectPart(row, part);
      };
      btn.addEventListener('mousedown', choose);
      btn.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') choose(event);
      });
      row.suggestions.append(btn);
    });
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
  }

  async function lookupPart(row, query, version, autoOnly = false) {
    if (!query.trim()) return null;
    const data = await request('/api/purchase/items/search?' + new URLSearchParams({keyword: query.trim(), limit: '100'}));
    if (version !== row.version || !rows.includes(row)) return null;
    const value = query.trim().toLocaleLowerCase();
    const exact = data.items.find(x => x.part_no.toLocaleLowerCase() === value) ||
      (data.items.filter(x => x.part_name.toLocaleLowerCase() === value).length === 1
        ? data.items.find(x => x.part_name.toLocaleLowerCase() === value) : null);
    if (exact) {
      selectPart(row, exact);
      return exact;
    }
    if (!autoOnly) {
      showPartSuggestions(row, data.items || []);
      row.status.textContent = data.items?.length ? '검색 결과에서 품목을 선택하세요.' : '품목 마스터에 일치하는 자료가 없습니다.';
    }
    return null;
  }

  function renumber() {
    rows.forEach((row, index) => { row.seq.textContent = String(index + 1); });
  }

  function addRow(item = null, focus = false) {
    const tr = document.createElement('tr');
    const seq = cell(tr, String(rows.length + 1));
    seq.className = 'po-seq';

    const query = input('text', '품번 또는 품명 검색');
    query.autocomplete = 'off';
    query.placeholder = '품번 / 품명';
    const suggestions = document.createElement('div');
    suggestions.className = 'po-suggestions';
    const status = document.createElement('div');
    status.className = 'po-row-status';
    const searchCell = cell(tr);
    searchCell.append(query, suggestions, status);

    const name = input('text', '품명'); name.readOnly = true; cell(tr).append(name);
    const spec = input('text', '규격'); spec.readOnly = true; cell(tr).append(spec);
    const unit = input('text', '단위'); unit.readOnly = true; cell(tr).append(unit);
    const qty = input('number', '발주수량'); qty.min = '0.000001'; qty.step = 'any'; cell(tr).append(qty);
    const warehouse = selectFromTemplate('po-warehouse-template', '입고창고', item?.warehouse_code || ''); cell(tr).append(warehouse);
    const location = selectFromTemplate('po-location-template', '저장위치', item?.storage_location || ''); cell(tr).append(location);
    const date = input('date', '품목 납기일'); cell(tr).append(date);
    const note = input('text', '품목 비고'); note.maxLength = 500; cell(tr).append(note);

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'po-remove';
    remove.textContent = '×';
    cell(tr).append(remove);

    const row = {tr, seq, query, suggestions, status, name, spec, unit, qty, warehouse, location, date, note, part:null, version:0, timer:null};

    remove.addEventListener('click', event => {
      event.preventDefault();
      clearTimeout(row.timer);
      rows = rows.filter(x => x !== row);
      tr.remove();
      renumber();
      if (!rows.length) addRow(null, true);
    });

    query.addEventListener('input', () => {
      clearTimeout(row.timer);
      row.version++;
      row.part = null;
      name.value = '';
      spec.value = '';
      unit.value = '';
      suggestions.replaceChildren();
      status.textContent = '';
      const value = query.value.trim();
      const version = row.version;
      if (value) {
        row.timer = setTimeout(() => lookupPart(row, value, version).catch(err => {
          if (version === row.version) status.textContent = err.message;
        }), 200);
      }
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

    if (focus) {
      query.focus();
      tr.scrollIntoView({block:'nearest'});
      message(`${rows.length}번 행을 추가했습니다.`);
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
    $('po-vendor-query').classList.remove('po-vendor-selected');
    $('po-vendor-hint').textContent = '글자를 입력한 뒤 목록에서 거래처를 선택하세요.';
    $('po-lines').replaceChildren();
    rows.forEach(row => clearTimeout(row.timer));
    rows = [];
    vendor = null;
    editingId = null;
    for (let n = 0; n < 3; n++) addRow();
    message('');
  }

  async function resolveEntries(used) {
    resolveVendorFromInput();
    if (!vendor) throw new Error('거래처를 목록에서 선택하세요.');
    for (const row of used) {
      if (!row.part) await lookupPart(row, row.query.value, row.version, true);
      if (!row.part) throw new Error(`${row.seq.textContent}행의 품목을 검색 결과에서 선택하세요.`);
      if (!row.qty.value || !Number.isFinite(Number(row.qty.value)) || Number(row.qty.value) <= 0)
        throw new Error(`${row.seq.textContent}행의 발주수량을 입력하세요.`);
      if (!row.warehouse.value) throw new Error(`${row.seq.textContent}행의 입고창고를 선택하세요.`);
      if (!row.location.value) throw new Error(`${row.seq.textContent}행의 저장위치를 선택하세요.`);
    }
  }

  async function saveOrder(event) {
    event.preventDefault();
    if (saving || $('po-save').disabled) return;
    const used = activeRows();
    if (!used.length) { message('품목을 한 행 이상 입력하세요.'); return; }
    saving = true;
    $('po-save').disabled = true;
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
      const isEdit = Boolean(editingId);
      message(isEdit ? '수정 저장 중…' : '저장 중…');
      const data = await request(isEdit ? `/api/purchase/orders/${editingId}` : '/api/purchase/orders', {
        method: isEdit ? 'PUT' : 'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(payload)
      });
      editingId = data.id;
      $('po-number').value = data.po_no;
      $('po-state').value = data.status === 'ORDERED' ? '발주완료' : data.status;
      message(`${data.po_no}${isEdit ? ' 수정되었습니다.' : ' 저장되었습니다.'}`);
    } catch (error) {
      message(error.message);
    } finally {
      saving = false;
      $('po-save').disabled = false;
    }
  }

  async function loadOrder(id) {
    try {
      const data = await request(`/api/purchase/orders/${id}`);
      $('po-lines').replaceChildren();
      rows.forEach(row => clearTimeout(row.timer));
      rows = [];
      editingId = data.id;
      vendor = {
        id:data.partner_id,
        partner_name:data.partner_name,
        partner_code:'',
        manager_name:data.manager_name || '',
        value:data.partner_name
      };
      $('po-date').value = data.order_date;
      $('po-requested-date').value = data.delivery_due_date || '';
      $('po-vendor-query').value = data.partner_name;
      $('po-vendor-query').classList.add('po-vendor-selected');
      $('po-vendor-hint').textContent = `불러온 거래처: ${data.partner_name}`;
      $('po-manager').value = data.manager_name || '';
      $('po-number').value = data.po_no;
      $('po-state').value = data.status === 'ORDERED' ? '발주완료' : data.status;
      $('po-note').value = data.note || '';
      (data.items || []).forEach(item => addRow(item));
      if (!rows.length) addRow();
      $('po-save').disabled = !data.editable;
      message(data.editable ? `${data.po_no} 수정 모드입니다.` : `${data.po_no}은 입고 이력이 있어 직접 수정할 수 없습니다.`);
    } catch (error) {
      message(error.message);
    }
  }

  function init() {
    const required = ['po-form','po-date','po-vendor-query','po-vendor-options','po-manager','po-requested-date','po-number','po-state','po-note','po-add-row','po-new','po-save','po-lines','po-warehouse-template','po-location-template','po-message'];
    const missing = required.filter(id => !$(id));
    if (missing.length) {
      console.error('Purchase order UI missing elements:', missing);
      return;
    }

    $('po-vendor-query').addEventListener('input', resolveVendorFromInput);
    $('po-vendor-query').addEventListener('change', resolveVendorFromInput);
    $('po-add-row').addEventListener('click', event => {
      event.preventDefault();
      addRow(null, true);
    });
    $('po-new').addEventListener('click', event => {
      event.preventDefault();
      resetOrder();
    });
    $('po-form').addEventListener('submit', saveOrder);

    resetOrder();
    const editId = Number(new URLSearchParams(location.search).get('edit'));
    if (Number.isInteger(editId) && editId > 0) loadOrder(editId);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
  else init();
})();
