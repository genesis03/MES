const $ = (id) => document.getElementById(id);
let customers = [];
let customerCandidates = [];
let customerActiveIndex = -1;
const editingOrderId = Number(new URLSearchParams(location.search).get('order_id') || 0);
let editingOrder = null;
let readOnlyOrder = false;

function today() {
  return new Date().toISOString().slice(0, 10);
}

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}

async function getJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || '처리에 실패했습니다.');
  return data;
}

function normalizeText(v) {
  return String(v ?? '').trim().toLowerCase();
}

function closeCustomerSuggestions() {
  const box = $('customerSuggestions');
  box.style.display = 'none';
  box.innerHTML = '';
  customerCandidates = [];
  customerActiveIndex = -1;
}

function chooseCustomer(row) {
  if (!row) return;
  $('customerId').value = row.id;
  $('customerSearch').value = row.partner_name;
  $('selectedCustomer').textContent = row.partner_name;
  closeCustomerSuggestions();
}

function renderCustomerSuggestions() {
  const box = $('customerSuggestions');
  if (!customerCandidates.length) {
    box.innerHTML = '<div class="customer-suggestion" style="color:#94a3b8">일치하는 판매처가 없습니다.</div>';
    box.style.display = 'block';
    return;
  }
  box.innerHTML = customerCandidates.map((x, i) => `
    <div class="customer-suggestion${i === customerActiveIndex ? ' active' : ''}" data-index="${i}">
      <span class="name">${esc(x.partner_name)}</span>
    </div>`).join('');
  box.querySelectorAll('.customer-suggestion[data-index]').forEach(el => {
    el.addEventListener('mousedown', e => {
      e.preventDefault();
      chooseCustomer(customerCandidates[Number(el.dataset.index)]);
    });
  });
  box.style.display = 'block';
}

function searchCustomer() {
  const q = normalizeText($('customerSearch').value);
  $('customerId').value = '';
  $('selectedCustomer').textContent = '';
  if (!q) {
    closeCustomerSuggestions();
    return;
  }

  customerCandidates = customers.filter(x =>
    normalizeText(x.partner_name).includes(q) || normalizeText(x.partner_code).includes(q)
  );
  customerActiveIndex = -1;

  if (customerCandidates.length === 1) {
    chooseCustomer(customerCandidates[0]);
    return;
  }
  renderCustomerSuggestions();
}

async function loadMasters() {
  customers = await getJson('/api/sales/customers');
  if (editingOrderId) {
    await loadOrder(editingOrderId);
  } else {
    addRow();
  }
}

function closeSuggestions(tr) {
  const box = tr.querySelector('.suggestions');
  box.style.display = 'none';
  box.innerHTML = '';
  tr._candidates = [];
  tr._activeIndex = -1;
}

function renderSuggestions(tr) {
  const box = tr.querySelector('.suggestions');
  const rows = tr._candidates || [];
  const activeIndex = tr._activeIndex ?? -1;
  if (!rows.length) {
    box.innerHTML = '<div class="suggestion" style="color:#94a3b8">일치하는 완제품/반제품 품번이 없습니다.</div>';
    box.style.display = 'block';
    return;
  }
  box.innerHTML = rows.map((x, i) => `<div class="suggestion${i === activeIndex ? ' active' : ''}" data-index="${i}"><span class="pn">${esc(x.part_no)}</span><span class="name">${esc(x.part_name || '')}</span></div>`).join('');
  box.querySelectorAll('.suggestion[data-index]').forEach(el => {
    el.addEventListener('mousedown', e => {
      e.preventDefault();
      choosePart(tr, rows[Number(el.dataset.index)]);
    });
  });
  box.style.display = 'block';
}

function choosePart(tr, row) {
  if (!row) return;
  tr.dataset.partNo = row.part_no;
  tr.querySelector('.part-input').value = row.part_no;
  tr.querySelector('.part-name').textContent = row.part_name || '';
  tr.querySelector('.unit').textContent = row.unit || 'EA';
  const delivery = tr.querySelector('.delivery-date');
  if (!delivery.value && $('deliveryDueDate').value) delivery.value = $('deliveryDueDate').value;
  closeSuggestions(tr);
}

async function searchPart(tr) {
  const input = tr.querySelector('.part-input');
  const q = input.value.trim();
  tr.dataset.partNo = '';
  tr.querySelector('.part-name').textContent = '';
  tr.querySelector('.unit').textContent = 'EA';
  if (!q) {
    closeSuggestions(tr);
    return;
  }
  try {
    tr._candidates = await getJson('/api/sales/items?q=' + encodeURIComponent(q) + '&limit=30');
    tr._activeIndex = -1;
    renderSuggestions(tr);
  } catch (e) {
    const box = tr.querySelector('.suggestions');
    box.innerHTML = `<div class="suggestion" style="color:#b91c1c">${esc(e.message)}</div>`;
    box.style.display = 'block';
  }
}

