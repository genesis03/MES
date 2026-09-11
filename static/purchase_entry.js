(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const rows = [];
  let mode = 'inbound', offset = 0, total = 0, searchId = 0, saving = false;
  const limit = 30;
  const picker = $('purchase-picker');
  const localNow = new Date();
  $('purchase-date').value = `${localNow.getFullYear()}-${String(localNow.getMonth()+1).padStart(2,'0')}-${String(localNow.getDate()).padStart(2,'0')}`;

  function message(text) { $('purchase-message').textContent = text; }
  async function request(url, options) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(Array.isArray(body.detail) ? body.detail.map(i => i.msg).join(' / ') : (body.detail || '처리 실패'));
    return body;
  }
  function cell(row, text) { const td = row.insertCell(); td.textContent = text; return td; }
  function button(label, action) { const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.onclick = action; return b; }
  function input(type, value, label) { const node = document.createElement('input'); node.type = type; node.value = value; node.required = true; node.setAttribute('aria-label', label); return node; }
  function select(template) { return $(template).content.firstElementChild.cloneNode(true); }

  function addItem(item) {
    const tr = document.createElement('tr');
    cell(tr, item.po_no || '단독 입고');
    cell(tr, [item.part_no, item.part_name, item.spec].filter(Boolean).join(' / '));
    cell(tr, item.unit);
    const qty = input('number', item.remaining_qty ?? 1, '입고수량'); qty.min = '0.000001'; qty.step = 'any';
    const lot = input('text', '', '공급사 LOT'); lot.maxLength = 100;
    const warehouse = select('purchase-warehouse-select'), location = select('purchase-location-select');
    cell(tr, '').append(qty); cell(tr, '').append(lot); cell(tr, '').append(warehouse); cell(tr, '').append(location);
    const entry = {item, tr, qty, lot, warehouse, location};
    const actions = cell(tr, '');
    actions.append(button('LOT 행 추가', () => addItem({...item, remaining_qty: 1})), button('삭제', () => {
      rows.splice(rows.indexOf(entry), 1); tr.remove();
      if (!rows.some(r => r.item.po_item_id != null)) $('purchase-partner').readOnly = false;
    }));
    rows.push(entry); $('purchase-inbound-rows').append(tr);
    if (item.po_item_id != null) $('purchase-partner').readOnly = true;
  }

  async function search() {
    const id = ++searchId;
    $('purchase-picker-message').textContent = '조회 중…';
    $('purchase-picker-rows').replaceChildren();
    $('purchase-prev').disabled = true; $('purchase-next').disabled = true;
    const params = new URLSearchParams({offset, limit});
    if (mode === 'po') params.set('partner_name', $('purchase-keyword').value.trim());
    else params.set('keyword', $('purchase-keyword').value.trim());
    try {
      const result = await request(`${mode === 'po' ? '/api/purchase/unreceived-orders' : '/api/purchase/items/search'}?${params}`);
      if (id !== searchId) return;
      total = result.total;
      $('purchase-picker-head').replaceChildren();
      const header = document.createElement('tr');
      for (const label of (mode === 'po' ? ['발주번호','거래처','품번','단위','잔량','선택'] : ['품번','품명','규격','단위','선택'])) {
        const th = document.createElement('th'); th.textContent = label; header.append(th);
      }
      $('purchase-picker-head').append(header);
      for (const item of result.items) {
        const tr = document.createElement('tr');
        const values = mode === 'po' ? [item.po_no,item.partner_name,item.part_no,item.unit,item.remaining_qty] : [item.part_no,item.part_name,item.spec,item.unit];
        values.forEach(v => cell(tr, v));
        const pick = button('선택', () => {
          if (mode === 'order') {
            $('inPartNo').value = item.part_no; $('inPartName').value = item.part_name;
            $('inSpec').value = item.spec || ''; $('inUnit').value = item.unit;
          } else {
            if (mode === 'po') {
              const linked = rows.find(r => r.item.po_item_id != null);
              if (linked && (linked.item.partner_id !== item.partner_id || linked.item.partner_name !== item.partner_name)) {
                $('purchase-picker-message').textContent = '한 입고 전표에는 같은 거래처의 발주만 담을 수 있습니다.'; return;
              }
              if (rows.some(r => r.item.po_item_id === item.po_item_id)) {
                $('purchase-picker-message').textContent = '이미 선택한 발주 품목입니다. LOT를 나누려면 기존 행의 LOT 행 추가를 사용하세요.'; return;
              }
              if (rows.length && $('purchase-partner').value.trim() !== item.partner_name) {
                $('purchase-picker-message').textContent = '입력 중인 전표의 거래처와 다릅니다. 기존 품목을 정리한 후 선택하세요.'; return;
              }
              $('purchase-partner').value = item.partner_name;
            }
            addItem(item);
          }
          picker.close();
        });
        cell(tr, '').append(pick); $('purchase-picker-rows').append(tr);
      }
      $('purchase-picker-message').textContent = total ? `검색 결과 ${total}건` : '검색 결과가 없습니다.';
      $('purchase-page').textContent = `${total ? offset + 1 : 0}–${Math.min(offset + limit, total)} / ${total}`;
      $('purchase-prev').disabled = offset === 0; $('purchase-next').disabled = offset + limit >= total;
    } catch (error) { if (id === searchId) $('purchase-picker-message').textContent = error.message; }
  }
  function open(nextMode) {
    mode = nextMode; offset = 0;
    $('purchase-picker-title').textContent = mode === 'po' ? '발주서 불러오기' : '품목 검색';
    $('purchase-keyword').value = mode === 'po' ? $('purchase-partner').value.trim() : '';
    $('purchase-keyword').previousSibling.textContent = mode === 'po' ? '거래처명 검색' : '품번·품명·규격 검색';
    picker.showModal(); search();
  }
  window.PurchaseEntry = {pickForOrder: () => open('order')};
  $('purchase-add').onclick = () => open('inbound');
  $('purchase-load').onclick = () => open('po');
  $('purchase-picker-close').onclick = () => picker.close();
  $('purchase-search').onclick = () => { offset = 0; search(); };
  $('purchase-keyword').onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); offset = 0; search(); } };
  $('purchase-prev').onclick = () => { offset = Math.max(0, offset-limit); search(); };
  $('purchase-next').onclick = () => { offset += limit; search(); };
  $('purchase-inbound-form').onsubmit = async event => {
    event.preventDefault();
    if (saving) return;
    if (!rows.length) { message('품목을 먼저 선택하세요.'); return; }
    if (rows.some(r => !r.lot.value.trim())) { message('모든 품목에 공급사 LOT 번호를 입력하세요.'); return; }
    const linked = rows.find(r => r.item.po_item_id != null);
    const body = {inbound_date:$('purchase-date').value, partner_id:linked?.item.partner_id ?? null,
      partner_name:$('purchase-partner').value.trim(), invoice_no:$('purchase-invoice').value.trim() || null,
      items:rows.map(r => ({po_item_id:r.item.po_item_id ?? null,part_no:r.item.part_no,unit:r.item.unit,
        inbound_qty:Number(r.qty.value),supplier_lot_no:r.lot.value.trim(),warehouse_code:r.warehouse.value,
        storage_location:r.location.value,unit_price:r.item.unit_price ?? 0}))};
    saving = true;
    // Disable the entire form so an in-flight request cannot lose subsequent edits.
    const controls = [...event.target.querySelectorAll('input,select,button')];
    controls.forEach(control => { control.disabled = true; });
    message('입고 저장 중…');
    try {
      const result = await request('/api/purchase/inbound', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      rows.length = 0; $('purchase-inbound-rows').replaceChildren(); $('purchase-partner').readOnly = false;
      message(`${result.inbound_no} 입고가 저장되었습니다.`);
      if (typeof window.loadOrders === 'function') window.loadOrders();
    } catch (error) { message(`${error.message} 입력 내용은 유지했습니다. 통신 오류라면 입고 이력에서 저장 여부를 확인하세요.`); }
    finally { saving = false; controls.forEach(control => { control.disabled = false; }); }
  };
})();
```
