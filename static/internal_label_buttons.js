(() => {
  'use strict';

  const state = {
    purchaseId: null,
    performanceId: null,
    subcontractOutboundId: null,
    subcontractInboundId: null,
    packingId: null,
    packingLots: [],
    directShipmentId: null,
  };

  const $ = (id) => document.getElementById(id);
  const enable = (id, on) => { const el = $(id); if (el) el.disabled = !on; };
  const openLabels = (source, id, extra = '') => {
    if (!id) return;
    const suffix = extra ? `?${extra}&auto=1` : '?auto=1';
    window.open(`/internal-labels/${source}/${id}${suffix}`, '_blank', 'width=980,height=900');
  };

  function refreshButtons() {
    enable('pi-label', !!state.purchaseId);
    enable('production-label-btn', !!state.performanceId);
    enable('ob-label', !!state.subcontractOutboundId);
    enable('si-label', !!state.subcontractInboundId);
    enable('pack-label-one', !!state.packingId && state.packingLots.length > 0);
    enable('pack-label-all', !!state.packingId);
    enable('direct-internal-label-btn', !!state.directShipmentId);
  }

  function handleJson(path, method, data) {
    const upperMethod = String(method || 'GET').toUpperCase();

    if (/^\/api\/purchase\/inbound\/drafts\/\d+\/confirm$/.test(path) && upperMethod === 'POST') {
      state.purchaseId = Number(data?.id || 0) || null;
    } else if (/^\/api\/purchase\/inbounds\/\d+$/.test(path) && data?.id) {
      state.purchaseId = String(data.status || '').toUpperCase() === 'CONFIRMED' ? Number(data.id) : null;
    } else if (/^\/api\/purchase\/inbounds\/\d+$/.test(path) && upperMethod === 'PUT') {
      state.purchaseId = String(data?.status || '').toUpperCase() === 'CONFIRMED' ? Number(data.id || 0) || null : null;
    }

    if (/^\/api\/production-run\/\d+\/complete$/.test(path) && upperMethod === 'POST') {
      state.performanceId = Number(data?.performance_id || 0) || null;
    } else if (/^\/api\/production-run\/\d+$/.test(path) && upperMethod === 'GET') {
      state.performanceId = String(data?.status || '').toUpperCase() === 'COMPLETED'
        ? Number(data?.performance_id || 0) || null
        : null;
    } else if (/^\/api\/production-run\/\d+\/cancel$/.test(path) && upperMethod === 'POST') {
      state.performanceId = null;
    }

    if (path === '/api/subcontract/outbound' && upperMethod === 'POST') {
      state.subcontractOutboundId = String(data?.status || '').toUpperCase() === 'OUTBOUND' ? Number(data.id || 0) || null : null;
    } else if (/^\/api\/subcontract\/outbound\/order\/\d+$/.test(path)) {
      const outbound = data?.outbound;
      state.subcontractOutboundId = outbound && String(outbound.status || '').toUpperCase() === 'OUTBOUND' ? Number(outbound.id || 0) || null : null;
    } else if (/^\/api\/subcontract\/outbound\/\d+$/.test(path) && upperMethod === 'GET') {
      state.subcontractOutboundId = String(data?.status || '').toUpperCase() === 'OUTBOUND' ? Number(data.id || 0) || null : null;
    } else if (/^\/api\/subcontract\/outbound\/\d+\/cancel$/.test(path) && upperMethod === 'POST') {
      state.subcontractOutboundId = null;
    }

    if (path === '/api/subcontract/inbound' && upperMethod === 'POST') {
      state.subcontractInboundId = String(data?.status || '').toUpperCase() === 'RECEIVED' ? Number(data.id || 0) || null : null;
    } else if (/^\/api\/subcontract\/inbound\/outbound\/\d+$/.test(path)) {
      const active = Array.isArray(data?.inbounds) ? data.inbounds.find(x => String(x.status || '').toUpperCase() === 'RECEIVED') : null;
      state.subcontractInboundId = active ? Number(active.id || 0) || null : null;
    } else if (/^\/api\/subcontract\/inbound\/\d+\/cancel$/.test(path) && upperMethod === 'POST') {
      state.subcontractInboundId = null;
    }

    if (path === '/api/packing' && upperMethod === 'POST') {
      state.packingId = Number(data?.id || 0) || null;
      state.packingLots = Array.isArray(data?.waiting_lots) ? data.waiting_lots.filter(Boolean) : [];
    }

    if (path === '/api/sales/shipping-entry/direct-confirm' && upperMethod === 'POST') {
      state.directShipmentId = Number(data?.shipment_id || 0) || null;
    } else if (/^\/api\/shipping\/inquiry\/\d+$/.test(path) && upperMethod === 'GET') {
      const hasDirect = Array.isArray(data?.items) && data.items.some(item => Array.isArray(item.direct_lots) && item.direct_lots.length > 0);
      state.directShipmentId = hasDirect ? Number(data.id || 0) || null : null;
    }

    refreshButtons();
  }

  const originalFetch = window.fetch.bind(window);
  window.fetch = async function(input, init = {}) {
    const response = await originalFetch(input, init);
    try {
      const rawUrl = typeof input === 'string' ? input : input?.url;
      const url = new URL(rawUrl, window.location.origin);
      const method = init?.method || (typeof input !== 'string' && input?.method) || 'GET';
      if (response.ok && url.origin === window.location.origin) {
        response.clone().json().then(data => handleJson(url.pathname, method, data)).catch(() => {});
      }
    } catch (_) {}
    return response;
  };

  function installDirectButton() {
    if (location.pathname !== '/sales/shipping' || $('direct-internal-label-btn')) return;
    const actions = document.querySelector('.ship-actions');
    if (!actions) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'direct-internal-label-btn';
    button.className = 'btn btn-light';
    button.textContent = '내부 라벨 출력';
    button.disabled = true;
    button.addEventListener('click', () => openLabels('direct-shipment', state.directShipmentId));
    actions.insertBefore(button, actions.querySelector('#confirmBtn'));
  }

  function replaceOutboundLabelHandler() {
    const old = $('ob-label');
    if (!old || old.dataset.internalBound === '1') return;
    const button = old.cloneNode(true);
    button.dataset.internalBound = '1';
    button.textContent = '내부 LOT 라벨 출력';
    button.disabled = !state.subcontractOutboundId;
    old.replaceWith(button);
    button.addEventListener('click', () => openLabels('subcontract-outbound', state.subcontractOutboundId));
  }

  function bindButtons() {
    $('pi-label')?.addEventListener('click', () => openLabels('purchase', state.purchaseId));
    $('production-label-btn')?.addEventListener('click', () => openLabels('production', state.performanceId));
    $('si-label')?.addEventListener('click', () => openLabels('subcontract-inbound', state.subcontractInboundId));
    $('pack-label-all')?.addEventListener('click', () => openLabels('packing', state.packingId));
    $('pack-label-one')?.addEventListener('click', () => {
      if (!state.packingId || !state.packingLots.length) return;
      let lot = state.packingLots[0];
      if (state.packingLots.length > 1) {
        const entered = window.prompt(`출력할 출고대기 LOT를 입력하세요.\n${state.packingLots.join('\n')}`, lot);
        if (!entered) return;
        lot = entered.trim();
        if (!state.packingLots.includes(lot)) {
          alert('금회 포장으로 생성된 LOT가 아닙니다.');
          return;
        }
      }
      openLabels('packing', state.packingId, `lot_no=${encodeURIComponent(lot)}`);
    });

    $('pi-new')?.addEventListener('click', () => { state.purchaseId = null; refreshButtons(); });
    document.querySelector('button[onclick="resetPage()"]')?.addEventListener('click', () => {
      state.packingId = null; state.packingLots = []; refreshButtons();
    });
    $('newEntryBtn')?.addEventListener('click', () => { state.directShipmentId = null; refreshButtons(); });

    installDirectButton();
    replaceOutboundLabelHandler();
    refreshButtons();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindButtons, {once:true});
  else bindButtons();
})();
