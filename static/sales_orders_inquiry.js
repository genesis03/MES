const $ = (id) => document.getElementById(id);

let orders = [];
let selectedIds = new Set();
const selectedParts = [];
let candidateRows = [];
let activeIndex = -1;
let searchTimer = null;

async function getJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || '처리에 실패했습니다.');
  return data;
}

function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, m => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  }[m]));
}

function statusText(v) {
  return {ORDERED:'수주', PARTIAL:'부분출고', COMPLETED:'출고완료', CANCELLED:'취소', WAITING:'대기'}[v] || v || '';
}
function orderTypeText(v) {
  return {NORMAL:'양산', SAMPLE:'샘플', DEVELOPMENT:'개발'}[v] || v || '';
}
function transactionTypeText(v) {
  return {PAID:'유상', FREE:'무상'}[v] || v || '';
}
function canDeleteOrder(order) {
  return order.status === 'ORDERED' && (order.items || []).every(item => Number(item.shipped_qty || 0) <= 0);
}

function renderChips() {
  $('chips').innerHTML = selectedParts.map((row, index) =>
    `<span class="chip" title="${esc(row.part_name || '')}">${esc(row.part_no)}<button type="button" data-remove-part="${index}">×</button></span>`
  ).join('');

  $('chips').querySelectorAll('[data-remove-part]').forEach(btn => {
    btn.addEventListener('click', () => {
      selectedParts.splice(Number(btn.dataset.removePart), 1);
      renderChips();
      $('partSearch').focus();
    });
  });
}

function hideSuggestions() {
  $('suggestions').style.display = 'none';
  $('suggestions').innerHTML = '';
  candidateRows = [];
  activeIndex = -1;
}

function addPart(row) {
  if (!row || !row.item_id) return;
  if (!selectedParts.some(x => Number(x.item_id) === Number(row.item_id))) {
    selectedParts.push(row);
  }
  renderChips();
  $('partSearch').value = '';
  hideSuggestions();
  $('partSearch').focus();
}

function renderSuggestions() {
  const box = $('suggestions');
  if (!candidateRows.length) {
    box.innerHTML = '<div class="suggestion" style="color:#94a3b8">일치하는 품번이 없습니다.</div>';
    box.style.display = 'block';
    return;
  }

  box.innerHTML = candidateRows.map((row, index) =>
    `<div class="suggestion${index === activeIndex ? ' active' : ''}" data-part-index="${index}"><span class="pn">${esc(row.part_no)}</span><span class="name">${esc(row.part_name || '')}</span></div>`
  ).join('');
  box.style.display = 'block';

  box.querySelectorAll('[data-part-index]').forEach(el => {
    el.addEventListener('mousedown', event => {
      event.preventDefault();
      addPart(candidateRows[Number(el.dataset.partIndex)]);
    });
  });
}

async function searchPartCandidates() {
  const q = $('partSearch').value.trim();
  if (!q) {
    hideSuggestions();
    return;
  }

  try {
    const rows = await getJson('/api/sales/items?q=' + encodeURIComponent(q) + '&limit=30');
    candidateRows = (rows || []).filter(row =>
      !selectedParts.some(selected => Number(selected.item_id) === Number(row.item_id))
    );
    activeIndex = -1;
    renderSuggestions();
  } catch (e) {
    $('suggestions').innerHTML = `<div class="suggestion" style="color:#b91c1c">${esc(e.message)}</div>`;
    $('suggestions').style.display = 'block';
  }
}

async function loadCustomers() {
  const rows = await getJson('/api/sales/customers');
  $('customerFilter').innerHTML =
    '<option value="">전체</option>' +
    (rows || []).map(row =>
      `<option value="${row.id}">${esc(row.partner_name)}${row.partner_code ? ' (' + esc(row.partner_code) + ')' : ''}</option>`
    ).join('');
}

function buildSearchParams() {
  const p = new URLSearchParams();
  const startDate = $('startDate').value;
  const endDate = $('endDate').value;
  const customerId = $('customerFilter').value;
  const orderNo = $('orderNoFilter').value.trim();
  const status = $('statusFilter').value;

  if (startDate) p.set('start_date', startDate);
  if (endDate) p.set('end_date', endDate);
  if (customerId) p.set('customer_id', customerId);
  if (orderNo) p.set('order_no', orderNo);
  if (status) p.set('status', status);
  selectedParts.forEach(row => p.append('item_id', String(row.item_id)));
  return p;
}

async function searchOrders() {
  const rows = await getJson('/api/sales/orders?' + buildSearchParams().toString());
  orders = rows || [];
  selectedIds.clear();
  render();
  updateSelection();
}

