(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let partner = null;
  let draftId = null;
  let lines = [];
  let saving = false;
  let confirmed = false;
  const today = () => {
    const d = new Date();
    return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
  };
  const cell = (tr, value = '') => {
    const td = tr.insertCell(); td.textContent = value == null ? '' : String(value); return td;
  };
  const button = (label, handler) => {
    const node = document.createElement('button');
    node.type = 'button'; node.className = 'pi-btn'; node.textContent = label;
    node.addEventListener('click', handler); return node;
  };
  const field = (type, label, value = '') => {
    const node = document.createElement('input');
    node.type = type; node.value = value == null ? '' : String(value);
    node.setAttribute('aria-label', label); return node;
  };
  const message = text => { $('pi-message').textContent = text; };
  async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(x => x.msg).join(' / ') : data.detail;
      throw new Error(detail || '처리하지 못했습니다.');
    }
    return data;
  }
  function dirty() {
    if (confirmed) return;
    if (draftId) {
      $('pi-confirm').disabled = true;
      $('pi-status').value = '수정 중';
      message('수정 내용이 있습니다. 입고 확정 전에 다시 저장하세요.');
    }
  }
  function renumber() { lines.forEach((line, index) => { line.seq.textContent = String(index + 1); }); }
  function addLine(item) {
    if (!lines.length) $('pi-lines').replaceChildren();
    const tr = document.createElement('tr');
    const seq = cell(tr, String(lines.length + 1)); seq.className = 'pi-no';
    [item.po_no, item.part_no, item.part_name, item.spec, item.unit, item.order_qty,
      item.remaining_qty].forEach(value => cell(tr, value));
    const qty = field('number', '입고수량', item.inbound_qty ?? item.remaining_qty ?? '');
    qty.min = '0.000001'; qty.step = 'any';
    const date = field('date', '품목 납기일', item.delivery_date || ''); date.readOnly = true;
    const supplierLot = field('text', '공급사 LOT', item.supplier_lot_no || ''); supplierLot.maxLength = 100;
    const warehouse = field('text', '입고창고', item.warehouse_code || 'RM'); warehouse.maxLength = 20;
    warehouse.setAttribute('list', 'pi-warehouse-options');
    const location = field('text', '저장위치', item.storage_location || 'S-LT'); location.maxLength = 20;
    location.setAttribute('list', 'pi-location-options');
    const lot = field('text', '원자재 LOT 번호', item.internal_lot_no || ''); lot.readOnly = true; lot.className = 'pi-lot';
    lot.placeholder = '확정 시 생성';
    const note = field('text', '비고', item.note || ''); note.maxLength = 500;
    [qty, date, supplierLot, warehouse, location, lot, note].forEach(node => cell(tr).append(node));
    const line = {item, tr, seq, qty, supplierLot, warehouse, location, lot, note};
    const controls = cell(tr);
    controls.append(button('LOT 행 추가', () => {
      const extra = {...item, inbound_qty: '', supplier_lot_no: '', internal_lot_no: '', note: ''};
      addLine(extra); dirty();
    }), button('삭제', () => {
      lines = lines.filter(value => value !== line); tr.remove(); renumber(); dirty();
      if (!lines.length) emptyLines();
    }));
    [qty, supplierLot, warehouse, location, note].forEach(node => node.addEventListener('input', dirty));
    lines.push(line); $('pi-lines').append(tr);
    return line;
  }
  function emptyLines() {
    $('pi-lines').replaceChildren();
    const tr = $('pi-lines').insertRow(); cell(tr, '발주 불러오기로 품목을 선택하세요.').colSpan = 16;
  }
  function reset() {
    $('pi-form').reset();
    $('pi-date').value = today();
    $('pi-status').value = '입력 중';
    $('pi-inbound-no').value = '저장 시 자동 발번';
    $('pi-po-search').hidden = true; $('pi-draft-search').hidden = true;
    $('pi-po-results').replaceChildren(); $('pi-draft-results').replaceChildren();
    $('pi-save').disabled = false; $('pi-confirm').disabled = true;
    ['pi-date', 'pi-invoice', 'pi-note'].forEach(id => { $(id).disabled = false; });
    lines = []; partner = null; draftId = null; confirmed = false;
    emptyLines(); message('');
  }
  function displayPO(rows) {
    if (!rows.length) throw new Error('이 발주에 남은 입고 품목이 없습니다.');
    reset();
    partner = {id: rows[0].partner_id, name: rows[0].partner_name};
    $('pi-vendor').value = partner.name;
    $('pi-po-no').value = rows[0].po_no;
    $('pi-manager').value = rows[0].manager_name || '';
    lines = [];
    rows.forEach(row => addLine(row));
    message(rows[0].po_no + '의 미입고 품목을 불러왔습니다.');
  }
  async function searchPO() {
    const box = $('pi-po-results'); box.textContent = '조회 중…';
    try {
      const query = $('pi-po-query').value.trim();
      const data = await request('/api/purchase/orders/unreceived?' + new URLSearchParams({po_no: query, limit: '1000'}));
      box.replaceChildren();
      const groups = new Map();
      data.items.forEach(row => {
        if (!groups.has(row.po_id)) groups.set(row.po_id, row);
      });
      groups.forEach(row => box.append(button(row.po_no + ' · ' + row.partner_name + ' · 미입고 품목 ' +
        data.items.filter(item => item.po_id === row.po_id).length + '행', () => {
          const selected = data.items.filter(item => item.po_id === row.po_id);
          displayPO(selected);
        })));
      $('pi-po-search-message').textContent = groups.size
        ? groups.size + '건의 발주가 있습니다.' : '미입고 발주가 없습니다.';
    } catch (error) { box.textContent = error.message; }
  }
  async function searchDrafts() {
    const box = $('pi-draft-results'); box.textContent = '조회 중…';
    try {
      const data = await request('/api/purchase/inbound/drafts?' +
        new URLSearchParams({inbound_no: $('pi-draft-query').value.trim()}));
      box.replaceChildren();
      if (!data.length) { box.textContent = '임시저장된 구매 입력이 없습니다.'; return; }
      data.forEach(row => box.append(button(row.inbound_no + ' · ' + row.partner_name +
        ' · ' + row.inbound_date, () => loadDraft(row.id))));
    } catch (error) { box.textContent = error.message; }
  }
  async function loadDraft(id) {
    try {
      const data = await request('/api/purchase/inbound/drafts/' + id);
      reset();
      draftId = data.id;
      partner = {id: data.partner_id, name: data.partner_name};
      $('pi-date').value = data.inbound_date;
      $('pi-vendor').value = data.partner_name;
      $('pi-manager').value = data.manager_name || '';
      $('pi-po-no').value = data.items[0]?.po_no || '';
      $('pi-inbound-no').value = data.inbound_no;
      $('pi-invoice').value = data.invoice_no || '';
      $('pi-note').value = data.note || '';
      $('pi-status').value = '임시저장';
      data.items.forEach(item => addLine(item));
      $('pi-confirm').disabled = false;
      message(data.inbound_no + ' 임시저장을 불러왔습니다.');
    } catch (error) { message(error.message); }
  }
  function payload() {
    if (!partner || !partner.id) throw new Error('발주를 먼저 불러오세요.');
    if (!lines.length) throw new Error('입고할 품목이 없습니다.');
    const items = lines.map(line => {
      const qty = Number(line.qty.value);
      if (!Number.isFinite(qty) || qty <= 0) throw new Error(line.seq.textContent + '행의 입고수량을 확인하세요.');
      if (!line.supplierLot.value.trim()) throw new Error(line.seq.textContent + '행의 공급사 LOT를 입력하세요.');
      if (!line.warehouse.value.trim() || !line.location.value.trim())
        throw new Error(line.seq.textContent + '행의 창고와 저장위치를 입력하세요.');
      return {
        po_item_id: line.item.po_item_id, part_no: line.item.part_no,
        inbound_qty: qty, supplier_lot_no: line.supplierLot.value.trim(),
        warehouse_code: line.warehouse.value.trim(), storage_location: line.location.value.trim(),
        note: line.note.value.trim() || null
      };
    });
    return {inbound_date: $('pi-date').value, partner_id: partner.id, partner_name: partner.name,
      invoice_no: $('pi-invoice').value.trim() || null, note: $('pi-note').value.trim() || null, items};
  }
  async function save(event) {
    event.preventDefault();
    if (saving || confirmed) return;
    let body;
    try { body = payload(); } catch (error) { message(error.message); return; }
    saving = true; $('pi-save').disabled = true; message('저장 중…');
    try {
      const data = await request(draftId
        ? '/api/purchase/inbound/drafts/' + draftId : '/api/purchase/inbound/drafts', {
        method: draftId ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      draftId = data.id;
      $('pi-inbound-no').value = data.inbound_no;
      $('pi-status').value = '임시저장';
      $('pi-confirm').disabled = false;
      message(data.inbound_no + ' 저장되었습니다. 확인 후 입고 확정하세요.');
    } catch (error) {
      message(error.message + ' 통신 오류라면 임시저장 불러오기로 저장 여부를 확인하세요.');
    } finally { saving = false; $('pi-save').disabled = false; }
  }
  async function confirm() {
    if (!draftId || saving || confirmed || $('pi-confirm').disabled) return;
    saving = true; $('pi-confirm').disabled = true; $('pi-save').disabled = true;
    message('입고 확정 중…');
    try {
      const data = await request('/api/purchase/inbound/drafts/' + draftId + '/confirm', {method: 'POST'});
      confirmed = true;
      $('pi-status').value = '입고 확정';
      data.items.forEach((item, index) => { if (lines[index]) lines[index].lot.value = item.internal_lot_no || ''; });
      lines.forEach(line => {
        line.tr.querySelectorAll('input,button').forEach(control => { control.disabled = true; });
      });
      ['pi-date', 'pi-invoice', 'pi-note'].forEach(id => { $(id).disabled = true; });
      message(data.inbound_no + ' 입고가 확정되었습니다. 원자재 LOT 번호가 생성되었습니다.');
    } catch (error) {
      $('pi-confirm').disabled = false; $('pi-save').disabled = false;
      message(error.message + ' 입고 이력에서 확정 여부를 확인하세요.');
    } finally { saving = false; }
  }
  $('pi-load-po').addEventListener('click', () => {
    $('pi-po-search').hidden = !$('pi-po-search').hidden;
    $('pi-draft-search').hidden = true;
    if (!$('pi-po-search').hidden) searchPO();
  });
  $('pi-load-draft').addEventListener('click', () => {
    $('pi-draft-search').hidden = !$('pi-draft-search').hidden;
    $('pi-po-search').hidden = true;
    if (!$('pi-draft-search').hidden) searchDrafts();
  });
  $('pi-po-search-button').addEventListener('click', searchPO);
  $('pi-draft-search-button').addEventListener('click', searchDrafts);
  $('pi-po-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); searchPO(); } });
  $('pi-draft-query').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); searchDrafts(); } });
  ['pi-date', 'pi-invoice', 'pi-note'].forEach(id => $(id).addEventListener('input', dirty));
  $('pi-new').addEventListener('click', reset);
  $('pi-form').addEventListener('submit', save);
  $('pi-confirm').addEventListener('click', confirm);
  reset();
})();

