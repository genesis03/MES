(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let partner = null;
  let inboundId = null;
  let mode = 'new'; // new | draft | confirmed
  let lines = [];
  let saving = false;

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
  const selectFromTemplate = (templateId, label, value = '') => {
    const source = $(templateId);
    const node = document.createElement('select');
    node.setAttribute('aria-label', label);
    [...source.options].forEach(option => node.append(option.cloneNode(true)));
    node.value = value || '';
    if (!node.value && node.options.length === 2) node.selectedIndex = 1;
    return node;
  };
  const message = text => { $('pi-message').textContent = text; };

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

  function renumber() { lines.forEach((line, index) => { line.seq.textContent = String(index + 1); }); }
  function emptyLines(text = '발주 불러오기로 품목을 선택하세요.') {
    $('pi-lines').replaceChildren();
    const tr = $('pi-lines').insertRow(); cell(tr, text).colSpan = 15;
  }

  function markDirty() {
    if (mode === 'draft') {
      $('pi-confirm').disabled = true;
      $('pi-status').value = '수정 중';
      message('수정 내용이 있습니다. 입고 확정 전에 다시 저장하세요.');
    } else if (mode === 'confirmed') {
      message('확정 구매 정정 내용이 있습니다. 저장하면 기존 LOT 번호는 유지됩니다.');
    }
  }

  function addLine(item, allowStructureChange = true) {
    if (!lines.length) $('pi-lines').replaceChildren();
    const tr = document.createElement('tr');
    const seq = cell(tr, String(lines.length + 1)); seq.className = 'pi-no';
    [item.po_no, item.part_no, item.part_name, item.spec, item.unit, item.order_qty,
      item.remaining_qty].forEach(value => cell(tr, value));

    const qty = field('number', '입고수량', item.inbound_qty ?? item.remaining_qty ?? '');
    qty.min = '0.000001'; qty.step = 'any';
    const date = field('date', '품목 납기일', item.delivery_date || ''); date.readOnly = true;
    const supplierLot = field('text', '공급사 LOT', item.supplier_lot_no || ''); supplierLot.maxLength = 100;
    const location = selectFromTemplate('pi-location-options', '저장위치', item.storage_location || '');
    const lot = field('text', '원자재 LOT 번호', item.internal_lot_no || ''); lot.readOnly = true; lot.className = 'pi-lot';
    lot.placeholder = '확정 시 생성';
    const note = field('text', '비고', item.note || ''); note.maxLength = 500;
    [qty, date, supplierLot, location, lot, note].forEach(node => cell(tr).append(node));

    const line = {item, tr, seq, qty, supplierLot, location, lot, note};
    const controls = cell(tr);
    if (allowStructureChange) {
      controls.append(button('LOT 행 추가', () => {
        const extra = {...item, inbound_item_id:null, inbound_qty:'', supplier_lot_no:'', internal_lot_no:'', note:''};
        addLine(extra, true); markDirty();
      }));
      controls.append(button('삭제', () => {
        lines = lines.filter(value => value !== line); tr.remove(); renumber(); markDirty();
        if (!lines.length) emptyLines();
      }));
    } else {
      controls.textContent = '정정';
    }
    [qty, supplierLot, location, note].forEach(node => node.addEventListener('change', markDirty));
    [qty, supplierLot, note].forEach(node => node.addEventListener('input', markDirty));
    lines.push(line); $('pi-lines').append(tr);
    return line;
  }

  function reset() {
    $('pi-form').reset();
    $('pi-date').value = today();
    $('pi-status').value = '입력 중';
    $('pi-inbound-no').value = '저장 시 자동 발번';
    $('pi-po-list').hidden = true;
    $('pi-po-results').replaceChildren();
    $('pi-po-message').textContent = '';
    $('pi-save').disabled = false; $('pi-confirm').disabled = true;
    ['pi-date', 'pi-note'].forEach(id => { $(id).disabled = false; });
    lines = []; partner = null; inboundId = null; mode = 'new';
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
    rows.forEach(row => addLine({
      ...row,
      delivery_date: row.item_delivery_date || row.delivery_date || '',
      storage_location: row.storage_location || ''
    }, true));
    $('pi-po-list').hidden = true;
    message(rows[0].po_no + '의 미입고 품목을 불러왔습니다. 저장위치를 확인하세요.');
  }

  async function loadOpenOrders() {
    const box = $('pi-po-results');
    box.textContent = '조회 중…'; $('pi-po-message').textContent = '';
    try {
      const data = await request('/api/purchase/orders/unreceived?limit=1000');
      box.replaceChildren();
      const groups = new Map();
      (data.items || []).forEach(row => {
        if (!groups.has(row.po_id)) groups.set(row.po_id, []);
        groups.get(row.po_id).push(row);
      });
      groups.forEach(groupRows => {
        const row = groupRows[0];
        box.append(button(`${row.po_no} · ${row.partner_name} · 미입고 품목 ${groupRows.length}행`, () => displayPO(groupRows)));
      });
      $('pi-po-message').textContent = groups.size ? groups.size + '건의 미입고 발주가 있습니다.' : '미입고 발주가 없습니다.';
    } catch (error) {
      box.replaceChildren(); $('pi-po-message').textContent = error.message;
    }
  }

  function payload() {
    if (!partner || !partner.id) throw new Error('발주를 먼저 불러오세요.');
    if (!lines.length) throw new Error('입고할 품목이 없습니다.');
    const items = lines.map(line => {
      const qty = Number(line.qty.value);
      if (!Number.isFinite(qty) || qty <= 0) throw new Error(line.seq.textContent + '행의 입고수량을 확인하세요.');
      if (!line.supplierLot.value.trim()) throw new Error(line.seq.textContent + '행의 공급사 LOT를 입력하세요.');
      if (!line.location.value) throw new Error(line.seq.textContent + '행의 저장위치를 선택하세요.');
      return {
        inbound_item_id: line.item.inbound_item_id || null,
        po_item_id: line.item.po_item_id,
        part_no: line.item.part_no,
        inbound_qty: qty,
        supplier_lot_no: line.supplierLot.value.trim(),
        internal_lot_no: line.item.internal_lot_no || null,
        storage_location: line.location.value,
        note: line.note.value.trim() || null
      };
    });
    return {
      inbound_date: $('pi-date').value,
      partner_id: partner.id,
      partner_name: partner.name,
      invoice_no: null,
      note: $('pi-note').value.trim() || null,
      items
    };
  }

  async function save(event) {
    event.preventDefault();
    if (saving) return;
    let body;
    try { body = payload(); } catch (error) { message(error.message); return; }
    saving = true; $('pi-save').disabled = true; message('저장 중…');
    try {
      let url = '/api/purchase/inbound/drafts';
      let method = 'POST';
      if (inboundId) { url = '/api/purchase/inbounds/' + inboundId; method = 'PUT'; }
      const data = await request(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      inboundId = data.id;
      mode = data.status === 'CONFIRMED' ? 'confirmed' : 'draft';
      $('pi-inbound-no').value = data.inbound_no;
      $('pi-status').value = mode === 'confirmed' ? '입고 확정' : '임시저장';
      $('pi-confirm').disabled = mode !== 'draft';
      message(data.inbound_no + (mode === 'confirmed' ? ' 정정 저장되었습니다. LOT 번호는 유지됩니다.' : ' 저장되었습니다. 확인 후 입고 확정하세요.'));
    } catch (error) {
      message(error.message + ' 저장 여부는 입고 조회에서 확인하세요.');
    } finally { saving = false; $('pi-save').disabled = false; }
  }

  async function confirm() {
    if (!inboundId || mode !== 'draft' || saving || $('pi-confirm').disabled) return;
    saving = true; $('pi-confirm').disabled = true; $('pi-save').disabled = true;
    message('입고 확정 중…');
    try {
      const data = await request('/api/purchase/inbound/drafts/' + inboundId + '/confirm', {method:'POST'});
      mode = 'confirmed';
      $('pi-status').value = '입고 확정';
      data.items.forEach((item, index) => {
        if (lines[index]) {
          lines[index].item.inbound_item_id = item.id;
          lines[index].item.internal_lot_no = item.internal_lot_no || '';
          lines[index].lot.value = item.internal_lot_no || '';
        }
      });
      message(data.inbound_no + ' 입고가 확정되었습니다. 이후 정정은 입고 조회에서 선택수정으로 진행하세요.');
    } catch (error) {
      $('pi-confirm').disabled = false; message(error.message);
    } finally { saving = false; $('pi-save').disabled = false; }
  }

  async function loadInbound(id) {
    try {
      const data = await request('/api/purchase/inbounds/' + id);
      $('pi-form').reset();
      $('pi-po-list').hidden = true;
      $('pi-po-results').replaceChildren();
      lines = []; $('pi-lines').replaceChildren();
      inboundId = data.id;
      mode = data.status === 'CONFIRMED' ? 'confirmed' : 'draft';
      partner = {id:data.partner_id, name:data.partner_name};
      $('pi-date').value = data.inbound_date;
      $('pi-vendor').value = data.partner_name;
      $('pi-manager').value = data.manager_name || '';
      $('pi-po-no').value = data.items[0]?.po_no || '';
      $('pi-inbound-no').value = data.inbound_no;
      $('pi-note').value = data.note || '';
      $('pi-status').value = mode === 'confirmed' ? '입고 확정(정정)' : '임시저장';
      (data.items || []).forEach(item => addLine(item, mode !== 'confirmed'));
      $('pi-confirm').disabled = mode !== 'draft';
      $('pi-save').disabled = false;
      message(mode === 'confirmed'
        ? data.inbound_no + ' 확정 구매 정정 모드입니다. 기존 LOT 행 추가/삭제는 제한됩니다.'
        : data.inbound_no + ' 임시저장 수정 모드입니다.');
    } catch (error) { message(error.message); }
  }

  function init() {
    const required = ['pi-form','pi-date','pi-vendor','pi-manager','pi-po-no','pi-inbound-no','pi-status','pi-note','pi-load-po','pi-po-list','pi-po-results','pi-po-message','pi-lines','pi-location-options','pi-new','pi-save','pi-confirm','pi-message'];
    const missing = required.filter(id => !$(id));
    if (missing.length) {
      console.error('Purchase inbound UI missing elements:', missing);
      return;
    }

    $('pi-load-po').addEventListener('click', event => {
      event.preventDefault();
      $('pi-po-list').hidden = !$('pi-po-list').hidden;
      if (!$('pi-po-list').hidden) loadOpenOrders();
    });
    $('pi-new').addEventListener('click', event => { event.preventDefault(); reset(); });
    $('pi-form').addEventListener('submit', save);
    $('pi-confirm').addEventListener('click', event => { event.preventDefault(); confirm(); });
    ['pi-date', 'pi-note'].forEach(id => $(id).addEventListener('input', markDirty));

    reset();
    const editId = Number(new URLSearchParams(location.search).get('edit'));
    if (Number.isInteger(editId) && editId > 0) loadInbound(editId);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once:true});
  else init();
})();