function addRow(data = null) {
  const tr = document.createElement('tr');
  tr.dataset.partNo = '';
  tr._candidates = [];
  tr._activeIndex = -1;
  tr.innerHTML = `
    <td><div class="part-picker"><input class="part-input" autocomplete="off" placeholder="품번 입력"><div class="suggestions"></div></div></td>
    <td class="part-name"></td>
    <td><input class="order-qty" type="number" min="0.000001" step="any" style="width:100%;padding:7px"></td>
    <td class="unit">EA</td>
    <td><input class="delivery-date" type="date" style="width:100%;padding:7px" value="${esc($('deliveryDueDate').value || '')}"></td>
    <td><button type="button" class="btn btn-danger delete-row">삭제</button></td>`;

  const input = tr.querySelector('.part-input');
  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => searchPart(tr), 180);
  });
  input.addEventListener('focus', () => {
    if (input.value.trim() && !tr.dataset.partNo) searchPart(tr);
  });
  input.addEventListener('keydown', e => {
    const rows = tr._candidates || [];
    if (e.key === 'ArrowDown' && rows.length) {
      e.preventDefault();
      tr._activeIndex = Math.min((tr._activeIndex ?? -1) + 1, rows.length - 1);
      renderSuggestions(tr);
    } else if (e.key === 'ArrowUp' && rows.length) {
      e.preventDefault();
      tr._activeIndex = Math.max((tr._activeIndex ?? 0) - 1, 0);
      renderSuggestions(tr);
    } else if (e.key === 'Enter' && rows.length) {
      e.preventDefault();
      choosePart(tr, rows[tr._activeIndex >= 0 ? tr._activeIndex : 0]);
    } else if (e.key === 'Escape') {
      closeSuggestions(tr);
    }
  });

  tr.querySelector('.delete-row').addEventListener('click', () => {
    tr.remove();
    if (!$('itemBody').querySelector('tr') && !readOnlyOrder) addRow();
  });
  $('itemBody').appendChild(tr);

  if (data) {
    tr.dataset.partNo = data.part_no || '';
    tr.querySelector('.part-input').value = data.part_no || '';
    tr.querySelector('.part-name').textContent = data.part_name || '';
    tr.querySelector('.order-qty').value = data.order_qty ?? '';
    tr.querySelector('.unit').textContent = data.unit || 'EA';
    tr.querySelector('.delivery-date').value = data.delivery_date || '';
  }
}

function setReadOnlyMode(message) {
  readOnlyOrder = true;
  document.querySelectorAll('#orderDate,#deliveryDueDate,#customerSearch,#orderType,#transactionType,#managerName,#note,#itemBody input,#itemBody button,#addRowBtn').forEach(el => {
    el.disabled = true;
  });
  $('saveBtn').disabled = true;
  $('saveBtn').textContent = '출고 이력으로 수정 불가';
  $('pageTitle').textContent = '수주 입력 · 조회';
  if (message) {
    const hint = document.createElement('div');
    hint.style.cssText = 'margin:0 0 12px;color:#b45309;font-size:12px;font-weight:700';
    hint.textContent = message;
    $('pageTitle').after(hint);
  }
}

async function loadOrder(orderId) {
  const order = await getJson('/api/sales/orders/' + encodeURIComponent(orderId));
  editingOrder = order;
  $('pageTitle').textContent = `수주 입력 · ${order.order_no}`;
  $('orderDate').value = order.order_date || '';
  $('deliveryDueDate').value = order.delivery_due_date || '';
  $('orderType').value = order.order_type || 'NORMAL';
  $('transactionType').value = order.transaction_type || 'PAID';
  $('managerName').value = order.manager_name || '';
  $('note').value = order.note || '';

  const customer = customers.find(x => Number(x.id) === Number(order.customer_id));
  if (customer) {
    chooseCustomer(customer);
  } else {
    $('customerId').value = order.customer_id || '';
    $('customerSearch').value = order.customer_name || '';
    $('selectedCustomer').textContent = order.customer_name || '';
  }

  $('itemBody').innerHTML = '';
  (order.items || []).forEach(item => addRow(item));
  if (!(order.items || []).length) addRow();

  if (order.editable) {
    $('saveBtn').textContent = '수주 수정 저장';
  } else {
    setReadOnlyMode('이미 출고 이력이 있는 수주입니다. 조회만 가능하며 수정은 할 수 없습니다.');
  }
}


