(() => {
    'use strict';
    const $=id=>document.getElementById(id), base='/api/packing/external-sync';
    let page=1,total=0,running=false,dirty=false,busy=false,request=0;
    const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const qty=value=>Number(value||0).toLocaleString(undefined,{maximumFractionDigits:0});
    const date=offset=>{const d=new Date();d.setDate(d.getDate()+offset);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
    const message=(text,error=false)=>{$('pkMessage').textContent=text;$('pkMessage').classList.toggle('es-error',error);};
    async function api(path,method='GET',data){return MesResponse.read(await fetch(base+path,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined}));}
    async function rows(){
        const id=++request,q=new URLSearchParams({start_date:$('pkStart').value,end_date:$('pkEnd').value,keyword:$('pkKeyword').value.trim(),page});
        const d=await api('/records?'+q);if(id!==request)return;
        total=d.total;if(page>1&&!d.rows.length){page=Math.max(1,Math.ceil(total/50));return rows();}
        $('pkTotal').textContent=`총 ${total.toLocaleString()}건`;
        $('pkRows').innerHTML=d.rows.length?d.rows.map(r=>'<tr>'+[r.part_no,r.part_name,r.lot_no,r.packing_date,qty(r.packing_qty),qty(r.shipment_qty),r.shipment_date,r.customer_name,r.linked?(r.connection_type==='MANUAL'?'수동 연결':'자동 연결'):'미연결',r.source_part_no].map(v=>`<td>${esc(v)}</td>`).join('')+'</tr>').join(''):'<tr><td colspan="10">조회된 외부 포장 내역이 없습니다.</td></tr>';
        $('pkPage').textContent=`${page} / ${Math.max(1,Math.ceil(total/50))}`;
        $('pkPrev').disabled=page===1;$('pkNext').disabled=page*50>=total;
    }
    async function state(){
        const s=await api('/settings'),previous=running;running=s.running;
        $('pkState').textContent=[`자동 동기화: ${s.enabled?'사용':'중지'} / ${s.interval_minutes}분마다 최근 ${s.lookback_days}일 조회`,`연동 계정: ${s.credentials_ready?'설정됨':'설정 필요'}`,`최초 가져오기: ${s.initial_completed_at?'완료 ('+s.initial_completed_at+')':s.initial_start_date+'부터 오늘까지 조회 예정'}`,`실행 상태: ${running?'조회 중':'대기'}`,`마지막 성공: ${s.last_success_at||'없음'}`,s.enabled?`다음 예정: ${s.next_run_at||'설정 후 실행'}`:'',s.last_error?`오류: ${s.last_error}`:''].filter(Boolean).join('\n');
        if(!dirty&&!busy&&!$('pkSettings').contains(document.activeElement)){$('pkEnabled').checked=s.enabled;$('pkInterval').value=s.interval_minutes;$('pkLookback').value=s.lookback_days;}
        $('pkRun').disabled=running||busy||!s.credentials_ready;
        if(previous&&!running)await rows();
    }
    async function runs(){
        const d=await api('/runs');
        $('pkRuns').innerHTML=d.map(r=>'<tr>'+[r.started_at,`${r.start_date} ~ ${r.end_date}`,{INITIAL:'최초 가져오기',AUTO:'자동',MANUAL:'수동'}[r.trigger]||r.trigger,{SUCCESS:'완료',FAILED:'실패',RUNNING:'조회 중'}[r.status]||r.status,`${r.counts.received||0} / ${r.counts.inserted||0} / ${r.counts.updated||0} / ${r.counts.unchanged||0}`,[r.error,...(r.counts.warnings||[])].filter(Boolean).join(' / ')].map(v=>`<td>${esc(v)}</td>`).join('')+'</tr>').join('');
    }
    const guard=fn=>async event=>{try{await fn(event);}catch(e){message(e.message,true);}};
    function reset(){page=1;$('pkStart').value=date(-6);$('pkEnd').value=date(0);$('pkKeyword').value='';}
    $('pkSettings').addEventListener('input',()=>{dirty=true;});
    $('pkSettings').addEventListener('submit',guard(async e=>{
        e.preventDefault();busy=true;const button=e.submitter;button.disabled=true;
        try{const d=await api('/settings','PUT',{enabled:$('pkEnabled').checked,interval_minutes:Number($('pkInterval').value),lookback_days:Number($('pkLookback').value)});dirty=false;message(d.message);}finally{busy=false;button.disabled=false;}
        await state();
    }));
    $('pkSearch').addEventListener('submit',guard(async e=>{e.preventDefault();page=1;await rows();}));
    $('pkReset').addEventListener('click',guard(async()=>{reset();await rows();}));
    $('pkPrev').addEventListener('click',guard(async()=>{if(page>1){page--;await rows();}}));
    $('pkNext').addEventListener('click',guard(async()=>{if(page*50<total){page++;await rows();}}));
    $('pkRun').addEventListener('click',guard(async()=>{
        if(busy)return;
        if(!$('pkSearch').reportValidity())return;
        busy=true;$('pkRun').disabled=true;
        try{const d=await api('/run','POST',{start_date:$('pkStart').value,end_date:$('pkEnd').value});message(d.message);running=true;}finally{busy=false;}
        await state();await runs();
    }));
    reset();guard(async()=>{await state();await rows();await runs();})();
    let polling=false;
    setInterval(guard(async()=>{if(polling)return;polling=true;try{await state();await runs();}finally{polling=false;}}),10000);
})();