function updateSelection() {
  document.querySelectorAll('.order-check').forEach(check => {
    check.checked = selectedIds.has(Number(check.value));
  });

  const deletableIds = orders.filter(canDeleteOrder).map(order => Number(order.id));
  const selectedDeletableCount = deletableIds.filter(id => selectedIds.has(id)).length;
  const allSelected = deletableIds.length > 0 && selectedDeletableCount === deletableIds.length;

  $('checkAll').checked = allSelected;
  $('checkAll').indeterminate = selectedDeletableCount > 0 && !allSelected;
  $('checkAll').disabled = deletableIds.length === 0;
  $('deleteSelectedBtn').disabled = selectedIds.size === 0;
  $('deleteSelectedBtn').textContent = `선택삭제 (${selectedIds.size})`;
}

function toggleSelected(id) {
  if (selectedIds.has(id)) selectedIds.delete(id);
  else selectedIds.add(id);
  updateSelection();
}

function render() {
  const body = $('orderBody');

  if (!orders.length) {
    body.innerHTML = '<tr><td colspan="11" class="muted">조회 결과가 없습니다.</td></tr>';
    $('summary').textContent = '총 0건';
    updateSelection();
    return;
  }

  body.innerHTML = orders.map((order, index) => {
    const deletable = canDeleteOrder(order);
    const orderUrl = '/sales/orders?order_id=' + encodeURIComponent(order.id);
    const checkbox = deletable
      ? `<input type="checkbox" class="row-check order-check" value="${order.id}">`
      : '<input type="checkbox" class="row-check" disabled title="출고 이력이 있거나 삭제할 수 없는 상태입니다.">';

    return `<tr>
      <td>${checkbox}</td>
      <td>${index + 1}</td>
      <td><a class="doc-link" href="${orderUrl}">${esc(order.order_no)}</a></td>
      <td>${esc(order.order_date || '')}</td>
      <td>${esc(order.customer_name || '')}</td>
      <td>${esc(orderTypeText(order.order_type || 'NORMAL'))}</td>
      <td>${esc(transactionTypeText(order.transaction_type || 'PAID'))}</td>
      <td>${esc(order.delivery_due_date || '')}</td>
      <td class="status">${esc(statusText(order.status))}</td>
      <td>${esc(order.manager_name || '')}</td>
      <td>${(order.items || []).length}</td>
    </tr>`;
  }).join('');

  body.querySelectorAll('.order-check').forEach(check => {
    check.addEventListener('change', () => toggleSelected(Number(check.value)));
  });

  $('summary').textContent = `총 ${orders.length.toLocaleString()}건`;
}

async function deleteSelectedOrders() {
  const ids = [...selectedIds];
  if (!ids.length) return;

  const selectedRows = orders.filter(order => selectedIds.has(Number(order.id)));
  if (!selectedRows.every(canDeleteOrder)) {
    alert('출고 이력이 있거나 삭제할 수 없는 상태의 수주가 포함되어 있습니다.');
    return;
  }

  const orderNos = selectedRows.map(row => row.order_no).join(', ');
  if (!confirm(`${selectedRows.length}건의 수주를 삭제하시겠습니까?\n${orderNos}\n수주 품목도 함께 삭제됩니다.`)) return;

  try {
    const data = await getJson('/api/sales/orders/delete-selected', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ids}),
    });
    alert(data.message || '선택한 수주를 삭제했습니다.');
    await searchOrders();
  } catch (e) {
    alert(e.message);
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  $('searchBtn').addEventListener('click', () => searchOrders().catch(e => alert(e.message)));
  $('deleteSelectedBtn').addEventListener('click', deleteSelectedOrders);

  $('checkAll').addEventListener('change', () => {
    const deletableIds = orders.filter(canDeleteOrder).map(order => Number(order.id));
    if ($('checkAll').checked) deletableIds.forEach(id => selectedIds.add(id));
    else deletableIds.forEach(id => selectedIds.delete(id));
    updateSelection();
  });

  $('orderNoFilter').addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      searchOrders().catch(err => alert(err.message));
    }
  });

  $('partSearch').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(searchPartCandidates, 180);
  });
  $('partSearch').addEventListener('keydown', e => {
    if (e.key === 'ArrowDown' && candidateRows.length) {
      e.preventDefault();
      activeIndex = Math.min(activeIndex + 1, candidateRows.length - 1);
      renderSuggestions();
    } else if (e.key === 'ArrowUp' && candidateRows.length) {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      renderSuggestions();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (candidateRows.length) addPart(candidateRows[activeIndex >= 0 ? activeIndex : 0]);
      else searchOrders().catch(err => alert(err.message));
    } else if (e.key === 'Backspace' && !$('partSearch').value && selectedParts.length) {
      selectedParts.pop();
      renderChips();
    } else if (e.key === 'Escape') {
      hideSuggestions();
    }
  });
  $('partSearch').addEventListener('focus', () => {
    if ($('partSearch').value.trim()) searchPartCandidates();
  });

  document.addEventListener('click', e => {
    if (!e.target.closest('.part-picker')) hideSuggestions();
  });

  try {
    await loadCustomers();
    await searchOrders();
  } catch (e) {
    alert(e.message);
  }
});
