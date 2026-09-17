(() => {
  'use strict';

  const $ = id => document.getElementById(id);
  let currentOrderId = Number(new URLSearchParams(location.search).get('edit')) || null;

  function setDeleteState() {
    const button = $('so-delete');
    if (button) button.disabled = !currentOrderId;
  }

  function setMessage(text) {
    const node = $('so-message');
    if (node) node.textContent = text || '';
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const input = args[0];
      const options = args[1] || {};
      const url = typeof input === 'string' ? input : String(input?.url || '');
      const method = String(options.method || 'GET').toUpperCase();
      const isOrderApi = /^\/api\/subcontract\/orders(?:\/\d+)?(?:\?.*)?$/.test(url);
      if (response.ok && isOrderApi && ['GET', 'POST', 'PUT'].includes(method)) {
        const data = await response.clone().json();
        if (data?.id) {
          currentOrderId = Number(data.id);
          setDeleteState();
        }
      }
    } catch (_) {
      // 본 화면 기능에는 영향을 주지 않도록 추적 실패는 무시합니다.
    }
    return response;
  };

  async function deleteOrder() {
    if (!currentOrderId) return;
    const orderNo = $('so-number')?.value || '';
    if (!confirm(`${orderNo || '현재 외주가공 발주'}를 삭제하시겠습니까?\n출고/입고 이력이 있는 발주는 삭제할 수 없습니다.\nLOT 배정만 있는 경우 삭제 시 예약수량이 즉시 해제됩니다.`)) return;

    const button = $('so-delete');
    button.disabled = true;
    setMessage('발주 삭제 중...');
    try {
      const response = await originalFetch(`/api/subcontract/orders/${currentOrderId}`, {method: 'DELETE'});
      let data = {};
      try { data = await response.json(); } catch (_) {}
      if (!response.ok) throw new Error(data.detail || '발주를 삭제하지 못했습니다.');

      currentOrderId = null;
      setDeleteState();
      const newButton = $('so-new');
      if (newButton) newButton.click();
      setMessage(data.message || '외주가공 발주를 삭제했습니다.');
    } catch (error) {
      setMessage(error.message);
      setDeleteState();
    }
  }

  function init() {
    const button = $('so-delete');
    if (!button) return;
    setDeleteState();
    button.addEventListener('click', deleteOrder);
    $('so-new')?.addEventListener('click', () => {
      currentOrderId = null;
      setDeleteState();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})();
