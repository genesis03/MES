(() => {
    'use strict';
    const $=id=>document.getElementById(id), base='/api/production/external-sync';
    let page=1,total=0,running=false,request=0,settingsBusy=false,settingsDirty=false,itemPair=null,itemPage=1,itemTotal=0,itemRequest=0;
    const esc=value=>{const e=document.createElement('span');e.textContent=value??'';return e.innerHTML.replaceAll('"','&quot;').replaceAll("'",'&#39;');};
    const integer=value=>value==null||value===''?'':Number.isFinite(Number(value))?Number(value).toFixed(0):value;
    const date=days=>{const d=new Date();d.setDate(d.getDate()+days);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
    const message=(text,error=false)=>{$('esMessage').textContent=text;$('esMessage').classList.toggle('es-error',error);};
    async function api(path,method='GET',data){return MesResponse.read(await fetch(base+path,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined}));}
    async function rows(){
        const id=++request,q=new URLSearchParams({start_date:$('esStart').value,end_date:$('esEnd').value,keyword:$('esKeyword').value.trim(),page,page_size:50});
        const d=await api('/records?'+q);if(id!==request)return;
        total=d.total;if(page>1&&!d.rows.length){page=Math.max(1,Math.ceil(total/50));return rows();}
        $('esTotal').textContent=`총 ${total.toLocaleString()}건`;
        $('esRows').innerHTML=d.rows.length?d.rows.map((r,index)=>{
            const type={MACHINING:'가공',ASSEMBLY:'조립'}[r.performance_type]||'미지정';
            const machine=(r.lot_no||'').trim().slice(-2,-1);
            const values=[(page-1)*50+index+1,r.work_date,type,'미확인',r.process_name||r.source_process,'미확인',/^[0-9]$/.test(machine)?machine:'미확인','미연결',r.mes_part_no||'미연결',r.part_name,integer(r.good_qty),integer(r.fault_qty),r.setup_qty===''?'미확인':r.setup_qty,'미확인','미확인',r.lot_no||'미확인','외부 연동',r.notes.join(' / ')];
            const raw=[['원본 품번',r.part_no],['원본 공정',r.source_process],['원본 작업번호',r.job_no],['원본 LOT',r.lot_no],['시작시간',r.started_at],['종료시간',r.ended_at],['작업수량(양품)',integer(r.job_qty)],['LOT수량',integer(r.lot_qty)],['불량수량',integer(r.fault_qty)],['SET-UP(F10)',r.setup_qty],['변경 수신일',r.changed_at]];
            return '<tr>'+values.map((v,i)=>`<td${i===17?' class="es-notes"':''}>${esc(v)}</td>`).join('')+`<td><details><summary>원본 보기</summary>${raw.map(([key,value])=>`<p>${esc(key)}: ${esc(value)}</p>`).join('')}</details></td></tr>`;
        }).join(''):'<tr><td colspan="19">조회된 외부 실적이 없습니다.</td></tr>';
        $('esPage').textContent=`${page} / ${Math.max(1,Math.ceil(total/50))}`;$('esPrev').disabled=page===1;$('esNext').disabled=page*50>=total;
    }
    async function state(){
        const s=await api('/settings'),previous=running;running=s.running;
        $('esState').textContent=[`자동 동기화: ${s.enabled?'사용':'중지'} / ${s.interval_minutes}분마다 최근 ${s.lookback_days}일 조회`,`연동 계정: ${s.credentials_ready?'설정됨':'설정 필요'}`,`최초 가져오기: ${s.initial_completed_at?'완료 ('+s.initial_completed_at+')':s.initial_start_date+'부터 오늘까지 조회 예정'}`,`실행 상태: ${running?'조회 중':'대기'}`,`마지막 성공: ${s.last_success_at||'없음'}`,s.enabled?`다음 예정: ${s.next_run_at||'설정 후 실행'}`:'',s.last_error?`오류: ${s.last_error}`:''].filter(Boolean).join('\n');
        if($('esEnabled')&&!settingsBusy&&!settingsDirty&&!$('esSettings').contains(document.activeElement)){$('esEnabled').checked=s.enabled;$('esInterval').value=s.interval_minutes;$('esLookback').value=s.lookback_days;}
        if($('esRun'))$('esRun').disabled=running||!s.credentials_ready;
        if(previous&&!running){await rows();await mappings();await itemMaps();}
    }
    async function runs(){const d=await api('/runs');$('esRuns').innerHTML=d.map(r=>`<tr><td>${esc(r.started_at)}</td><td>${esc(r.start_date)} ~ ${esc(r.end_date)}</td><td>${r.trigger==='INITIAL'?'최초 가져오기':r.trigger==='AUTO'?'자동':'수동'}</td><td>${esc({SUCCESS:'완료',FAILED:'실패',RUNNING:'진행 중'}[r.status]||r.status)}</td><td>${['received','inserted','updated','unchanged'].map(k=>r.counts[k]||0).join(' / ')}</td><td>${esc([r.error,...r.counts.warnings||[]].filter(Boolean).join(' / '))}</td></tr>`).join('')||'<tr><td colspan="6">동기화 이력이 없습니다.</td></tr>';}
    async function mappings(){
        const d=await api('/process-maps'),writable=!!$('esSettings');
        $('esMappings').innerHTML=d.mappings.length?'<table class="es-table"><thead><tr><th>원본 공정</th><th>MES 공정</th><th>가공/조립</th><th>저장</th></tr></thead><tbody>'+d.mappings.map(m=>`<tr><td>${esc(m.source_name)}</td><td><select ${writable?'':'disabled'}>${['<option value="">이름이 정확히 같은 공정으로 연결</option>',...d.processes.map(p=>`<option value="${esc(p.code)}" ${m.process_code===p.code?'selected':''}>${esc(p.code+' · '+p.name)}</option>`)].join('')}</select></td><td><select class="es-performance-type" ${writable?'':'disabled'}>${[['','미지정'],['MACHINING','가공'],['ASSEMBLY','조립']].map(([code,label])=>`<option value="${code}" ${m.performance_type===code?'selected':''}>${label}</option>`).join('')}</select></td><td>${writable?'<button type="button" class="es-btn">저장</button>':''}</td></tr>`).join('')+'</tbody></table>':'<p class="es-muted">실적을 가져오면 공정 연결 항목이 표시됩니다.</p>';
        $('esMappings').querySelectorAll('tbody tr').forEach((tr,i)=>{const b=tr.querySelector('button');if(b)b.onclick=async()=>{b.disabled=true;try{await api('/process-maps','PUT',{source_name:d.mappings[i].source_name,process_code:tr.querySelector('select').value,performance_type:tr.querySelector('.es-performance-type').value});await rows();await itemMaps();message('공정 연결을 저장했습니다.');}catch(e){message(e.message,true);}finally{b.disabled=false;}};});
    }
    async function itemMaps(){
        const d=await api('/item-maps'),writable=!!$('esSettings');
        const connectionNames={AUTO_STAGE:'공정 기준 자동',AUTO_EXACT:'동일 품번 자동',MANUAL:'수동'};
        $('esItemMaps').innerHTML=d.length?'<table class="es-table"><thead><tr><th>원본 품번</th><th>원본 공정</th><th>MES 품번</th><th>품명</th><th>연결 방식</th><th>연결</th></tr></thead><tbody>'+d.map(m=>`<tr><td>${esc(m.source_part_no)}</td><td>${esc(m.source_process)}</td><td>${esc(m.part_no||'미연결')}</td><td>${esc(m.part_name)}</td><td>${esc(connectionNames[m.connection_type]||'미연결')}</td><td>${writable?'<button type="button" class="es-btn" data-item-op="choose">품번 조회</button>':''} ${writable&&m.explicit?'<button type="button" class="es-btn" data-item-op="clear">자동 연결로 전환</button>':''}</td></tr>`).join('')+'</tbody></table>':'<p class="es-muted">실적을 가져오면 원본 품번이 표시됩니다.</p>';
        $('esItemMaps').querySelectorAll('tbody tr').forEach((tr,i)=>{
            tr.querySelectorAll('button').forEach(button=>button.onclick=async()=>{
                if(button.dataset.itemOp==='clear'){
                    if(!confirm('수동 품번 연결을 해제하고 공정 기준 자동 연결로 전환할까요?'))return;
                    button.disabled=true;
                    try{await api('/item-maps','PUT',{source_part_no:d[i].source_part_no,source_process:d[i].source_process,item_id:null});await rows();await itemMaps();}catch(e){message(e.message,true);}finally{button.disabled=false;}
                    return;
                }
                itemPair=d[i];itemPage=1;$('esItemKeyword').value='';$('esItemTitle').textContent=`${itemPair.source_part_no} · ${itemPair.source_process} → MES 품번 선택`;
                $('esItemDialog').showModal();itemCandidates().catch(e=>{$('esItemHelp').textContent=e.message;});
            });
        });
    }
    async function itemCandidates(){
        const selected=itemPair,requestId=++itemRequest;
        const q=new URLSearchParams({source_process:selected.source_process,keyword:$('esItemKeyword').value.trim(),page:itemPage});
        const d=await api('/item-candidates?'+q);if(requestId!==itemRequest||selected!==itemPair)return;
        itemTotal=d.total;$('esItemHelp').textContent=d.message;$('esItemPage').textContent=`${itemPage} / ${Math.max(1,Math.ceil(itemTotal/50))}`;
        $('esItemPrev').disabled=itemPage===1;$('esItemNext').disabled=itemPage*50>=itemTotal;
        $('esItemCandidates').innerHTML=d.rows.length?'<table class="es-table"><thead><tr><th>품번</th><th>품명</th><th>선택</th></tr></thead><tbody>'+d.rows.map(r=>`<tr><td>${esc(r.part_no)}</td><td>${esc(r.part_name)}</td><td><button class="es-btn" type="button">선택</button></td></tr>`).join('')+'</tbody></table>':'<p>조회된 품목이 없습니다. 검색 조건을 확인하거나 MES 품목을 등록해 주세요.</p>';
        $('esItemCandidates').querySelectorAll('tbody tr').forEach((tr,i)=>{const b=tr.querySelector('button');b.onclick=async()=>{
            b.disabled=true;
            try{await api('/item-maps','PUT',{source_part_no:selected.source_part_no,source_process:selected.source_process,item_id:d.rows[i].id});$('esItemDialog').close();await rows();await itemMaps();message('품번을 연결했습니다. 이전에 가져온 실적에도 적용됩니다.');}catch(e){$('esItemHelp').textContent=e.message;b.disabled=false;}
        };});
    }
    $('esItemSearch').onsubmit=e=>{e.preventDefault();itemPage=1;itemCandidates().catch(e=>{$('esItemHelp').textContent=e.message;});};
    $('esItemReset').onclick=()=>{$('esItemKeyword').value='';itemPage=1;itemCandidates().catch(e=>{$('esItemHelp').textContent=e.message;});};
    $('esItemClose').onclick=()=>{$('esItemDialog').close();itemPair=null;itemRequest++;};
    $('esItemDialog').oncancel=()=>{itemPair=null;itemRequest++;};
    $('esItemPrev').onclick=()=>{if(itemPage>1){itemPage--;itemCandidates().catch(e=>{$('esItemHelp').textContent=e.message;});}};
    $('esItemNext').onclick=()=>{if(itemPage*50<itemTotal){itemPage++;itemCandidates().catch(e=>{$('esItemHelp').textContent=e.message;});}};
    async function credentials(){
        const c=await api('/credentials');
        $('esUsername').value=c.username||'';
        $('esCredentialMessage').textContent=c.error||(c.configured?'연동 계정이 설정되어 있습니다. 비밀번호 공란으로 저장하면 기존 값을 유지합니다.':'아이디와 비밀번호를 입력하고 계정을 저장해 주세요.');
    }
    let credentialBusy=false;
    async function credentialAction(save){
        if(credentialBusy||!$('esCredentials').reportValidity())return;
        credentialBusy=true;
        const payload={username:$('esUsername').value.trim(),password:$('esPassword').value};
        const controls=$('esCredentials').querySelectorAll('input,button');controls.forEach(e=>e.disabled=true);
        $('esCredentialMessage').textContent='외부 생산 시스템의 로그인을 확인 중입니다…';
        try{
            const d=await api(save?'/credentials':'/credentials/test',save?'PUT':'POST',payload);
            $('esCredentialMessage').textContent=d.message;
            if(save){$('esPassword').value='';await state();}
        }catch(e){$('esCredentialMessage').textContent=e.message;}
        finally{credentialBusy=false;controls.forEach(e=>e.disabled=false);}
    }
    $('esCredentials').onsubmit=e=>{e.preventDefault();credentialAction(true);};
    $('esTestLogin').onclick=()=>credentialAction(false);
    $('esSearch').onsubmit=e=>{e.preventDefault();page=1;rows().catch(e=>message(e.message,true));};
    $('esReset').onclick=()=>{$('esStart').value=date(-6);$('esEnd').value=date(0);$('esKeyword').value='';page=1;rows().catch(e=>message(e.message,true));};
    $('esPrev').onclick=()=>{if(page>1){page--;rows().catch(e=>message(e.message,true));}};
    $('esNext').onclick=()=>{if(page*50<total){page++;rows().catch(e=>message(e.message,true));}};
    if($('esSettings'))$('esSettings').oninput=()=>{settingsDirty=true;};
    if($('esSettings'))$('esSettings').onsubmit=async e=>{e.preventDefault();settingsBusy=true;const b=e.target.querySelector('button');b.disabled=true;try{await api('/settings','PUT',{enabled:$('esEnabled').checked,interval_minutes:Number($('esInterval').value),lookback_days:Number($('esLookback').value)});settingsDirty=false;message('설정을 저장했습니다.');await state();}catch(e){message(e.message,true);}finally{settingsBusy=false;b.disabled=false;}};
    if($('esRun'))$('esRun').onclick=async()=>{const b=$('esRun');b.disabled=true;try{await api('/run','POST',{start_date:$('esStart').value,end_date:$('esEnd').value});running=true;message('선택 기간의 실적 동기화를 시작했습니다.');await state();await runs();}catch(e){message(e.message,true);await state().catch(()=>{});}};
    $('esStart').value=date(-6);$('esEnd').value=date(0);
    Promise.all([rows(),state(),runs(),mappings(),itemMaps(),credentials()]).catch(e=>message(e.message,true));
    let polling=false;
    setInterval(async()=>{if(document.hidden||polling)return;polling=true;try{await state();await runs();}catch(e){message(e.message,true);}finally{polling=false;}},10000);
})();
