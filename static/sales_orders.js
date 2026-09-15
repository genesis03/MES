const $ = (id) => document.getElementById(id);

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

async function loadMasters() {
  const customers = await getJson('/api/sales/customers');
  $('customerId').innerHTML = '<option value="">선택</option>' + customers.map(x => `<option value="${x.id}">${esc(x.partner_code)} | ${esc(x.partner_name)}</option>`).join('');
  addRow();
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

function addRow() {
  const tr = document.createElement('tr');
  tr.dataset.partNo = '';
  tr._candidates = [];
  tr._activeIndex = -1;
  tr.innerHTML = `
    <td><div class="part-picker"><input class="part-input" autocomplete="off" placeholder="품번 입력"><div class="suggestions"></div></div></td>
    <td class="part-name"></td>
    <td><input class="order-qty" type="number" min="0.000001" step="any" style="width:100%;padding:7px"></td>
    <td class="unit">EA</td>
    <td><input class="delivery-date" type="date" style="width:100%;padding:7px"></td>
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

  tr.querySelector('.delete-row').addEventListener('click', () => tr.remove());
  $('itemBody').appendChild(tr);
}

async function saveOrder() {
  const rows = [...$('itemBody').querySelectorAll('tr')];
  const items = rows.map(tr => ({
    part_no: tr.dataset.partNo || '',
    typed_part_no: tr.querySelector('.part-input').value.trim(),
    order_qty: Number(tr.querySelector('.order-qty').value || 0),
    delivery_date: tr.querySelector('.delivery-date').value || null
  })).filter(x => x.typed_part_no || x.order_qty);

  if (!$('orderDate').value || !$('customerId').value) return alert('수주일자와 판매처를 입력해 주세요.');
  if (!items.length) return alert('수주 품목을 입력해 주세요.');
  if (items.some(x => !x.part_no || x.part_no !== x.typed_part_no || x.order_qty <= 0)) return alert('품번은 검색 결과에서 등록된 완제품/반제품을 선택하고 수량을 입력해 주세요.');

  try {
    const data = await getJson('/api/sales/orders', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        order_date: $('orderDate').value,
        delivery_due_date: $('deliveryDueDate').value || null,
        customer_id: Number($('customerId').value),
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
  document.querySelectorAll('#itemBody tr').forEach(tr => {
    if (!tr.contains(e.target)) closeSuggestions(tr);
  });
});

document.addEventListener('DOMContentLoaded', () => {
  $('orderDate').value = today();
  $('addRowBtn').addEventListener('click', addRow);
  $('saveBtn').addEventListener('click', saveOrder);
  loadMasters().catch(e => alert(e.message));
});
