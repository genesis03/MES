(() => {
  const $ = (id) => document.getElementById(id);
  const state = { defectTypes: [], selectedOutboundId: null, source: null };

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

  function defectOptions(selected = "") {
    return `<option value="">선택</option>` + state.defectTypes.map((x) => `<option value="${esc(x)}"${x === selected ? " selected" : ""}>${esc(x)}</option>`).join("");
  }

  function recompute() {
    let totalOut = 0, totalDefect = 0, totalGood = 0;
    document.querySelectorAll("#si-lots tr[data-lot-id]").forEach((tr) => {
      const source = num(tr.dataset.sourceQty);
      const input = tr.querySelector(".si-defect-qty");
      let defect = input ? num(input.value) : num(tr.dataset.defectQty);
      if (defect < 0) defect = 0;
      if (defect > source) defect = source;
      if (input && num(input.value) !== defect) input.value = defect;
      const good = source - defect;
      const goodCell = tr.querySelector(".si-good-qty");
      if (goodCell) goodCell.textContent = fmt(good);
      totalOut += source;
      totalDefect += defect;
      totalGood += good;
    });
    $("si-total-out").textContent = fmt(totalOut);
    $("si-total-defect").textContent = fmt(totalDefect);
    $("si-total-good").textContent = fmt(totalGood);
  }

  function renderSource(source) {
    state.source = source;
    const inbound = source.inbound || null;
    $("si-number").value = inbound ? inbound.inbound_no : "입고 시 자동 발번";
    $("si-date").value = inbound ? inbound.inbound_date : todayLocal();
    $("si-date").disabled = !!inbound;
    $("si-status").value = inbound ? inbound.status_name : "입고대기";
    $("si-outbound-no").value = source.outbound_no || "";
    $("si-order-no").value = source.order_no || "";
    $("si-partner").value = source.partner_name || "";
    $("si-process").value = source.processing_type_name || "";
    $("si-manager").value = source.manager_name || "";
    $("si-location").value = inbound ? inbound.storage_location : "";
    $("si-location").disabled = !!inbound;

    const inboundLots = new Map();
    if (inbound) {
      inbound.items.forEach((item) => item.lots.forEach((lot) => inboundLots.set(lot.outbound_lot_id, lot)));
    }

    let no = 0;
    const rows = [];
    source.items.forEach((item) => {
      item.lots.forEach((lot) => {
        no += 1;
        const saved = inboundLots.get(lot.outbound_lot_id);
        const defectQty = saved ? num(saved.defect_qty) : 0;
        const defectType = saved ? saved.defect_type : "";
        const childLot = saved ? saved.child_lot_no : "확정 시 자동발번";
        const disabled = inbound ? " disabled" : "";
        rows.push(`<tr data-lot-id="${lot.outbound_lot_id}" data-source-qty="${lot.source_qty}" data-defect-qty="${defectQty}">
          <td>${no}</td><td class="left">${esc(item.part_no)}</td><td class="left">${esc(item.part_name)}</td>
          <td class="left">${esc(lot.source_lot_no)}</td><td>${fmt(lot.source_qty)}</td>
          <td><input class="si-defect-qty" type="number" min="0" max="${lot.source_qty}" step="0.001" value="${defectQty}"${disabled}></td>
          <td><select class="si-defect-type"${disabled}>${defectOptions(defectType)}</select></td>
          <td class="si-good-qty">${fmt(num(lot.source_qty) - defectQty)}</td><td class="left">${esc(childLot)}</td><td>${esc(item.unit)}</td>
        </tr>`);
      });
    });
    $("si-lots").innerHTML = rows.length ? rows.join("") : `<tr><td colspan="10">입고할 LOT가 없습니다.</td></tr>`;

    document.querySelectorAll(".si-defect-qty").forEach((el) => el.addEventListener("input", () => {
      const tr = el.closest("tr");
      const type = tr.querySelector(".si-defect-type");
      if (num(el.value) <= 0) type.value = "";
      recompute();
    }));
    recompute();

    $("si-confirm").disabled = !!inbound || rows.length === 0;
    $("si-cancel").disabled = !inbound || inbound.status !== "RECEIVED";
  }

  async function loadOutboundList() {
    const keyword = $("si-search").value.trim();
    $("si-outbounds").innerHTML = `<tr><td colspan="8">조회 중...</td></tr>`;
    try {
      const data = await api(`/api/subcontract/inbound/outbounds?keyword=${encodeURIComponent(keyword)}`);
      $("si-outbounds").innerHTML = data.items.length ? data.items.map((row, i) => `<tr class="si-out-row" data-id="${row.outbound_id}">
        <td>${i + 1}</td><td>${esc(row.outbound_no)}</td><td>${esc(row.outbound_date)}</td><td>${esc(row.order_no)}</td>
        <td class="left">${esc(row.partner_name)}</td><td>${esc(row.processing_type_name)}</td><td>${fmt(row.total_qty)}</td>
        <td class="${row.inbound_id ? "st-done" : "st-wait"}">${esc(row.inbound_status_name)}</td></tr>`).join("") : `<tr><td colspan="8">조회 결과가 없습니다.</td></tr>`;
      document.querySelectorAll(".si-out-row").forEach((tr) => tr.addEventListener("click", () => {
        document.querySelectorAll(".si-out-row").forEach((x) => x.classList.remove("selected"));
        tr.classList.add("selected");
        state.selectedOutboundId = Number(tr.dataset.id);
        $("si-modal-apply").disabled = false;
      }));
    } catch (e) {
      $("si-outbounds").innerHTML = `<tr><td colspan="8">${esc(e.message)}</td></tr>`;
    }
  }

  async function applySelectedOutbound() {
    if (!state.selectedOutboundId) return;
    try {
      const source = await api(`/api/subcontract/inbound/outbound/${state.selectedOutboundId}`);
      renderSource(source);
      $("si-modal").hidden = true;
      message(source.inbound ? "입고완료 이력을 불러왔습니다." : "외주가공 출고건을 불러왔습니다.");
    } catch (e) {
      message(e.message, true);
    }
  }

  async function confirmInbound() {
    if (!state.source) return;
    const location = $("si-location").value;
    if (!location) return message("입고 저장위치를 선택하세요.", true);

    const lotResults = [];
    for (const tr of document.querySelectorAll("#si-lots tr[data-lot-id]")) {
      const sourceQty = num(tr.dataset.sourceQty);
      const defectQty = num(tr.querySelector(".si-defect-qty").value);
      const defectType = tr.querySelector(".si-defect-type").value;
      if (defectQty < 0 || defectQty > sourceQty) return message("불량수량은 0 이상 출고수량 이하로 입력하세요.", true);
      if (defectQty > 0 && !defectType) return message("불량수량이 있는 LOT는 불량유형을 선택하세요.", true);
      lotResults.push({ outbound_lot_id: Number(tr.dataset.lotId), defect_qty: defectQty, defect_type: defectQty > 0 ? defectType : null });
    }

    if (!confirm("외주가공 입고를 확정하시겠습니까? 확정 시 신규 LOT가 생성됩니다.")) return;
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
      message(`입고 완료: ${inbound.inbound_no}`);
    } catch (e) {
      $("si-confirm").disabled = false;
      message(e.message, true);
    }
  }

  async function cancelInbound() {
    const inbound = state.source && state.source.inbound;
    if (!inbound) return;
    if (!confirm(`${inbound.inbound_no} 입고를 취소하시겠습니까?`)) return;
    $("si-cancel").disabled = true;
    try {
      await api(`/api/subcontract/inbound/${inbound.id}/cancel`, { method: "POST" });
      const refreshed = await api(`/api/subcontract/inbound/outbound/${state.source.outbound_id}`);
      renderSource(refreshed);
      message("입고가 취소되었습니다.");
    } catch (e) {
      $("si-cancel").disabled = false;
      message(e.message, true);
    }
  }

  async function init() {
    $("si-date").value = todayLocal();
    try {
      const data = await api("/api/subcontract/inbound/defect-types");
      state.defectTypes = data.items || [];
    } catch (e) {
      message(e.message, true);
    }

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
    $("si-confirm").addEventListener("click", confirmInbound);
    $("si-cancel").addEventListener("click", cancelInbound);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
