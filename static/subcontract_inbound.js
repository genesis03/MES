(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    selectedOutboundId: null,
    source: null,
    entries: new Map(),
    popupEntries: new Map(),
    popupItemId: null,
    viewInbound: null,
  };

  const esc = (v) => String(v ?? "").replace(/[&<>'"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
  const num = (v) => Number(v || 0);
  const fmt = (v) => num(v).toLocaleString("ko-KR", { maximumFractionDigits: 3 });

  async function api(url, options = {}) {
    const res = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    if (!res.ok) {
      let msg = `처리 중 오류가 발생했습니다. (${res.status})`;
      try {
        const body = await res.json();
        msg = body.detail || body.message || msg;
      } catch (_) {}
      throw new Error(msg);
    }
    return res.json();
  }

  function message(text, error = false) {
    $("si-message").textContent = text || "";
    $("si-message").style.color = error ? "#b91c1c" : "#475569";
  }

  function lotMessage(text, error = false) {
    $("si-lot-message").textContent = text || "";
    $("si-lot-message").style.color = error ? "#b91c1c" : "#475569";
  }

  function scanError(text = "") {
    $("si-scan-error").textContent = text;
  }

  function todayLocal() {
    const d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 10);
  }

  function latestActiveInbound() {
    if (!state.source || !Array.isArray(state.source.inbounds)) return null;
    return state.source.inbounds.find((row) => row.status === "RECEIVED") || null;
  }

  function statusText(status) {
    if (status === "COMPLETED") return "입고완료";
    if (status === "PARTIAL") return "부분입고";
    return "입고대기";
  }

  function itemById(itemId) {
    return state.source?.items.find((item) => item.outbound_item_id === itemId) || null;
  }

  function lotOwner(lotId) {
    if (!state.source) return null;
    for (const item of state.source.items) {
      const lot = item.lots.find((x) => x.outbound_lot_id === lotId);
      if (lot) return { item, lot };
    }
    return null;
  }

  function itemStats(item, entryMap = state.entries) {
    let allocated = 0, received = 0, available = 0, current = 0, enteredLots = 0;
    item.lots.forEach((lot) => {
      allocated += num(lot.allocated_qty);
      received += num(lot.received_qty);
      available += num(lot.remaining_qty);
      const entry = entryMap.get(lot.outbound_lot_id);
      if (entry && num(entry.inbound_qty) > 0) {
        current += num(entry.inbound_qty);
        enteredLots += 1;
      }
    });
    return { allocated, received, available, current, afterRemaining: Math.max(0, available - current), enteredLots };
  }

  function recomputeTotals() {
    let allocated = 0, received = 0, current = 0, afterRemaining = 0;
    if (state.source) {
      state.source.items.forEach((item) => {
        const s = itemStats(item);
        allocated += s.allocated;
        received += s.received;
        current += s.current;
        afterRemaining += s.afterRemaining;
      });
    }
    $("si-total-out").textContent = fmt(allocated);
    $("si-total-received").textContent = fmt(received);
    $("si-total-current").textContent = fmt(current);
    $("si-total-remaining").textContent = fmt(afterRemaining);
    $("si-confirm").disabled = !!state.viewInbound || current <= 0;
  }

  function renderItems() {
    if (!state.source) {
      $("si-items").innerHTML = `<tr><td colspan="12">외주가공 출고건을 불러오세요.</td></tr>`;
      recomputeTotals();
      return;
    }

    const rows = state.source.items.map((item, index) => {
      const s = itemStats(item);
      const completed = s.available <= 0;
      const status = completed ? "입고완료" : (s.received > 0 ? "부분입고" : "입고대기");
      const cls = completed ? "st-done" : (s.received > 0 ? "st-partial" : "st-wait");
      const viewOnly = completed && !!state.viewInbound;
      return `<tr>
        <td>${index + 1}</td>
        <td class="left">${esc(item.part_no)}</td>
        <td class="left">${esc(item.part_name)}</td>
        <td class="left">${esc(item.spec || "")}</td>
        <td>${esc(item.unit)}</td>
        <td>${fmt(s.allocated)}</td>
        <td>${fmt(s.received)}</td>
        <td>${fmt(s.available)}</td>
        <td class="si-item-current">${s.current ? fmt(s.current) : "-"}</td>
        <td>${s.enteredLots}/${item.lots.length}</td>
        <td class="${cls}">${status}</td>
        <td><button type="button" class="si-btn si-lot-btn" data-item-id="${item.outbound_item_id}"${completed && !viewOnly ? " disabled" : ""}>${viewOnly ? "LOT 조회" : "LOT 입고"}</button></td>
      </tr>`;
    });

    $("si-items").innerHTML = rows.length ? rows.join("") : `<tr><td colspan="12">입고할 품목이 없습니다.</td></tr>`;
    document.querySelectorAll(".si-lot-btn").forEach((btn) => btn.addEventListener("click", () => {
      const itemId = Number(btn.dataset.itemId);
      openLotPopup(itemId, state.viewInbound && itemStats(itemById(itemId)).available <= 0 ? state.viewInbound : null);
    }));
    recomputeTotals();
  }

  function renderSource(source, selectedInbound = null) {
    state.source = source;
    state.entries.clear();
    state.popupEntries.clear();
    state.popupItemId = null;
    const completed = source.inbound_status === "COMPLETED";
    state.viewInbound = selectedInbound || (completed ? source.inbound || null : null);
    const shownInbound = state.viewInbound;
    $("si-number").value = shownInbound ? shownInbound.inbound_no : "입고 시 자동 발번";
    $("si-date").value = shownInbound ? shownInbound.inbound_date : todayLocal();
    $("si-status").value = shownInbound ? shownInbound.status_name : statusText(source.inbound_status);
    $("si-outbound-no").value = source.outbound_no || "";
    $("si-order-no").value = source.order_no || "";
    $("si-partner").value = source.partner_name || "";
    $("si-process").value = source.processing_type_name || "";
    $("si-manager").value = source.manager_name || "";
    $("si-location").value = shownInbound?.storage_location || source.external_storage_location || "";
    $("si-date").disabled = !!shownInbound;
    $("si-location").disabled = !!shownInbound;
    $("si-cancel").disabled = !latestActiveInbound();
    renderItems();
  }

  function generatedLotText(lot, inboundQty) {
    if (inboundQty <= 0) return "-";
    if (num(lot.received_qty) === 0 && inboundQty === num(lot.allocated_qty)) return "원 LOT 유지";
    return "LZ 자동발번";
  }

  function cloneEntries(sourceMap) {
    const copy = new Map();
    sourceMap.forEach((value, key) => copy.set(key, { ...value }));
    return copy;
  }

  function renderPopupItems() {
    if (!state.source) return;
    $("si-popup-item-count").textContent = state.source.items.length;
    $("si-popup-items").innerHTML = state.source.items.map((item) => {
      const selected = item.outbound_item_id === state.popupItemId ? " selected" : "";
      const s = itemStats(item, state.popupEntries);
      return `<tr class="si-select-row${selected}" data-item-id="${item.outbound_item_id}">
        <td class="left">${esc(item.part_no)}</td>
        <td class="left">${esc(item.part_name)}</td>
        <td>${s.available <= 0 ? "완료" : "가공단자"}</td>
      </tr>`;
    }).join("");
    const selectedItem = itemById(state.popupItemId);
    $("si-popup-item-selected").textContent = selectedItem ? selectedItem.part_no : "-";
    document.querySelectorAll("#si-popup-items .si-select-row").forEach((tr) => tr.addEventListener("click", () => selectPopupItem(Number(tr.dataset.itemId))));
  }

  function renderSourceLots() {
    const item = itemById(state.popupItemId);
    if (!item) {
      $("si-popup-source-lots").innerHTML = `<tr><td colspan="2" class="si-empty">품번을 선택하세요.</td></tr>`;
      $("si-popup-source-count").textContent = "0";
      $("si-popup-source-total").textContent = "0";
      return;
    }
    let availableTotal = 0;
    const rows = item.lots.map((lot) => {
      const remaining = num(lot.remaining_qty);
      availableTotal += remaining;
      const already = state.popupEntries.has(lot.outbound_lot_id);
      const cls = remaining <= 0 ? " disabled" : "";
      return `<tr class="si-pick-row${cls}" data-lot-id="${lot.outbound_lot_id}">
        <td class="left">${esc(lot.source_lot_no)}${already ? " ✓" : ""}</td><td>${fmt(remaining)}</td>
      </tr>`;
    });
    $("si-popup-source-lots").innerHTML = rows.join("") || `<tr><td colspan="2" class="si-empty">출고 LOT가 없습니다.</td></tr>`;
    $("si-popup-source-count").textContent = item.lots.length;
    $("si-popup-source-total").textContent = fmt(availableTotal);
    document.querySelectorAll("#si-popup-source-lots .si-pick-row:not(.disabled)").forEach((tr) => tr.addEventListener("dblclick", () => addLotToReceive(Number(tr.dataset.lotId))));
  }

  function currentPopupEntries() {
    const item = itemById(state.popupItemId);
    if (!item) return [];
    return item.lots.map((lot) => ({ lot, entry: state.popupEntries.get(lot.outbound_lot_id) })).filter((x) => x.entry);
  }

  function renderReceiveLots() {
    const rows = currentPopupEntries();
    const readOnly = !!state.viewInbound;
    let total = 0;
    if (!rows.length) {
      $("si-popup-receive-lots").innerHTML = `<tr><td colspan="9" class="si-empty">LOT를 스캔하거나 가운데 목록을 더블클릭하세요.</td></tr>`;
    } else {
      $("si-popup-receive-lots").innerHTML = rows.map(({ lot, entry }) => {
        total += num(entry.inbound_qty);
        return `<tr data-lot-id="${lot.outbound_lot_id}">
          <td class="left">${esc(lot.source_lot_no)}</td>
          <td><input class="si-right-input si-r-supplier" maxlength="100" value="${esc(entry.supplier_lot_no || "")}"${readOnly ? " readonly" : ""}></td>
          <td>${fmt(lot.allocated_qty)}</td>
          <td>${fmt(lot.received_qty)}</td>
          <td>${fmt(lot.remaining_qty)}</td>
          <td><input class="si-right-input si-r-inbound" type="number" min="0.001" max="${lot.remaining_qty}" step="0.001" value="${entry.inbound_qty}"${readOnly ? " readonly" : ""}></td>
          <td><input class="si-right-input si-r-sample" type="number" min="0" step="0.001" value="${entry.sample_qty || 0}"${readOnly ? " readonly" : ""}></td>
          <td>${esc(readOnly ? (entry.child_lot_no || "-") : generatedLotText(lot, num(entry.inbound_qty)))}</td>
          <td>${readOnly ? "" : `<button type="button" class="si-btn si-remove-lot" data-lot-id="${lot.outbound_lot_id}">삭제</button>`}</td>
        </tr>`;
      }).join("");
    }
    $("si-popup-receive-count").textContent = rows.length;
    $("si-popup-receive-total").textContent = fmt(total);

    if (readOnly) return;
    document.querySelectorAll("#si-popup-receive-lots tr[data-lot-id]").forEach((tr) => {
      const lotId = Number(tr.dataset.lotId);
      const lot = lotOwner(lotId)?.lot;
      tr.querySelector(".si-r-inbound").addEventListener("input", (e) => {
        let q = num(e.target.value);
        if (q > num(lot.remaining_qty)) q = num(lot.remaining_qty);
        if (q < 0) q = 0;
        e.target.value = q;
        const entry = state.popupEntries.get(lotId);
        entry.inbound_qty = q;
        const sample = tr.querySelector(".si-r-sample");
        if (num(sample.value) > q) sample.value = q;
        entry.sample_qty = num(sample.value);
        tr.children[7].textContent = generatedLotText(lot, q);
        refreshPopupReceiveTotal();
      });
      tr.querySelector(".si-r-supplier").addEventListener("input", (e) => { state.popupEntries.get(lotId).supplier_lot_no = e.target.value.trim() || null; });
      tr.querySelector(".si-r-sample").addEventListener("input", (e) => {
        const entry = state.popupEntries.get(lotId);
        let q = num(e.target.value);
        if (q < 0) q = 0;
        if (q > num(entry.inbound_qty)) q = num(entry.inbound_qty);
        e.target.value = q;
        entry.sample_qty = q;
      });
    });
    document.querySelectorAll(".si-remove-lot").forEach((btn) => btn.addEventListener("click", () => {
      state.popupEntries.delete(Number(btn.dataset.lotId));
      renderSourceLots();
      renderReceiveLots();
      $("si-lot-scan").focus();
    }));
  }

  function refreshPopupReceiveTotal() {
    let total = 0;
    currentPopupEntries().forEach(({ entry }) => total += num(entry.inbound_qty));
    $("si-popup-receive-total").textContent = fmt(total);
  }

  function selectPopupItem(itemId) {
    state.popupItemId = itemId;
    scanError("");
    renderPopupItems();
    renderSourceLots();
    renderReceiveLots();
    $("si-lot-scan").value = "";
    if (!state.viewInbound) $("si-lot-scan").focus();
  }

  function addLotToReceive(lotId) {
    const owner = lotOwner(lotId);
    if (!owner) return;
    if (owner.item.outbound_item_id !== state.popupItemId) {
      scanError("선택한 품번의 LOT가 아닙니다. 해당 품번을 먼저 선택하세요.");
      return;
    }
    const lot = owner.lot;
    if (num(lot.remaining_qty) <= 0) {
      scanError("이미 전량 입고된 LOT입니다.");
      return;
    }
    if (!state.popupEntries.has(lotId)) {
      state.popupEntries.set(lotId, {
        outbound_lot_id: lotId,
        inbound_qty: num(lot.remaining_qty),
        supplier_lot_no: null,
        sample_qty: 0,
      });
    }
    scanError("");
    renderSourceLots();
    renderReceiveLots();
    $("si-lot-scan").value = "";
    $("si-lot-scan").focus();
  }

  function scanLot() {
    const code = $("si-lot-scan").value.trim();
    if (!state.popupItemId) {
      scanError("입고 품번을 먼저 선택하세요.");
      return;
    }
    if (!code) return;
    const item = itemById(state.popupItemId);
    const lot = item?.lots.find((x) => String(x.source_lot_no).trim().toUpperCase() === code.toUpperCase());
    if (!lot) {
      const anywhere = state.source?.items.some((it) => it.lots.some((x) => String(x.source_lot_no).trim().toUpperCase() === code.toUpperCase()));
      scanError(anywhere ? "선택한 품번의 LOT가 아닙니다. 해당 품번을 먼저 선택하세요." : "출고 LOT 목록에서 해당 LOT를 찾을 수 없습니다.");
      $("si-lot-scan").select();
      return;
    }
    addLotToReceive(lot.outbound_lot_id);
  }

  function openLotPopup(initialItemId, inbound = null) {
    if (!state.source) return;
    state.popupEntries = new Map();
    if (inbound) {
      (inbound.items || []).forEach((item) => {
        (item.lots || []).forEach((lot) => {
          state.popupEntries.set(Number(lot.outbound_lot_id), {
            outbound_lot_id: Number(lot.outbound_lot_id),
            inbound_qty: num(lot.inbound_qty),
            supplier_lot_no: lot.supplier_lot_no || null,
            sample_qty: num(lot.sample_qty),
            child_lot_no: lot.child_lot_no || "",
          });
        });
      });
    } else {
      state.popupEntries = cloneEntries(state.entries);
    }
    state.popupItemId = initialItemId || state.source.items.find((x) => itemStats(x).available > 0)?.outbound_item_id || state.source.items[0]?.outbound_item_id || null;
    $("si-scan-wrap").hidden = !!inbound;
    $("si-lot-save").hidden = !!inbound;
    lotMessage(inbound ? `입고번호 ${inbound.inbound_no}의 LOT 입고 내역입니다. 확정된 입고는 조회만 가능합니다.` : "");
    scanError("");
    renderPopupItems();
    renderSourceLots();
    renderReceiveLots();
    $("si-lot-modal").hidden = false;
    if (!inbound) setTimeout(() => $("si-lot-scan").focus(), 50);
  }

  function applyLotPopup() {
    for (const [lotId, entry] of state.popupEntries.entries()) {
      const owner = lotOwner(lotId);
      if (!owner) continue;
      const q = num(entry.inbound_qty);
      const sample = num(entry.sample_qty);
      if (q <= 0 || q > num(owner.lot.remaining_qty)) {
        lotMessage(`${owner.lot.source_lot_no} 입고수량은 0보다 크고 입고가능수량 이하여야 합니다.`, true);
        return;
      }
      if (sample < 0 || sample > q) {
        lotMessage(`${owner.lot.source_lot_no} 샘플수량은 입고수량을 초과할 수 없습니다.`, true);
        return;
      }
      if (!String(entry.supplier_lot_no || "").trim()) {
        lotMessage(`${owner.lot.source_lot_no} 공급사 외주 LOT를 입력해 주세요.`, true);
        return;
      }
    }
    state.entries = cloneEntries(state.popupEntries);
    $("si-lot-modal").hidden = true;
    renderItems();
    message("LOT별 금회 입고 내용을 반영했습니다. 상단 '입고 확정'을 누르면 저장됩니다.");
  }

  async function loadInboundById(inboundId) {
    const inbound = await api(`/api/subcontract/inbound/${encodeURIComponent(inboundId)}`);
    const source = await api(`/api/subcontract/inbound/outbound/${encodeURIComponent(inbound.outbound_id)}`);
    renderSource(source, inbound);
    $("si-inbound-search").value = inbound.inbound_no || "";
    message(`입고번호 ${inbound.inbound_no} 조회 완료. 확정 LOT는 'LOT 조회'에서 확인할 수 있습니다.`);
  }

  async function searchInboundNo() {
    const keyword = $("si-inbound-search").value.trim();
    if (!keyword) return message("조회할 입고번호를 입력하세요.", true);
    try {
      const data = await api(`/api/subcontract/inbound/lookup?inbound_no=${encodeURIComponent(keyword)}`);
      if (!data.items?.length) return message("해당 입고번호를 찾을 수 없습니다.", true);
      const exact = data.items.find((x) => String(x.inbound_no).toUpperCase() === keyword.toUpperCase());
      if (!exact && data.items.length > 1) {
        return message(`입고번호가 여러 건 검색되었습니다: ${data.items.map(x=>x.inbound_no).join(", ")}`, true);
      }
      await loadInboundById((exact || data.items[0]).id);
    } catch (e) {
      message(e.message, true);
    }
  }

  async function loadOutboundList() {
    const keyword = $("si-search").value.trim();
    $("si-outbounds").innerHTML = `<tr><td colspan="10">조회 중...</td></tr>`;
    try {
      const data = await api(`/api/subcontract/inbound/outbounds?keyword=${encodeURIComponent(keyword)}`);
      $("si-outbounds").innerHTML = data.items.length ? data.items.map((row, i) => {
        const cls = row.inbound_status === "COMPLETED" ? "st-done" : (row.inbound_status === "PARTIAL" ? "st-partial" : "st-wait");
        return `<tr class="si-out-row" data-id="${row.outbound_id}">
          <td>${i + 1}</td><td>${esc(row.outbound_no)}</td><td>${esc(row.outbound_date)}</td><td>${esc(row.order_no)}</td>
          <td class="left">${esc(row.partner_name)}</td><td>${esc(row.processing_type_name)}</td><td>${fmt(row.total_qty)}</td>
          <td>${fmt(row.received_qty)}</td><td>${fmt(row.remaining_qty)}</td><td class="${cls}">${esc(row.inbound_status_name)}</td></tr>`;
      }).join("") : `<tr><td colspan="10">조회 결과가 없습니다.</td></tr>`;
      document.querySelectorAll(".si-out-row").forEach((tr) => tr.addEventListener("click", () => {
        document.querySelectorAll(".si-out-row").forEach((x) => x.classList.remove("selected"));
        tr.classList.add("selected");
        state.selectedOutboundId = Number(tr.dataset.id);
        $("si-load-apply").disabled = false;
      }));
    } catch (e) {
      $("si-outbounds").innerHTML = `<tr><td colspan="10">${esc(e.message)}</td></tr>`;
    }
  }

  async function applySelectedOutbound() {
    if (!state.selectedOutboundId) return;
    try {
      const source = await api(`/api/subcontract/inbound/outbound/${state.selectedOutboundId}`);
      renderSource(source, null);
      $("si-load-modal").hidden = true;
      message(source.inbound_status === "PARTIAL" ? "부분입고 건을 불러왔습니다. 품번별 LOT 입고에서 잔량을 추가 입력할 수 있습니다." : "외주가공 출고건을 불러왔습니다.");
    } catch (e) {
      message(e.message, true);
    }
  }

  async function confirmInbound() {
    if (!state.source) return;
    const location = $("si-location").value;
    if (!location) return message("입고 저장위치를 선택하세요.", true);
    const lotResults = Array.from(state.entries.values());
    if (!lotResults.length) return message("LOT 입고에서 금회 입고 LOT를 입력하세요.", true);
    if (!confirm("입력한 LOT 수량대로 외주가공 입고를 확정하시겠습니까?")) return;

    $("si-confirm").disabled = true;
    try {
      const inbound = await api("/api/subcontract/inbound", {
        method: "POST",
        body: JSON.stringify({ outbound_id: state.source.outbound_id, inbound_date: $("si-date").value, storage_location: location, lot_results: lotResults }),
      });
      const refreshed = await api(`/api/subcontract/inbound/outbound/${state.source.outbound_id}`);
      renderSource(refreshed, inbound);
      message(`입고 완료: ${inbound.inbound_no}`);
    } catch (e) {
      recomputeTotals();
      message(e.message, true);
    }
  }

  async function cancelLatestInbound() {
    const inbound = latestActiveInbound();
    if (!inbound) return;
    if (!confirm(`${inbound.inbound_no} 입고를 취소하시겠습니까?`)) return;
    $("si-cancel").disabled = true;
    try {
      await api(`/api/subcontract/inbound/${inbound.id}/cancel`, { method: "POST" });
      const refreshed = await api(`/api/subcontract/inbound/outbound/${state.source.outbound_id}`);
      renderSource(refreshed, null);
      message("최근 입고가 취소되었습니다.");
    } catch (e) {
      $("si-cancel").disabled = false;
      message(e.message, true);
    }
  }

  function init() {
    $("si-date").value = todayLocal();
    $("si-load").addEventListener("click", () => {
      state.selectedOutboundId = null;
      $("si-load-apply").disabled = true;
      $("si-load-modal").hidden = false;
      loadOutboundList();
    });
    $("si-search-btn").addEventListener("click", loadOutboundList);
    $("si-inbound-search-btn").addEventListener("click", searchInboundNo);
    $("si-inbound-search").addEventListener("keydown", (e) => { if (e.key === "Enter") searchInboundNo(); });
    $("si-search").addEventListener("keydown", (e) => { if (e.key === "Enter") loadOutboundList(); });
    $("si-load-close").addEventListener("click", () => { $("si-load-modal").hidden = true; });
    $("si-load-apply").addEventListener("click", applySelectedOutbound);
    $("si-lot-scan").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); scanLot(); } });
    $("si-lot-close").addEventListener("click", () => { $("si-lot-modal").hidden = true; });
    $("si-lot-save").addEventListener("click", applyLotPopup);
    $("si-confirm").addEventListener("click", confirmInbound);
    $("si-cancel").addEventListener("click", cancelLatestInbound);
    const inboundId = new URLSearchParams(location.search).get("inbound_id");
    if (inboundId) loadInboundById(inboundId).catch((e) => message(e.message, true));
  }

  document.addEventListener("DOMContentLoaded", init);
})();
