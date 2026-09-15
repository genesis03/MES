const $ = (id) => document.getElementById(id);
let itemOptions = [];

function today() {
  return new Date().toISOString().slice(0, 10);
}

async function getJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || '처리에 실패했습니다.');
  return data;
}

async function loadMasters() {
  const [customers, items] = await Promise.all([
    getJson('/api/sales/customers'),
    getJson('/api/sales/items')
  ]);
  $('customerId').innerHTML = '<option value="">선택</option>' + customers.map(x => `<option value="${x.id}">${x.partner_code} | ${x.partner_name}</option>`).join('');
  itemOptions = items;
  addRow();
}

function addRow() {
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><select class="part-select" style="width:100%;padding:7px"><option value="">선택</option>${itemOptions.map(x => `<option value="${x.part_no}" data-name="${x.part_name}" data-unit="${x.unit}">${x.part_no} | ${x.part_name}</option>`).join('')}</select></td>
    <td class="part-name"></td>
    <td><input class="order-qty" type="number" min="0.000001" step="any" style="width:100%;padding:7px"></td>
    <td class="unit">EA</td>
    <td><input class="delivery-date" type="date" style="width:100%;padding:7px"></td>
    <td><button type="button" class="btn btn-danger delete-row">삭제</button></td>`;
  tr.querySelector('.part-select').addEventListener('change', (e) => {
    const opt = e.target.selectedOptions[0];
    tr.querySelector('.part-name').textContent = opt?.dataset.name || '';
    tr.querySelector('.unit').textContent = opt?.dataset.unit || 'EA';
  });
  tr.querySelector('.delete-row').addEventListener('click', () => tr.remove());
  $('itemBody').appendChild(tr);
}

async function saveOrder() {
  const rows = [...$('itemBody').querySelectorAll('tr')];
  const items = rows.map(tr => ({
    part_no: tr.querySelector('.part-select').value,
    order_qty: Number(tr.querySelector('.order-qty').value || 0),
    delivery_date: tr.querySelector('.delivery-date').value || null
  })).filter(x => x.part_no || x.order_qty);

  if (!$('orderDate').value || !$('customerId').value) return alert('수주일자와 고객사를 입력해 주세요.');
  if (!items.length || items.some(x => !x.part_no || x.order_qty <= 0)) return alert('수주 품목과 수량을 확인해 주세요.');

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
        items
      })
    });
    alert(`${data.message}\n수주번호: ${data.order_no}`);
    location.href = '/sales/orders/inquiry';
  } catch (e) {
    alert(e.message);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('orderDate').value = today();
  $('addRowBtn').addEventListener('click', addRow);
  $('saveBtn').addEventListener('click', saveOrder);
  loadMasters().catch(e => alert(e.message));
});
