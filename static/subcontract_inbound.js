(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    selectedOutboundId: null,
    source: null,
    entries: new Map(),
    editingLotId: null,
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

  function generatedLotText(lot, entry) {
    if (!entry || num(entry.inbound_qty) <= 0) return "-";
    if (num(lot.received_qty) === 0 && num(entry.inbound_qty) === num(lot.allocated_qty)) return "원 LOT 유지";
    return "LZ 자동발번";
  }

  function recompute() {
    let allocated = 0, received = 0, remaining = 0, current = 0;
    if (state.source) {
      state.source.items.forEach((item) => item.lots.forEach((lot) => {
        allocated += num(lot.allocated_qty);
        received += num(lot.received_qty);
        remaining += num(lot.remaining_qty);
        const entry = state.entries.get(lot.outbound_lot_id);
        current += entry ? num(entry.inbound_qty) : 0;
      }));
    }
    $("si-total-out").textContent = fmt(allocated);
    $("si-total-received").textContent = fmt(received);
    $("si-total-remain").textContent = fmt(remaining);
    $("si-total-current").textContent = fmt(current);
    $("si-confirm").disabled = current <= 0;
  }

  function renderLots() {
    if (!state.source) {
      $("si-lots").innerHTML = `<tr><td colspan="13">외주가공 출고건을 불러오세요.</td></tr>`;
      recompute();
      return;
    }
    let no = 0;
    const rows = [];
    state.source.items.forEach((item) => {
      item.lots.forEach((lot) => {
        no += 1;
        const entry = state.entries.get(lot.outbound_lot_id);
        const inboundQty = entry ? num(entry.inbound_qty) : 0;
        const supplierLot = entry ? entry.supplier_lot_no : "";
        const sampleQty = entry ? num(entry.sample_qty) : 0;
        const disabled = num(lot.remaining_qty) <= 0 ? " disabled" : "";
        rows.push(`<tr>
          <td>${no}</td>
          <td class="left">${esc(item.part_no)}</td>
          <td class="left">${esc(item.part_name)}</td>
          <td class="left">${esc(lot.source_lot_no)}</td>
          <td>${fmt(lot.allocated_qty)}</td>
          <td>${fmt(lot.received_qty)}</td>
          <td>${fmt(lot.remaining_qty)}</td>
          <td>${inboundQty ? fmt(inboundQty) : "-"}</td>
          <td class="left">${esc(supplierLot || "-")}</td>
          <td>${sampleQty ? fmt(sampleQty) : "-"}</td>
          <td class="left">${esc(generatedLotText(lot, entry))}</td>
          <td>${esc(item.unit)}</td>
          <td><button class="si-btn si-lot-btn" type="button" data-lot-id="${lot.outbound_lot_id}"${disabled}>${entry ? "수정" : "입력"}</button></td>
        </tr>`);
      });
    });
    $("si-lots").innerHTML = rows.length ? rows.join("") : `<tr><td colspan="13">입고할 LOT가 없습니다.</td></tr>`;
    document.querySelectorAll(".si-lot-btn").forEach((btn) => btn.addEventListener("click", () => openLotPopup(Number(btn.dataset.lotId))));
    recompute();
  }

  function renderSource(source) {
    state.source = source;
    state.entries.clear();
    $("si-number").value = "입고 시 자동 발번";
    $("si-date").value = todayLocal();
    $("si-date").disabled = false;
    $("si-status").value = statusText(source.inbound_status);
    $("si-outbound-no").value = source.outbound_no || "";
    $("si-order-no").value = source.order_no || "";
    $("si-partner").value = source.partner_name || "";
    $("si-process").value = source.processing_type_name || "";
    $("si-manager").value = source.manager_name || "";
    $("si-location").disabled = false;
    const active = latestActiveInbound();
    $("si-cancel").disabled = !active;
    renderLots();
  }

  function findLot(lotId) {
    if (!state.source) return null;
    for (const item of state.source.items) {
      const lot = item.lots.find((row) => row.outbound_lot_id === lotId);
      if (lot) return lot;
    }
    return null;
  }

  function openLotPopup(lotId) {
    const lot = findLot(lotId);
    if (!lot || num(lot.remaining_qty) <= 0) return;
    state.editingLotId = lotId;
    const entry = state.entries.get(lotId) || { inbound_qty: lot.remaining_qty, supplier_lot_no: "", sample_qty: 0 };
    $("si-lot-source").value = lot.source_lot_no || "";
    $("si-lot-allocated").value = fmt(lot.allocated_qty);
    $("si-lot-received").value = fmt(lot.received_qty);
    $("si-lot-remaining").value = fmt(lot.remaining_qty);
    $("si-lot-inbound").max = String(lot.remaining_qty);
    $("si-lot-inbound").value = entry.inbound_qty;
    $("si-lot-supplier").value = entry.supplier_lot_no || "";
    $("si-lot-sample").max = String(lot.remaining_qty);
    $("si-lot-sample").value = entry.sample_qty || 0;
    $("si-lot-modal").hidden = false;
    $("si-lot-inbound").focus();
  }

  function applyLotPopup() {
    const lot = findLot(state.editingLotId);
    if (!lot) return;
    const inboundQty = num($("si-lot-inbound").value);
    const sampleQty = num($("si-lot-sample").value);
    const supplierLot = $("si-lot-supplier").value.trim();
    if (inboundQty <= 0) return message("금회 입고수량은 0보다 커야 합니다.", true);
    if (inboundQty > num(lot.remaining_qty)) return message(`입고수량은 입고 가능수량 ${fmt(lot.remaining_qty)}을 초과할 수 없습니다.`, true);
    if (sampleQty < 0 || sampleQty > inboundQty) return message("샘플수량은 0 이상 금회 입고수량 이하여야 합니다.", true);
    state.entries.set(lot.outbound_lot_id, {
      outbound_lot_id: lot.outbound_lot_id,
      inbound_qty: inboundQty,
      sample_qty: sampleQty,
      supplier_lot_no: supplierLot || null,
    });
    $("si-lot-modal").hidden = true;
    renderLots();
    message("LOT 입고수량을 반영했습니다. 입고 확정을 누르면 저장됩니다.");
  }

  function clearLotPopup() {
    if (state.editingLotId) state.entries.delete(state.editingLotId);
    $("si-lot-modal").hidden = true;
    renderLots();
    message("해당 LOT의 금회 입고 입력을 취소했습니다.");
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
        $("si-modal-apply").disabled = false;
      }));
    } catch (e) {
      $("si-outbounds").innerHTML = `<tr><td colspan="10">${esc(e.message)}</td></tr>`;
    }
  }

  async function applySelectedOutbound() {
    if (!state.selectedOutboundId) return;
    try {
      const source = await api(`/api/subcontract/inbound/outbound/${state.selectedOutboundId}`);
      renderSource(source);
      $("si-modal").hidden = true;
      message(source.inbound_status === "PARTIAL" ? "부분입고 건을 불러왔습니다. 잔여 LOT 수량을 추가 입고할 수 있습니다." : "외주가공 출고건을 불러왔습니다.");
    } catch (e) {
      message(e.message, true);
    }
  }

  async function confirmInbound() {
    if (!state.source) return;
    const location = $("si-location").value;
    if (!location) return message("입고 저장위치를 선택하세요.", true);
    const lotResults = Array.from(state.entries.values());
    if (!lotResults.length) return message("입고할 LOT의 수량을 입력하세요.", true);
    if (!confirm("입력한 LOT 수량대로 외주가공 입고를 확정하시겠습니까?")) return;

    $("si-confirm").disabled = true;
    try {
      const inbound = await api("/api/subcontract/inbound", {
        method: "POST",
        body: JSON.stringify({
          outbound_id: state.source.outbound_id,
          inbound_date: $("si-date").value,
          storage_location: location,
          lot_results: lotResults,
        }),
      });
      const refreshed = await api(`/api/subcontract/inbound/outbound/${state.source.outbound_id}`);
      renderSource(refreshed);
      $("si-number").value = inbound.inbound_no;
      message(`입고 완료: ${inbound.inbound_no}`);
    } catch (e) {
      recompute();
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
      renderSource(refreshed);
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
      $("si-modal-apply").disabled = true;
      $("si-modal").hidden = false;
      loadOutboundList();
    });
    $("si-search-btn").addEventListener("click", loadOutboundList);
    $("si-search").addEventListener("keydown", (e) => { if (e.key === "Enter") loadOutboundList(); });
    $("si-modal-close").addEventListener("click", () => { $("si-modal").hidden = true; });
    $("si-modal-apply").addEventListener("click", applySelectedOutbound);
    $("si-lot-close").addEventListener("click", () => { $("si-lot-modal").hidden = true; });
    $("si-lot-apply").addEventListener("click", applyLotPopup);
    $("si-lot-clear").addEventListener("click", clearLotPopup);
    $("si-confirm").addEventListener("click", confirmInbound);
    $("si-cancel").addEventListener("click", cancelLatestInbound);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
