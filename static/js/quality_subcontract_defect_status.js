(() => {
    const $ = id => document.getElementById(id);
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const fmt = value => value == null ? '—' : Number(value).toLocaleString('ko-KR', {maximumFractionDigits: 6});
    const numberCell = value => '<td class="num">' + fmt(value) + '</td>';
    let requestNumber = 0;
    function defaults() {
        const today = new Date(), start = new Date(today);
        start.setDate(start.getDate() - 30);
        $('sd-start').value = MesLocalDate.format(start);
        $('sd-end').value = MesLocalDate.format(today);
        $('sd-part').value = ''; $('sd-partner').value = '';
    }
    async function search() {
        const request = ++requestNumber;
        const params = new URLSearchParams({limit:'5000'});
        for (const [field, id] of [['start_date','sd-start'],['end_date','sd-end'],['part_no','sd-part'],['partner_name','sd-partner']]) {
            if ($(id).value.trim()) params.set(field, $(id).value.trim());
        }
        $('sd-note').textContent = '조회 중…';
        try {
            const response = await fetch('/api/quality/defect-status/subcontract?' + params);
            const data = await response.json();
            if (request !== requestNumber) return;
            if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '조회하지 못했습니다.');
            const types = data.defect_types, totals = data.totals;
            const headers = ['구분','일자','입고번호','품번','품명','공정','거래처','LOT NO','입고수량','양품수량','불량 계'];
            const head = '<thead><tr>' + [...headers, ...types.map(t => t.name)].map(h => '<th>' + esc(h) + '</th>').join('') + '</tr></thead>';
            const body = data.items.map(row => '<tr>' +
                [row.record_source==='EXTERNAL_SUBCONTRACT'?'외부 연동':'내부',row.date,row.inbound_no,row.part_no,row.part_name,row.process_name,row.partner_name].map(v => '<td>' + esc(v) + '</td>').join('') +
                '<td' + (row.unallocated ? ' class="sd-pending"' : '') + '>' + esc(row.unallocated ? '미배분' : row.lot_no) + '</td>' +
                [row.inbound_qty,row.good_qty,row.defect_qty,...types.map(t => row.by_type[t.code])].map(numberCell).join('') + '</tr>').join('');
            const foot = '<tfoot><tr><td colspan="8">합계</td>' +
                [totals.inbound_qty,totals.good_qty,totals.defect_qty,...types.map(t => totals.by_type[t.code])].map(numberCell).join('') + '</tr></tfoot>';
            $('sd-table').innerHTML = head + '<tbody>' + (body || '<tr><td colspan="' + (11 + types.length) + '">조회 결과가 없습니다.</td></tr>') + '</tbody>' + foot;
            $('sd-count').textContent = fmt(data.receipt_item_count);
            for (const [id, key] of [['sd-inbound','inbound_qty'],['sd-good','good_qty'],['sd-bad','defect_qty']]) $(id).textContent = fmt(totals[key]);
            const notes = [...(data.notes || [])];
            if (data.items.some(row => row.unallocated)) notes.push('LOT 연결이 확인되지 않은 수량은 미배분으로 표시합니다. 미배분 불량이 있는 품목의 LOT별 양품수량은 —로 표시하며, 양품 합계는 입고품목 기준입니다.');
            if (data.truncated) notes.push('상세는 5,000행까지 표시합니다. 합계는 전체 조회 결과 기준이며, 기간을 줄이면 상세를 모두 확인할 수 있습니다.');
            $('sd-note').textContent = notes.join(' ');
        } catch (error) {
            if (request !== requestNumber) return;
            $('sd-note').textContent = error.message;
            $('sd-table').innerHTML = '';
            for (const id of ['sd-count','sd-inbound','sd-good','sd-bad']) $(id).textContent = '—';
        }
    }
    $('sd-filter').addEventListener('submit', event => {event.preventDefault(); search();});
    $('sd-filter').addEventListener('reset', event => {event.preventDefault(); defaults(); search();});
    defaults(); search();
})();
