(() => {
 'use strict';
 const $=id=>document.getElementById(id),base='/api/subcontract/fault-sync';
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmt=v=>v==null?'—':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:6});
 const quantityNames={ORI_LOT_QTY:'원수량',JOB_QTY:'작업수량',LOT_QTY:'LOT 수량',KIPUP_QTY:'KIPUP 수량',SAMPLE_QTY:'시료 수량',LOSS_QTY:'손실 수량',REST_QTY:'잔여 수량'};
 let page=1,total=0,request=0,dirty=false,busy=false,running=false,sourceFields=[];
 const message=(text,error=false)=>{$('sfMessage').textContent=text;$('sfMessage').className=error?'qp-error':'qp-help';};
 const api=async(path,method='GET',data)=>MesResponse.read(await fetch(base+path,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined}));
 const guard=fn=>async e=>{try{await fn(e);}catch(error){message(error.message,true);}};
 function defaults(){const today=MesLocalDate.today(),d=new Date(today+'T12:00:00');d.setDate(d.getDate()-30);$('sfStart').value=MesLocalDate.format(d);$('sfEnd').value=today;$('sfKeyword').value='';page=1;}
 async function loadMapping(){const data=await api('/mapping'),settings=data.settings;sourceFields=data.source_fields;
   const quantities='<option value="">미연결</option>'+data.quantity_fields.map(f=>`<option value="${f}">${esc(quantityNames[f])} (${f})</option>`).join('');
   $('sfInbound').innerHTML=quantities;$('sfGood').innerHTML=quantities+'<option value="INBOUND_MINUS_DEFECT">입고수량 − 불량 계</option>';
   $('sfInbound').value=settings.inbound_field;$('sfGood').value=settings.good_field;$('sfPartner').value=settings.partner_field;
   $('sfTypes').innerHTML=sourceFields.map(f=>`<label>원본 ${f}<select data-source="${f}"><option value="">미연결</option>${data.defect_types.map(t=>`<option value="${esc(t.code)}">${esc(t.name)}</option>`).join('')}</select></label>`).join('');
   sourceFields.forEach(f=>document.querySelector(`[data-source="${f}"]`).value=settings.defects[f]||'');
 }
 async function rows(){const id=++request,params=new URLSearchParams({start_date:$('sfStart').value,end_date:$('sfEnd').value,keyword:$('sfKeyword').value.trim(),page});const data=await api('/records?'+params);if(id!==request)return;
   total=data.total;if(page>1&&!data.rows.length){page=Math.max(1,Math.ceil(total/20));return rows();}
   $('sfRows').innerHTML=data.rows.map((r,index)=>'<tr>'+[(page-1)*20+index+1,r.date,r.inbound_no,r.part_no,r.part_name,r.process_name,r.partner_name,r.lot_no,fmt(r.defect_qty),[!r.present?'원본 누락':r.notes.length?r.notes.join(' / '):'연결됨'].join('')].map(v=>`<td>${esc(v)}</td>`).join('')+`<td><details><summary>원본 보기</summary><dl>${Object.entries(r.raw).map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('')}</dl></details></td></tr>`).join('')||'<tr><td colspan="11">조회된 내역이 없습니다.</td></tr>';
   $('sfTotal').textContent=`총 ${total.toLocaleString()}건 · 페이지당 20건`;$('sfPage').textContent=`${page} / ${Math.max(1,Math.ceil(total/20))}`;$('sfPrev').disabled=page===1;$('sfNext').disabled=page*20>=total;
 }
 async function state(){const data=await api('/settings'),previous=running;running=data.running;
   $('sfState').textContent=[`자동 동기화: ${data.enabled?'사용':'중지'} / ${data.interval_minutes}분마다 최근 ${data.lookback_days}일 조회`,`연동 계정: ${data.credentials_ready?'설정됨':'설정 필요'}`,`실행 상태: ${running?'조회 중':'대기'}`,`마지막 성공: ${data.last_success_at||'없음'}`,data.enabled?`다음 예정: ${data.next_run_at||'대기'}`:'',data.last_error?`확인사항: ${data.last_error}`:''].filter(Boolean).join('\n');
   if(!dirty&&!busy&&!$('sfSettings').contains(document.activeElement)){$('sfEnabled').checked=data.enabled;$('sfInterval').value=data.interval_minutes;$('sfLookback').value=data.lookback_days;}
   $('sfRun').disabled=running||busy||!data.credentials_ready;if(previous&&!running)await rows();
 }
 async function runs(){const data=await api('/runs');$('sfRuns').innerHTML=data.map(r=>'<tr>'+[r.started_at,`${r.start_date} ~ ${r.end_date}`,{MANUAL:'수동',AUTO:'자동',INITIAL:'최초'}[r.trigger]||r.trigger,{SUCCESS:'완료',FAILED:'실패',RUNNING:'조회 중'}[r.status]||r.status,['received','inserted','updated','unchanged'].map(k=>r.counts[k]||0).join(' / '),[r.error,...(r.counts.warnings||[])].filter(Boolean).join(' / ')].map(v=>`<td>${esc(v)}</td>`).join('')+'</tr>').join('')||'<tr><td colspan="6">동기화 이력이 없습니다.</td></tr>';}
 $('sfSettings').oninput=()=>dirty=true;
 $('sfSettings').onsubmit=guard(async e=>{e.preventDefault();busy=true;e.submitter.disabled=true;try{message((await api('/settings','PUT',{enabled:$('sfEnabled').checked,interval_minutes:Number($('sfInterval').value),lookback_days:Number($('sfLookback').value)})).message);dirty=false;}finally{busy=false;e.submitter.disabled=false;}await state();});
 $('sfMapping').onsubmit=guard(async e=>{e.preventDefault();e.submitter.disabled=true;try{message((await api('/mapping','PUT',{defects:Object.fromEntries(sourceFields.map(f=>[f,document.querySelector(`[data-source="${f}"]`).value])),inbound_field:$('sfInbound').value,good_field:$('sfGood').value,partner_field:$('sfPartner').value})).message);await rows();}finally{e.submitter.disabled=false;}});
 $('sfSearch').onsubmit=guard(async e=>{e.preventDefault();page=1;await rows();});
 $('sfReset').onclick=guard(async()=>{defaults();await rows();});
 $('sfPrev').onclick=guard(async()=>{if(page>1){page--;await rows();}});$('sfNext').onclick=guard(async()=>{if(page*20<total){page++;await rows();}});
 $('sfRun').onclick=guard(async()=>{if(busy||running||!$('sfSearch').reportValidity())return;busy=true;$('sfRun').disabled=true;try{message((await api('/run','POST',{start_date:$('sfStart').value,end_date:$('sfEnd').value})).message);running=true;}finally{busy=false;await state();await runs();}});
 defaults();guard(async()=>{await loadMapping();await state();await rows();await runs();})();
 let polling=false;setInterval(guard(async()=>{if(polling)return;polling=true;try{await state();await runs();}finally{polling=false;}}),10000);
})();
