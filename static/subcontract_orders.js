(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let vendor = null;
  let orderId = null;
  let rows = [];
  let saving = false;
  let lotTarget = null;
  let lotCandidates = [];

  const today = () => {
    const d = new Date();
    return [d.getFullYear(), String(d.getMonth() + 1).padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-');
  };
  const fmt = value => Number(value || 0).toLocaleString(undefined, {maximumFractionDigits: 6});
  const cell = (tr, value = '') => { const td = tr.insertCell(); td.textContent = value == null ? '' : String(value); return td; };
  const input = (type, label) => { const node = document.createElement('input'); node.type = type; node.setAttribute('aria-label', label); return node; };
  const msg = text => { $('so-message').textContent = text; };

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

  function cloneProcessSelect(value = '') {
    const node = document.createElement('select');
    [...$('so-process').options].forEach(option => node.append(option.cloneNode(true)));
    node.value = value || $('so-process').value || '';
    return node;
  }

  async function resolveBomOutput(row) {
    if (!row.part?.part_no || !row.process.value) {
      row.orderPart.value = '';
      return;
    }
    row.orderPart.value = 'BOM 조회중';
    try {
      const data = await request('/api/subcontract/bom-output?' + new URLSearchParams({
        previous_part_no: row.part.part_no,
        process_code: row.process.value
      }));
      if (!rows.includes(row) || row.part?.part_no !== data.previous_part_no) return;
      row.orderPart.value = data.order_part_no || '';
      row.orderName.value = data.order_part_name || '';
      row.spec.value = data.spec || '';
      row.unit.value = data.unit || '';
    } catch (error) {
      row.orderPart.value = '';
      msg(error.message);
    }
  }

  function vendorOptions() {
    return [...$('so-vendors').options].map(option => ({
      id: Number(option.dataset.id), code: option.dataset.code || '', name: option.dataset.name || '',
      manager: option.dataset.manager || '', value: option.value
    }));
  }
  function resolveVendor() {
    const value = $('so-vendor').value.trim().toLowerCase();
    $('so-vendor').classList.remove('so-vendor-selected');
    vendor = null;
    if (!value) { $('so-vendor-hint').textContent = '등록된 활성 공급사에서 선택합니다.'; return; }
    const matches = vendorOptions().filter(x => x.value.toLowerCase() === value || x.code.toLowerCase() === value || x.name.toLowerCase() === value);
    if (matches.length === 1) {
      vendor = matches[0];
      $('so-vendor').value = vendor.value;
      $('so-vendor').classList.add('so-vendor-selected');
      $('so-vendor-hint').textContent = `선택됨: ${vendor.code} / ${vendor.name}`;
      if (!$('so-manager').value.trim()) $('so-manager').value = vendor.manager;
    } else $('so-vendor-hint').textContent = '목록에서 발주처를 선택하세요.';
  }

  function showSuggestions(row, parts) {
    row.suggestions.replaceChildren();
    parts.forEach(part => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = [part.part_no, part.part_name, part.spec].filter(Boolean).join(' · ');
      button.addEventListener('mousedown', event => { event.preventDefault(); selectPreviousPart(row, part); });
      row.suggestions.append(button);
    });
  }

  async function searchPart(row, keyword, version) {
    const data = await request('/api/purchase/items/search?' + new URLSearchParams({keyword, limit: '100'}));
    if (row.version !== version || !rows.includes(row)) return;
    showSuggestions(row, data.items || []);
  }

  async function loadStock(row) {
    if (!row.part?.part_no) { row.stock.value = ''; return; }
    row.stock.value = '조회중';
    try {
      const params = new URLSearchParams({part_no: row.part.part_no});
      if (row.itemId) params.set('order_item_id', String(row.itemId));
      const data = await request('/api/subcontract/stock?' + params.toString());
      row.stock.value = fmt(data.stock_qty);
      row.stock.title = data.source_note || '';
    } catch (error) {
      row.stock.value = '-';
      row.stock.title = error.message;
    }
  }

  function selectPreviousPart(row, part) {
    row.part = part;
    row.previous.value = part.part_no;
    row.name = part.part_name || '';
    row.spec.value = part.spec || '';
    row.unit.value = part.unit || '';
    row.orderPart.value = '';
    row.orderName.value = '';
    row.suggestions.replaceChildren();
    if (!row.date.value) row.date.value = $('so-due-date').value;
    resolveBomOutput(row);
    loadStock(row);
  }

  function allocationStatus(row) {
    const qty = Number(row.qty.value || 0);
    const allocated = Number(row.allocatedQty || 0);
    row.allocated.value = fmt(allocated);
    const complete = qty > 0 && Math.abs(qty - allocated) < 1e-9 && row.allocations.length > 0;
    row.allocationStatus.textContent = complete ? '완료' : (allocated > 0 ? '미완료' : '미배정');
    row.allocationStatus.className = complete ? 'so-status-ok' : 'so-status-wait';
    return complete;
  }

  function renumber() { rows.forEach((row, index) => { row.seq.textContent = String(index + 1); }); }

  function addRow(item = null, focus = false) {
    const tr = document.createElement('tr');
    const seq = cell(tr, String(rows.length + 1)); seq.className = 'so-seq';
    const previous = input('text', '이전 품번'); previous.autocomplete = 'off'; previous.placeholder = '품번 검색';
    const suggestions = document.createElement('div'); suggestions.className = 'so-suggestions';
    const prevCell = cell(tr); prevCell.append(previous, suggestions);
    const stock = input('text', '이전품 재고'); stock.readOnly = true; cell(tr).append(stock);
    const orderPart = input('text', '발주 품번'); orderPart.readOnly = true; cell(tr).append(orderPart);
    const orderName = input('text', '발주 품명'); orderName.readOnly = true; cell(tr).append(orderName);
    const spec = input('text', '규격'); spec.readOnly = true; cell(tr).append(spec);
    const process = cloneProcessSelect(item?.processing_type_code || ''); cell(tr).append(process);
    const unit = input('text', '단위'); unit.readOnly = true; cell(tr).append(unit);
    const qty = input('number', '발주수량'); qty.min = '0.000001'; qty.step = 'any'; cell(tr).append(qty);
    const date = input('date', '납기일'); cell(tr).append(date);
    const allocated = input('text', 'LOT 배정수량'); allocated.readOnly = true; cell(tr).append(allocated);
    const allocationStatus = document.createElement('span'); allocationStatus.className = 'so-status-wait'; allocationStatus.textContent = '미배정'; cell(tr).append(allocationStatus);
    const note = input('text', '비고'); note.maxLength = 500; cell(tr).append(note);
    const lotBtn = document.createElement('button'); lotBtn.type = 'button'; lotBtn.className = 'so-btn'; lotBtn.textContent = 'LOT 배정'; lotBtn.disabled = true; cell(tr).append(lotBtn);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'so-remove'; remove.textContent = '×'; cell(tr).append(remove);

    const row = {tr, seq, previous, suggestions, stock, orderPart, orderName, spec, process, unit, qty, date, allocated, allocationStatus, note, lotBtn, remove, part:null, name:'', version:0, timer:null, itemId:null, allocations:[], allocatedQty:0};
    rows.push(row); $('so-lines').append(tr);

    previous.addEventListener('input', () => {
      clearTimeout(row.timer); row.version += 1; row.part = null; row.stock.value = ''; row.orderPart.value = ''; row.orderName.value = ''; row.spec.value = ''; row.unit.value = ''; row.suggestions.replaceChildren();
      const keyword = previous.value.trim(); const version = row.version;
      if (keyword) row.timer = setTimeout(() => searchPart(row, keyword, version).catch(error => { msg(error.message); }), 180);
    });
    process.addEventListener('change', () => { if (row.part) resolveBomOutput(row); });
    qty.addEventListener('input', () => { allocationStatus(row); updateConfirmState(); });
    lotBtn.addEventListener('click', () => openLotModal(row));
    remove.addEventListener('click', () => {
      if (row.allocations.length) { alert('LOT가 배정된 행은 저장 후 LOT 배정을 먼저 해제하세요.'); return; }
      clearTimeout(row.timer); rows = rows.filter(x => x !== row); tr.remove(); renumber(); if (!rows.length) addRow(null, true);
    });

    if (item) {
      row.itemId = item.id || null;
      row.part = {part_no:item.previous_part_no, part_name:item.order_part_name || '', spec:item.spec || '', unit:item.unit || ''};
      previous.value = item.previous_part_no || '';
      orderPart.value = item.order_part_no || '';
      orderName.value = item.order_part_name || '';
      spec.value = item.spec || '';
      process.value = item.processing_type_code || '';
      unit.value = item.unit || '';
      qty.value = item.order_qty ?? '';
      date.value = item.delivery_date || '';
      note.value = item.note || '';
      row.allocations = item.allocations || [];
      row.allocatedQty = item.allocated_qty || 0;
      lotBtn.disabled = !row.itemId;
      allocationStatus(row);
      loadStock(row);
    }
    if (focus) previous.focus();
    return row;
  }

  function usedRows() { return rows.filter(row => row.previous.value.trim() || row.qty.value || row.orderPart.value.trim()); }

  function setStatus(status) {
    const names = {DRAFT:'작성중', LOT_ALLOCATING:'LOT배정중', ORDERED:'발주완료', CANCELLED:'취소'};
    $('so-status').value = names[status] || status || '작성중';
  }

  function updateConfirmState(data = null) {
    if (data) $('so-confirm').disabled = !data.can_confirm;
    else $('so-confirm').disabled = !(orderId && usedRows().length && usedRows().every(allocationStatus));
  }

  function applyServerOrder(data) {
    orderId = data.id;
    $('so-number').value = data.order_no;
    setStatus(data.status);
    (data.items || []).forEach((item, index) => {
      const row = rows[index]; if (!row) return;
      row.itemId = item.id;
      row.previous.value = item.previous_part_no || '';
      row.part = {part_no:item.previous_part_no, part_name:item.order_part_name || '', spec:item.spec || '', unit:item.unit || ''};
      row.orderPart.value = item.order_part_no || '';
      row.orderName.value = item.order_part_name || '';
      row.spec.value = item.spec || '';
      row.unit.value = item.unit || '';
      row.allocations = item.allocations || [];
      row.allocatedQty = item.allocated_qty || 0;
      row.lotBtn.disabled = data.status === 'ORDERED';
      allocationStatus(row);
      loadStock(row);
    });
    $('so-save').disabled = data.status === 'ORDERED';
    $('so-add-row').disabled = data.status === 'ORDERED';
    updateConfirmState(data);
  }

  function reset() {
    $('so-form').reset();
    $('so-date').value = today(); $('so-number').value = ''; $('so-status').value = '작성중';
    $('so-vendor').classList.remove('so-vendor-selected'); $('so-vendor-hint').textContent = '등록된 활성 공급사에서 선택합니다.';
    $('so-lines').replaceChildren(); rows.forEach(row => clearTimeout(row.timer)); rows = []; vendor = null; orderId = null;
    $('so-save').disabled = false; $('so-add-row').disabled = false; $('so-confirm').disabled = true;
    for (let i = 0; i < 3; i += 1) addRow();
    msg(''); history.replaceState(null, '', '/subcontract/orders');
  }

  function payload() {
    resolveVendor();
    if (!vendor) throw new Error('발주처를 목록에서 선택하세요.');
    if (!$('so-process').value) throw new Error('가공유형을 선택하세요.');
    if (!$('so-location').value) throw new Error('외주 저장위치를 선택하세요.');
    const used = usedRows(); if (!used.length) throw new Error('발주 품목을 한 행 이상 입력하세요.');
    const items = used.map(row => {
      if (!row.part) throw new Error(`${row.seq.textContent}행의 이전 품번을 검색 결과에서 선택하세요.`);
      if (!row.process.value) throw new Error(`${row.seq.textContent}행의 가공유형을 선택하세요.`);
      if (!row.orderPart.value.trim() || row.orderPart.value === 'BOM 조회중') throw new Error(`${row.seq.textContent}행의 BOM 발주 품번을 확인하세요.`);
      const qty = Number(row.qty.value); if (!Number.isFinite(qty) || qty <= 0) throw new Error(`${row.seq.textContent}행의 발주수량을 확인하세요.`);
      return {previous_part_no:row.part.part_no, processing_type_code:row.process.value, order_part_no:row.orderPart.value.trim(), order_qty:qty, delivery_date:row.date.value || null, note:row.note.value.trim() || null};
    });
    return {order_date:$('so-date').value, partner_id:vendor.id, partner_name:vendor.name, processing_type_code:$('so-process').value, delivery_due_date:$('so-due-date').value || null, external_storage_location:$('so-location').value, manager_name:$('so-manager').value.trim() || null, note:$('so-note').value.trim() || null, items};
  }

  async function save(event) {
    event.preventDefault(); if (saving || $('so-save').disabled) return;
    let body; try { body = payload(); } catch (error) { msg(error.message); return; }
    saving = true; $('so-save').disabled = true; msg('저장 중...');
    try {
      const data = await request(orderId ? `/api/subcontract/orders/${orderId}` : '/api/subcontract/orders', {method:orderId?'PUT':'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      applyServerOrder(data); msg(`${data.order_no} 저장되었습니다. 각 품목의 LOT를 배정하세요.`);
    } catch (error) { msg(error.message); }
    finally { saving = false; if ($('so-status').value !== '발주완료') $('so-save').disabled = false; }
  }

  async function confirmOrder() {
    if (!orderId || $('so-confirm').disabled) return;
    if (!confirm('LOT 배정수량이 발주수량과 일치합니다. 외주가공 발주를 확정하시겠습니까?')) return;
    try {
      const data = await request(`/api/subcontract/orders/${orderId}/confirm`, {method:'POST'});
      applyServerOrder(data); msg(`${data.order_no} 발주 확정되었습니다. 외주가공 출고 처리에서 불러올 수 있습니다.`);
    } catch (error) { msg(error.message); }
  }

  function selectedLotNos() { return [...document.querySelectorAll('#so-lot-body input[type=checkbox]:checked')].map(x => x.value); }
  function refreshLotSummary() {
    if (!lotTarget) return;
    const selected = new Set(selectedLotNos());
    const total = lotCandidates.filter(x => selected.has(x.lot_no)).reduce((sum, x) => sum + Number(x.lot_qty || 0), 0);
    const required = Number(lotTarget.qty.value || 0);
    $('so-lot-selected').textContent = fmt(total); $('so-lot-balance').textContent = fmt(required - total);
    $('so-lot-balance').className = Math.abs(required-total) < 1e-9 ? 'so-status-ok' : 'so-status-wait';
  }

  async function openLotModal(row) {
    if (!orderId || !row.itemId) { msg('발주 내용을 먼저 저장하세요.'); return; }
    lotTarget = row; $('so-lot-modal').hidden = false; $('so-lot-part').textContent = row.part?.part_no || row.previous.value; $('so-lot-required').textContent = fmt(row.qty.value); $('so-lot-selected').textContent = '0'; $('so-lot-balance').textContent = fmt(row.qty.value); $('so-lot-body').innerHTML = '<tr><td colspan="4">조회 중...</td></tr>';
    try {
      const data = await request('/api/subcontract/stock?' + new URLSearchParams({part_no:row.part.part_no, order_item_id:String(row.itemId)}));
      lotCandidates = data.lots || []; $('so-lot-source-note').textContent = data.source_note || '';
      if (!lotCandidates.length) { $('so-lot-body').innerHTML = '<tr><td colspan="4">사용 가능한 LOT이 없습니다. 생산/재고 LOT 원장 연동 상태를 확인하세요.</td></tr>'; return; }
      const selected = new Set((row.allocations || []).map(x => x.lot_no));
      $('so-lot-body').innerHTML = lotCandidates.map(lot => `<tr><td><input type="checkbox" value="${escapeHtml(lot.lot_no)}" ${selected.has(lot.lot_no)?'checked':''}></td><td>${escapeHtml(lot.lot_no)}</td><td>${fmt(lot.lot_qty)}</td><td>${escapeHtml(lot.storage_location||'')}</td></tr>`).join('');
      document.querySelectorAll('#so-lot-body input[type=checkbox]').forEach(box => box.addEventListener('change', refreshLotSummary)); refreshLotSummary();
    } catch (error) { $('so-lot-body').innerHTML = `<tr><td colspan="4" class="so-error">${escapeHtml(error.message)}</td></tr>`; }
  }
  function escapeHtml(value) { const span=document.createElement('span'); span.textContent=value??''; return span.innerHTML; }
  function closeLotModal() { $('so-lot-modal').hidden = true; lotTarget = null; lotCandidates = []; }
  async function applyLots() {
    if (!lotTarget) return;
    const lotNos = selectedLotNos(); if (!lotNos.length) { alert('배정할 LOT를 선택하세요.'); return; }
    try {
      const data = await request(`/api/subcontract/orders/${orderId}/items/${lotTarget.itemId}/lots`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({lot_nos:lotNos})});
      applyServerOrder(data); closeLotModal(); msg('LOT 배정을 저장했습니다. LOT 전체수량 합계가 발주수량과 같아야 발주할 수 있습니다.');
    } catch (error) { alert(error.message); }
  }

  function loadOrderData(data) {
    $('so-lines').replaceChildren(); rows = []; orderId = data.id;
    $('so-date').value = data.order_date;
    $('so-due-date').value = data.delivery_due_date || '';
    $('so-location').value = data.external_storage_location || '';
    $('so-manager').value = data.manager_name || '';
    $('so-note').value = data.note || '';
    $('so-process').value = data.processing_type_code || '';
    const matchedVendor = vendorOptions().find(x => x.id === Number(data.partner_id));
    vendor = matchedVendor || {id:data.partner_id,name:data.partner_name,code:'',manager:data.manager_name||'',value:data.partner_name};
    $('so-vendor').value = matchedVendor ? matchedVendor.value : data.partner_name;
    $('so-vendor').classList.add('so-vendor-selected');
    $('so-vendor-hint').textContent = `불러온 발주처: ${data.partner_name}`;
    (data.items || []).forEach(item => addRow(item));
    if (!rows.length) addRow();
    applyServerOrder(data);
    msg(`${data.order_no}을 불러왔습니다.`);
  }

  async function loadOrder(id) {
    try {
      const data = await request(`/api/subcontract/orders/${id}`);
      loadOrderData(data);
    } catch (error) { msg(error.message); }
  }

  async function loadOrderByNumber() {
    const orderNo = $('so-number').value.trim();
    if (!orderNo) {
      msg('조회할 발주번호를 입력하세요.');
      $('so-number').focus();
      return;
    }
    try {
      msg('발주를 조회하는 중...');
      const data = await request('/api/subcontract/orders/by-number?' + new URLSearchParams({order_no: orderNo}));
      loadOrderData(data);
    } catch (error) {
      msg(error.message);
    }
  }

  function init() {
    const required=['so-form','so-date','so-vendor','so-vendors','so-process','so-due-date','so-location','so-manager','so-number','so-number-search','so-status','so-note','so-add-row','so-lines','so-new','so-save','so-confirm','so-message','so-lot-modal','so-lot-close','so-lot-cancel','so-lot-apply','so-lot-body'];
    const missing=required.filter(id=>!$(id)); if(missing.length){console.error('Subcontract order UI missing:',missing);return;}
    $('so-vendor').addEventListener('input',resolveVendor); $('so-vendor').addEventListener('change',resolveVendor);
    $('so-number-search').addEventListener('click',loadOrderByNumber);
    $('so-number').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();loadOrderByNumber();}});
    $('so-process').addEventListener('change',()=>rows.forEach(row=>{if(!row.process.value)row.process.value=$('so-process').value;if(row.part)resolveBomOutput(row);}));
    $('so-due-date').addEventListener('change',()=>rows.forEach(row=>{if(!row.date.value)row.date.value=$('so-due-date').value;}));
    $('so-add-row').addEventListener('click',()=>addRow(null,true)); $('so-new').addEventListener('click',reset); $('so-form').addEventListener('submit',save); $('so-confirm').addEventListener('click',confirmOrder);
    $('so-lot-close').addEventListener('click',closeLotModal); $('so-lot-cancel').addEventListener('click',closeLotModal); $('so-lot-apply').addEventListener('click',applyLots); $('so-lot-modal').addEventListener('click',event=>{if(event.target===$('so-lot-modal'))closeLotModal();});
    const editId=Number(new URLSearchParams(location.search).get('edit'));
    reset();
    if(Number.isInteger(editId)&&editId>0) loadOrder(editId);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true}); else init();
})();