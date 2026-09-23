(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let partner = null;
  let inboundId = null;
  let mode = 'new'; // new | draft | confirmed
  let lines = [];
  let saving = false;
  let pickerOrders = [];
  let selectedPickerId = null;

  const today = () => {
    const d = new Date();
    return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
  };
  const daysAgo = days => {
    const d = new Date();
    d.setDate(d.getDate() - days);
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
    $('pi-inbound-no').value = '';
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
    closeOrderPicker();
    message(rows[0].po_no + '의 미입고 품목을 불러왔습니다. 저장위치를 확인하세요.');
  }

  function renderOrderPicker() {
    const body = $('pi-po-popup-body');
    body.replaceChildren();
    selectedPickerId = null;
    $('pi-po-select').disabled = true;

    if (!pickerOrders.length) {
      const tr = body.insertRow();
      const td = tr.insertCell();
      td.colSpan = 6;
      td.className = 'po-popup-empty';
      td.textContent = 'No data';
      $('pi-po-popup-count').textContent = '0건';
      return;
    }

    pickerOrders.forEach(order => {
      const tr = body.insertRow();
      tr.dataset.poId = String(order.po_id);
      [
        order.po_no,
        order.partner_name,
        order.order_date,
        order.status_name,
        order.related_sales_order_no || '-',
        order.updated_at || ''
      ].forEach(value => cell(tr, value));
      tr.addEventListener('click', () => {
        body.querySelectorAll('tr.selected').forEach(row => row.classList.remove('selected'));
        tr.classList.add('selected');
        selectedPickerId = order.po_id;
        $('pi-po-select').disabled = false;
      });
      tr.addEventListener('dblclick', () => {
        selectedPickerId = order.po_id;
        choosePickerOrder();
      });
    });
    $('pi-po-popup-count').textContent = pickerOrders.length + '건';
  }

  async function searchOrderPicker() {
    const params = new URLSearchParams();
    const values = {
      start_date: $('pi-po-start').value,
      end_date: $('pi-po-end').value,
      po_no: $('pi-po-search-no').value.trim(),
      part_no: $('pi-po-search-part').value.trim(),
    };
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    $('pi-po-popup-message').textContent = '조회 중…';
    try {
      const data = await request('/api/purchase/inbound/order-picker?' + params.toString());
      pickerOrders = data.orders || [];
      renderOrderPicker();
      $('pi-po-popup-message').textContent = pickerOrders.length
        ? '행을 선택한 뒤 선택 버튼을 누르거나 더블클릭하세요.'
        : '조건에 맞는 미입고 발주가 없습니다.';
    } catch (error) {
      pickerOrders = [];
      renderOrderPicker();
      $('pi-po-popup-message').textContent = error.message;
    }
  }

  function openOrderPicker() {
    if (!$('pi-po-start').value) $('pi-po-start').value = daysAgo(7);
    if (!$('pi-po-end').value) $('pi-po-end').value = today();
    const dialog = $('pi-po-dialog');
    if (!dialog.open) dialog.showModal();
    searchOrderPicker();
    setTimeout(() => $('pi-po-search-no').focus(), 0);
  }

  function closeOrderPicker() {
    const dialog = $('pi-po-dialog');
    if (dialog.open) dialog.close();
  }

  function choosePickerOrder() {
    const order = pickerOrders.find(row => row.po_id === selectedPickerId);
    if (!order) {
      $('pi-po-popup-message').textContent = '불러올 발주를 선택하세요.';
      return;
    }
    try { displayPO(order.items || []); }
    catch (error) { $('pi-po-popup-message').textContent = error.message; }
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


  let inboundPickerRows = [];
  let selectedInboundPickerId = null;

  function closeInboundPicker() {
    const dialog = $('pi-inbound-dialog');
    if (dialog.open) dialog.close();
    selectedInboundPickerId = null;
  }

  function renderInboundPicker() {
    const body = $('pi-inbound-popup-body');
    body.replaceChildren();
    selectedInboundPickerId = null;
    $('pi-inbound-select').disabled = true;
    if (!inboundPickerRows.length) {
      const tr = body.insertRow(); const td = tr.insertCell(); td.colSpan = 6; td.className = 'po-popup-empty'; td.textContent = '조회된 입고가 없습니다.';
      $('pi-inbound-popup-count').textContent = '0건';
      return;
    }
    inboundPickerRows.forEach(row => {
      const tr = body.insertRow();
      tr.dataset.inboundId = String(row.inbound_id);
      [row.inbound_no,row.partner_name,row.inbound_date,row.po_no,row.status_name || row.status || '',row.item_count].forEach(value => cell(tr,value));
      tr.addEventListener('click',()=>{
        body.querySelectorAll('tr.selected').forEach(x=>x.classList.remove('selected'));
        tr.classList.add('selected'); selectedInboundPickerId = Number(row.inbound_id); $('pi-inbound-select').disabled = false;
      });
      tr.addEventListener('dblclick',async()=>{ selectedInboundPickerId = Number(row.inbound_id); await chooseInboundPicker(); });
    });
    $('pi-inbound-popup-count').textContent = inboundPickerRows.length + '건';
  }

  async function searchInboundPicker() {
    const params = new URLSearchParams();
    const values = {
      inbound_no:$('pi-inbound-search-no').value.trim(),
      start_date:$('pi-inbound-start').value,
      end_date:$('pi-inbound-end').value,
      partner_name:$('pi-inbound-search-partner').value.trim(),
      part_no:$('pi-inbound-search-part').value.trim(),
      po_no:$('pi-inbound-search-po').value.trim(),
      lot:$('pi-inbound-search-lot').value.trim()
    };
    Object.entries(values).forEach(([k,v])=>{ if(v) params.set(k,v); });
    $('pi-inbound-popup-message').textContent = '조회 중…';
    try {
      const data = await request('/api/purchase/inquiry/inbounds?' + params.toString());
      const map = new Map();
      (data.items || []).forEach(row => {
        if (!map.has(row.inbound_id)) map.set(row.inbound_id,{...row,item_count:0});
        map.get(row.inbound_id).item_count += 1;
      });
      inboundPickerRows = [...map.values()];
      renderInboundPicker();
      $('pi-inbound-popup-message').textContent = inboundPickerRows.length ? '행을 선택한 뒤 선택 버튼을 누르거나 더블클릭하세요.' : '조건에 맞는 입고가 없습니다.';
    } catch(error) {
      inboundPickerRows = []; renderInboundPicker(); $('pi-inbound-popup-message').textContent = error.message;
    }
  }

  function openInboundPicker() {
    $('pi-inbound-search-no').value = '';
    $('pi-inbound-search-partner').value = '';
    $('pi-inbound-search-part').value = '';
    $('pi-inbound-search-po').value = '';
    $('pi-inbound-search-lot').value = '';
    $('pi-inbound-start').value = daysAgo(7);
    $('pi-inbound-end').value = today();
    const dialog = $('pi-inbound-dialog');
    if (!dialog.open) dialog.showModal();
    searchInboundPicker();
    setTimeout(()=>$('pi-inbound-search-no').focus(),0);
  }

  async function chooseInboundPicker() {
    if (!selectedInboundPickerId) return;
    const id = selectedInboundPickerId;
    closeInboundPicker();
    await loadInbound(id);
  }

  async function loadInboundByNumber() {
    const inboundNo = $('pi-inbound-no').value.trim();
    if (!inboundNo) { openInboundPicker(); return; }
    try {
      const data = await request('/api/purchase/inquiry/inbounds?' + new URLSearchParams({inbound_no:inboundNo}));
      const exact = (data.items || []).find(row => String(row.inbound_no || '').toUpperCase() === inboundNo.toUpperCase());
      if (!exact) throw new Error('해당 입고번호를 찾을 수 없습니다.');
      await loadInbound(exact.inbound_id);
    } catch(error) { message(error.message); }
  }

  function init() {
    const required = [
      'pi-form','pi-date','pi-vendor','pi-manager','pi-po-no','pi-inbound-no','pi-inbound-search-btn','pi-status','pi-note',
      'pi-load-po','pi-lines','pi-location-options','pi-new','pi-save','pi-confirm','pi-message',
      'pi-po-dialog','pi-po-x','pi-po-search-no','pi-po-start','pi-po-end','pi-po-search-part','pi-po-search-btn',
      'pi-po-popup-body','pi-po-popup-message','pi-po-popup-count','pi-po-select','pi-po-close',
      'pi-inbound-dialog','pi-inbound-x','pi-inbound-search-no','pi-inbound-start','pi-inbound-end','pi-inbound-search-partner','pi-inbound-search-part','pi-inbound-search-po','pi-inbound-search-lot','pi-inbound-search-submit','pi-inbound-popup-body','pi-inbound-popup-message','pi-inbound-popup-count','pi-inbound-select','pi-inbound-close'
    ];
    const missing = required.filter(id => !$(id));
    if (missing.length) {
      console.error('Purchase inbound UI missing elements:', missing);
      return;
    }

    $('pi-inbound-search-btn').addEventListener('click', event => { event.preventDefault(); loadInboundByNumber(); });
    $('pi-inbound-no').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); loadInboundByNumber(); } });
    $('pi-inbound-x').addEventListener('click', closeInboundPicker);
    $('pi-inbound-close').addEventListener('click', closeInboundPicker);
    $('pi-inbound-select').addEventListener('click', chooseInboundPicker);
    $('pi-inbound-search-submit').addEventListener('click', searchInboundPicker);
    ['pi-inbound-search-no','pi-inbound-start','pi-inbound-end','pi-inbound-search-partner','pi-inbound-search-part','pi-inbound-search-po','pi-inbound-search-lot'].forEach(id => $(id).addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); searchInboundPicker(); } }));
    $('pi-inbound-dialog').addEventListener('cancel', event => { event.preventDefault(); closeInboundPicker(); });
    $('pi-load-po').addEventListener('click', event => { event.preventDefault(); openOrderPicker(); });
    $('pi-po-x').addEventListener('click', closeOrderPicker);
    $('pi-po-close').addEventListener('click', closeOrderPicker);
    $('pi-po-select').addEventListener('click', choosePickerOrder);
    $('pi-po-search-btn').addEventListener('click', searchOrderPicker);
    ['pi-po-search-no', 'pi-po-search-part'].forEach(id => $(id).addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); searchOrderPicker(); }
    }));
    $('pi-po-dialog').addEventListener('cancel', event => { event.preventDefault(); closeOrderPicker(); });
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
