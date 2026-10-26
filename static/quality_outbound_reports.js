(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {PASS:'합격',REJECT:'불합격',HOLD:'보류',OK:'OK',NG:'NG'};
  let standards = [], loadedPart = '', requestId = 0;
  const today = new Date(); today.setMinutes(today.getMinutes() - today.getTimezoneOffset());
  $('qrDate').value = today.toISOString().slice(0, 10);
  async function api(url, options) {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : '입력값을 확인해 주세요.');
    return body;
  }
  function message(text, error=false) { $('qrMessage').textContent = text; $('qrMessage').className = error ? 'qp-error' : ''; }
  function selected() { return standards.find(s => String(s.id) === $('qrStandard').value); }
  function render() {
    const standard = selected();
    $('qrMeasurements').innerHTML = (standard?.items || []).map((item, index) => `<tr><td>${index+1}</td><td>${esc(item.item_name)}</td><td>${esc(item.spec_display)}</td><td>${esc(item.eval_tool)}</td>${[0,1,2,3,4].map(n => `<td><input type="number" step="any" data-index="${index}" data-sample="${n}" aria-label="${esc(item.item_name)} 시료 ${n+1}"></td>`).join('')}<td data-avg="${index}">—</td><td>${item.spec_min == null && item.spec_max == null ? `<select data-manual="${index}"><option value="">보류</option><option>OK</option><option>NG</option></select>` : `<span data-result="${index}">보류</span>`}</td></tr>`).join('');
  }
  function evaluate() {
    (selected()?.items || []).forEach((item,index) => {
      const inputs = [...document.querySelectorAll(`input[data-index="${index}"]`)];
      const values = inputs.map(i => i.value === '' ? null : Number(i.value));
      const present = values.filter(v => v !== null);
      document.querySelector(`[data-avg="${index}"]`).textContent = present.length ? Number((present.reduce((a,b)=>a+b,0)/present.length).toFixed(5)) : '—';
      const bounded = item.spec_min != null || item.spec_max != null;
      let ng = false;
      inputs.forEach((input,n) => {const bad = bounded && values[n] !== null && ((item.spec_min != null && values[n]<item.spec_min)||(item.spec_max != null && values[n]>item.spec_max)); input.className = bad ? 'qp-ng' : ''; input.title = values[n] == null ? '미입력' : (bounded ? (bad?'NG':'OK') : '수동 판정'); ng ||= bad;});
      const result = document.querySelector(`[data-result="${index}"]`);
      if(result) {result.textContent = ng ? 'NG' : (present.length === 5 ? 'OK' : '보류'); result.className = ng?'qp-ng':(present.length===5?'qp-ok':'qp-hold');}
    });
  }
  $('qrMeasurements').addEventListener('input', evaluate);
  $('qrStandard').addEventListener('change', render);
  $('qrPart').addEventListener('input', () => { requestId++; standards=[];loadedPart='';$('qrStandard').replaceChildren(new Option('품번으로 불러와 주세요',''));render(); });
  $('qrStandards').onclick = async () => {
    const id = ++requestId, part = $('qrPart').value.trim();
    try {
      const data = await api('/api/quality/outbound-reports/standards?'+new URLSearchParams({part_no:part}));
      if(id!==requestId) return;
      loadedPart=data.part_no;standards=data.items;
      $('qrPartName').textContent=data.part_name;
      $('qrStandard').replaceChildren(new Option('기준서를 선택해 주세요',''), ...standards.map(s=>new Option(`${s.management_no || '최종검사'} / ${MesRevisionNumber.display(s.revision)}`,s.id)));
      if(standards.length===1) $('qrStandard').value=standards[0].id;
      render();message(standards.length?'':'현행 최종검사 기준서가 없습니다.',!standards.length);
    } catch(e) {message(e.message,true);}
  };
  $('qrForm').onsubmit = async event => {
    event.preventDefault(); const standard=selected();
    if(!standard || loadedPart!==$('qrPart').value.trim()) return message('품번과 검사기준서를 다시 확인해 주세요.',true);
    if(!standard.items.length || standard.items.length>27) return message('검사항목은 1~27개여야 합니다.',true);
    const items=standard.items.map((i,index)=>({standard_item_id:i.id,values:[...document.querySelectorAll(`input[data-index="${index}"]`)].map(input=>input.value===''?null:Number(input.value)),manual_judgment:document.querySelector(`[data-manual="${index}"]`)?.value || ''}));
    $('qrSave').disabled=true;
    try {
      const result=await api('/api/quality/outbound-reports',{method:'POST',headers:{'Content-Type':'application/json','X-MES-Menu-Path':'/quality/outbound-reports'},body:JSON.stringify({part_no:loadedPart,lot_no:$('qrLot').value,inspection_date:$('qrDate').value,standard_id:standard.id,order_no:$('qrOrder').value,note:$('qrNote').value,items})});
      message(`${result.item.report_no} 저장 · ${labels[result.item.judgment]}`);await list();
    } catch(e) {message(e.message,true);} finally {$('qrSave').disabled=false;}
  };
  async function list() {
    const params=new URLSearchParams(); if($('qrFindPart').value.trim())params.set('part_no',$('qrFindPart').value.trim()); if($('qrStart').value)params.set('start_date',$('qrStart').value);if($('qrEnd').value)params.set('end_date',$('qrEnd').value);
    try {const data=await api('/api/quality/outbound-reports?'+params);$('qrCount').textContent=`전체 ${data.total}건 중 ${data.items.length}건 표시`;
      $('qrList').innerHTML=data.items.map(r=>`<tr><td>${esc(r.report_no)}</td><td>${esc(r.inspection_date)}</td><td>${esc(r.part_no)}</td><td>${esc(r.lot_no)}</td><td>${esc(r.inspector_name)}</td><td>${esc(MesRevisionNumber.display(r.standard_revision))}</td><td>${r.sample_qty}</td><td>${labels[r.judgment]}</td><td><div class="qp-links"><a href="/api/quality/outbound-reports/${r.id}/excel">엑셀</a><a href="/api/quality/outbound-reports/${r.id}/print" target="_blank" rel="noopener">상세·인쇄</a></div></td></tr>`).join('') || '<tr><td colspan="9">조회된 성적서가 없습니다.</td></tr>';
    }catch(e){message(e.message,true);}
  }
  $('qrFind').onclick=list;list();
})();
