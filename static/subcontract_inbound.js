(() => {
  const $ = (id) => document.getElementById(id);
  const state = {
    selectedOutboundId: null,
    source: null,
    entries: new Map(),
    editingItemId: null,
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

  function itemStats(item) {
    let allocated = 0, received = 0, available = 0, current = 0, enteredLots = 0;
    item.lots.forEach((lot) => {
      allocated += num(lot.allocated_qty);
      received += num(lot.received_qty);
      available += num(lot.remaining_qty);
      const entry = state.entries.get(lot.outbound_lot_id);
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
    $("si-confirm").disabled = current <= 0;
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
      const buttonText = s.enteredLots > 0 ? "LOT 수정" : "LOT 입고";
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
        <td><button type="button" class="si-btn si-lot-btn" data-item-id="${item.outbound_item_id}"${completed ? " disabled" : ""}>${buttonText}</button></td>
      </tr>`;
    });

    $("si-items").innerHTML = rows.length ? rows.join("") : `<tr><td colspan="12">입고할 품목이 없습니다.</td></tr>`;
    document.querySelectorAll(".si-lot-btn").forEach((btn) => btn.addEventListener("click", () => openItemLotPopup(Number(btn.dataset.itemId))));
    recomputeTotals();
  }

  function renderSource(source) {
    state.source = source;
    state.entries.clear();
    state.editingItemId = null;
    $("si-number").value = "입고 시 자동 발번";
    $("si-date").value = todayLocal();
    $("si-status").value = statusText(source.inbound_status);
    $("si-outbound-no").value = source.outbound_no || "";
    $("si-order-no").value = source.order_no || "";
    $("si-partner").value = source.partner_name || "";
    $("si-process").value = source.processing_type_name || "";
    $("si-manager").value = source.manager_name || "";
    $("si-location").value = source.external_storage_location || "";
    $("si-cancel").disabled = !latestActiveInbound();
    renderItems();
  }

  function generatedLotText(lot, inboundQty) {
    if (inboundQty <= 0) return "-";
    if (num(lot.received_qty) === 0 && inboundQty === num(lot.allocated_qty)) return "원 LOT 유지";
    return "LZ 자동발번";
  }

  function openItemLotPopup(itemId) {
    const item = itemById(itemId);
    if (!item) return;
    state.editingItemId = itemId;
    $("si-lot-item-title").innerHTML = `&nbsp; | &nbsp;<strong>${esc(item.part_no)}</strong> ${esc(item.part_name)}`;
    lotMessage("");

    const rows = item.lots.map((lot, index) => {
      const entry = state.entries.get(lot.outbound_lot_id);
      const inboundQty = entry ? num(entry.inbound_qty) : 0;
      const supplierLot = entry ? (entry.supplier_lot_no || "") : "";
      const sampleQty = entry ? num(entry.sample_qty) : 0;
      const disabled = num(lot.remaining_qty) <= 0 ? " disabled" : "";
      return `<tr data-lot-id="${lot.outbound_lot_id}" data-remaining="${lot.remaining_qty}" data-allocated="${lot.allocated_qty}" data-received="${lot.received_qty}">
        <td>${index + 1}</td>
        <td class="left">${esc(lot.source_lot_no)}</td>
        <td>${fmt(lot.allocated_qty)}</td>
        <td>${fmt(lot.received_qty)}</td>
        <td>${fmt(lot.remaining_qty)}</td>
        <td><input class="si-pop-inbound" type="number" min="0" max="${lot.remaining_qty}" step="0.001" value="${inboundQty || ""}"${disabled}></td>
        <td><input class="si-pop-supplier" type="text" maxlength="100" value="${esc(supplierLot)}"${disabled}></td>
        <td><input class="si-pop-sample" type="number" min="0" step="0.001" value="${sampleQty || 0}"${disabled}></td>
        <td class="si-pop-newlot">${esc(generatedLotText(lot, inboundQty))}</td>
      </tr>`;
    });
    $("si-lot-rows").innerHTML = rows.length ? rows.join("") : `<tr><td colspan="9">배정된 LOT가 없습니다.</td></tr>`;

    document.querySelectorAll("#si-lot-rows .si-pop-inbound").forEach((input) => input.addEventListener("input", () => {
      const tr = input.closest("tr");
      let q = num(input.value);
      const remaining = num(tr.dataset.remaining);
      if (q < 0) q = 0;
      if (q > remaining) q = remaining;
      if (num(input.value) !== q) input.value = q || "";
      const sample = tr.querySelector(".si-pop-sample");
      if (num(sample.value) > q) sample.value = q;
      const lot = item.lots.find((x) => x.outbound_lot_id === Number(tr.dataset.lotId));
      tr.querySelector(".si-pop-newlot").textContent = generatedLotText(lot, q);
    }));

    $("si-lot-modal").hidden = false;
  }

  function applyItemLotPopup() {
    const item = itemById(state.editingItemId);
    if (!item) return;
    const changes = [];

    for (const tr of document.querySelectorAll("#si-lot-rows tr[data-lot-id]")) {
      const lotId = Number(tr.dataset.lotId);
      const remaining = num(tr.dataset.remaining);
      const inboundQty = num(tr.querySelector(".si-pop-inbound").value);
      const supplierLot = tr.querySelector(".si-pop-supplier").value.trim();
      const sampleQty = num(tr.querySelector(".si-pop-sample").value);

      if (inboundQty < 0 || inboundQty > remaining) {
        return lotMessage(`금회 입고수량은 0 이상 입고가능수량 ${fmt(remaining)} 이하여야 합니다.`, true);
      }
      if (sampleQty < 0 || sampleQty > inboundQty) {
        return lotMessage("샘플수량은 금회 입고수량을 초과할 수 없습니다.", true);
      }
      changes.push({ lotId, inboundQty, supplierLot, sampleQty });
    }

    changes.forEach(({ lotId, inboundQty, supplierLot, sampleQty }) => {
      if (inboundQty > 0) {
        state.entries.set(lotId, {
          outbound_lot_id: lotId,
          inbound_qty: inboundQty,
          supplier_lot_no: supplierLot || null,
          sample_qty: sampleQty,
        });
      } else {
        state.entries.delete(lotId);
      }
    });

    $("si-lot-modal").hidden = true;
    renderItems();
    message(`${item.part_no} LOT별 입고수량을 반영했습니다. 최종 저장은 '입고 확정'에서 진행됩니다.`);
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
      renderSource(source);
      $("si-load-modal").hidden = true;
      message(source.inbound_status === "PARTIAL" ? "부분입고 건을 불러왔습니다. 품번별 LOT 입고 버튼에서 잔량을 추가 입력할 수 있습니다." : "외주가공 출고건을 불러왔습니다.");
    } catch (e) {
      message(e.message, true);
    }
  }

  async function confirmInbound() {
    if (!state.source) return;
    const location = $("si-location").value;
    if (!location) return message("입고 저장위치를 선택하세요.", true);
    const lotResults = Array.from(state.entries.values());
    if (!lotResults.length) return message("품번의 LOT 입고 버튼에서 금회 입고수량을 입력하세요.", true);
    if (!confirm("품번별로 입력한 LOT 수량을 외주가공 입고로 확정하시겠습니까?")) return;

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
      $("si-load-apply").disabled = true;
      $("si-load-modal").hidden = false;
      loadOutboundList();
    });
    $("si-search-btn").addEventListener("click", loadOutboundList);
    $("si-search").addEventListener("keydown", (e) => { if (e.key === "Enter") loadOutboundList(); });
    $("si-load-close").addEventListener("click", () => { $("si-load-modal").hidden = true; });
    $("si-load-apply").addEventListener("click", applySelectedOutbound);

    $("si-lot-close").addEventListener("click", () => { $("si-lot-modal").hidden = true; });
    $("si-lot-save").addEventListener("click", applyItemLotPopup);

    $("si-confirm").addEventListener("click", confirmInbound);
    $("si-cancel").addEventListener("click", cancelLatestInbound);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