function validateOrder(items) {
  const orderDate = $('orderDate').value;
  const dueDate = $('deliveryDueDate').value;
  if (!orderDate || !$('customerId').value) return '수주일자와 판매처를 입력해 주세요.';
  if (!['NORMAL','SAMPLE','DEVELOPMENT'].includes($('orderType').value)) return '수주구분을 선택해 주세요.';
  if (!['PAID','FREE'].includes($('transactionType').value)) return '거래구분을 선택해 주세요.';
  if (dueDate && dueDate < orderDate) return '납기예정일은 수주일자보다 빠를 수 없습니다.';
  if (!items.length) return '수주 품목을 입력해 주세요.';
  if (items.some(x => !x.part_no || x.part_no !== x.typed_part_no || x.order_qty <= 0)) return '품번은 검색 결과에서 등록된 완제품/반제품을 선택하고 수량을 입력해 주세요.';

  const partNos = items.map(x => x.part_no);
  if (new Set(partNos).size !== partNos.length) return '동일 품번은 한 수주에 중복 입력할 수 없습니다.';
  if (items.some(x => x.delivery_date && x.delivery_date < orderDate)) return '품목 납기일은 수주일자보다 빠를 수 없습니다.';
  return '';
}

async function saveOrder() {
  const rows = [...$('itemBody').querySelectorAll('tr')];
  const items = rows.map(tr => ({
    part_no: tr.dataset.partNo || '',
    typed_part_no: tr.querySelector('.part-input').value.trim(),
    order_qty: Number(tr.querySelector('.order-qty').value || 0),
    delivery_date: tr.querySelector('.delivery-date').value || null
  })).filter(x => x.typed_part_no || x.order_qty);

  const error = validateOrder(items);
  if (error) return alert(error);

  try {
    const data = await getJson(editingOrderId ? '/api/sales/orders/' + encodeURIComponent(editingOrderId) : '/api/sales/orders', {
      method: editingOrderId ? 'PUT' : 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        order_date: $('orderDate').value,
        delivery_due_date: $('deliveryDueDate').value || null,
        customer_id: Number($('customerId').value),
        order_type: $('orderType').value,
        transaction_type: $('transactionType').value,
        manager_name: $('managerName').value.trim() || null,
        note: $('note').value.trim() || null,
        items: items.map(x => ({part_no: x.part_no, order_qty: x.order_qty, delivery_date: x.delivery_date}))
      })
    });
    alert(`${data.message}\n수주번호: ${data.order_no}`);
    location.href = '/sales/orders/inquiry';
  } catch (e) {
    alert(e.message);
  }
}

document.addEventListener('click', e => {
  if (!$('customerSearch').contains(e.target) && !$('customerSuggestions').contains(e.target)) closeCustomerSuggestions();
  document.querySelectorAll('#itemBody tr').forEach(tr => {
    if (!tr.contains(e.target)) closeSuggestions(tr);
  });
});

document.addEventListener('DOMContentLoaded', () => {
  if (!editingOrderId) $('orderDate').value = today();
  $('addRowBtn').addEventListener('click', addRow);
  $('saveBtn').addEventListener('click', saveOrder);
  $('deliveryDueDate').addEventListener('change', () => {
    document.querySelectorAll('#itemBody .delivery-date').forEach(el => {
      if (!el.value) el.value = $('deliveryDueDate').value;
    });
  });

  let customerTimer = null;
  $('customerSearch').addEventListener('input', () => {
    clearTimeout(customerTimer);
    customerTimer = setTimeout(searchCustomer, 120);
  });
  $('customerSearch').addEventListener('focus', () => {
    if ($('customerSearch').value.trim() && !$('customerId').value) searchCustomer();
  });
  $('customerSearch').addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' && customerCandidates.length > 1) {
      e.preventDefault();
      customerActiveIndex = Math.min(customerActiveIndex + 1, customerCandidates.length - 1);
      renderCustomerSuggestions();
    } else if (e.key === 'ArrowUp' && customerCandidates.length > 1) {
      e.preventDefault();
      customerActiveIndex = Math.max(customerActiveIndex - 1, 0);
      renderCustomerSuggestions();
    } else if (e.key === 'Enter' && customerCandidates.length > 1) {
      e.preventDefault();
      chooseCustomer(customerCandidates[customerActiveIndex >= 0 ? customerActiveIndex : 0]);
    } else if (e.key === 'Escape') {
      closeCustomerSuggestions();
    }
  });

  loadMasters().catch(e => alert(e.message));
});
