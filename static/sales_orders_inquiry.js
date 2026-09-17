const $ = (id) => document.getElementById(id);
let orders = [];

async function getJson(url, options = {}) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.message || '처리에 실패했습니다.');
  return data;
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

function matchesKeyword(order, keyword) {
  if (!keyword) return true;
  const key = keyword.toLowerCase();
  if (String(order.order_no || '').toLowerCase().includes(key)) return true;
  if (String(order.customer_name || '').toLowerCase().includes(key)) return true;
  return (order.items || []).some(item => String(item.part_no || '').toLowerCase().includes(key));
}

async function searchOrders() {
  const p = new URLSearchParams();
  if ($('statusFilter').value) p.set('status', $('statusFilter').value);

  const rows = await getJson('/api/sales/orders?' + p.toString());
  const keyword = $('keywordFilter').value.trim();
  orders = rows.filter(order => matchesKeyword(order, keyword));
  render();
}

function canDeleteOrder(order) {
  return order.status === 'ORDERED' && (order.items || []).every(i => Number(i.shipped_qty || 0) <= 0);
}

function render() {
  const body = $('orderBody');
  body.innerHTML = '';
  if (!orders.length) {
    body.innerHTML = '<tr><td colspan="11" class="muted">조회 결과가 없습니다.</td></tr>';
    return;
  }
  orders.forEach((o, idx) => {
    const tr = document.createElement('tr');
    const deletable = canDeleteOrder(o);
    tr.innerHTML = `<td>${o.order_no}</td><td>${o.order_date}</td><td>${o.customer_name}</td><td>${orderTypeText(o.order_type || 'NORMAL')}</td><td>${transactionTypeText(o.transaction_type || 'PAID')}</td><td>${o.delivery_due_date || ''}</td><td class="status">${statusText(o.status)}</td><td>${o.manager_name || ''}</td><td>${o.items.length}</td><td><button type="button" class="detail-btn" data-idx="${idx}">상세</button></td><td><button type="button" class="btn-delete" data-delete-idx="${idx}" ${deletable ? '' : 'disabled title="출고 이력이 있는 수주는 삭제할 수 없습니다."'}>삭제</button></td>`;
    body.appendChild(tr);
    tr.querySelector('.detail-btn').addEventListener('click', () => toggleDetail(idx, tr));
    const deleteBtn = tr.querySelector('.btn-delete');
    if (deletable) deleteBtn.addEventListener('click', () => deleteOrder(idx));
  });
}

function toggleDetail(idx, anchor) {
  const next = anchor.nextElementSibling;
  if (next && next.classList.contains('detail-row')) { next.remove(); return; }
  const o = orders[idx];
  const detail = document.createElement('tr');
  detail.className = 'detail-row';
  detail.innerHTML = `<td colspan="11"><div style="margin-bottom:7px;font-weight:700">수주구분: ${orderTypeText(o.order_type || 'NORMAL')} / 거래구분: ${transactionTypeText(o.transaction_type || 'PAID')}</div><table class="detail-table"><thead><tr><th>품번</th><th>품명</th><th>수주수량</th><th>기출고</th><th>잔량</th><th>단위</th><th>납기일</th><th>상태</th></tr></thead><tbody>${o.items.map(i => `<tr><td>${i.part_no}</td><td>${i.part_name || ''}</td><td>${i.order_qty}</td><td>${i.shipped_qty}</td><td>${i.remaining_qty}</td><td>${i.unit}</td><td>${i.delivery_date || ''}</td><td>${statusText(i.status)}</td></tr>`).join('')}</tbody></table></td>`;
  anchor.after(detail);
}

async function deleteOrder(idx) {
  const order = orders[idx];
  if (!order) return;
  if (!canDeleteOrder(order)) return alert('출고 이력이 있는 수주는 삭제할 수 없습니다.');
  if (!confirm(`수주번호 ${order.order_no}를 삭제하시겠습니까?\n수주 품목도 함께 삭제됩니다.`)) return;

  try {
    const data = await getJson(`/api/sales/orders/${order.id}`, {method: 'DELETE'});
    alert(data.message || '수주가 삭제되었습니다.');
    await searchOrders();
  } catch (e) {
    alert(e.message);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $('searchBtn').addEventListener('click', () => searchOrders().catch(e => alert(e.message)));
  $('keywordFilter').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchOrders().catch(err => alert(err.message));
  });
  searchOrders().catch(e => alert(e.message));
});
