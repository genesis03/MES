(() => {
 'use strict';
 const $=id=>document.getElementById(id),base='/api/production/external-sync';
 const paths={production:'/api/production/external-sync',subcontract:'/api/subcontract/fault-sync',packing:'/api/packing/external-sync',shipment:'/api/shipping/external-sync'};
 const names={production:'생산실적',subcontract:'외주불량',packing:'포장현황',shipment:'출고이력'};
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let dirty=false,busy=false,polling=false,initialized=false,runEndEdited=false;
 async function fetchData(path,method='GET',data){const response=await fetch(path,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});const extra=!response.ok&&path.includes('/credentials')?await response.clone().json().catch(()=>null):null;try{return await MesResponse.read(response);}catch(error){if(extra?.diagnostic)error.diagnostic=extra.diagnostic;throw error;}}
 const api=(path,method='GET',data)=>fetchData(base+path,method,data);
 const common=(path,method='GET',data)=>fetchData('/api/admin/external-sync'+path,method,data);
 const message=(text,error=false)=>{$('exMessage').textContent=text;$('exMessage').classList.toggle('es-error',error);};
 const targets=()=>Array.from(document.querySelectorAll('[data-sync-target]:checked')).map(e=>e.dataset.syncTarget);
 function defaultRunEnd(){if(runEndEdited)return;const day=new Date(MesLocalDate.today()+'T12:00:00');if(targets().includes('shipment'))day.setDate(day.getDate()+1);$('exEnd').value=MesLocalDate.format(day);}
 const guard=fn=>async event=>{try{await fn(event);}catch(error){message(error.message,true);}};
 async function state(){
   const data=await common('/settings');
   $('exStatus').innerHTML=data.states.map(s=>`<article><strong>${esc(s.name)}</strong>\n자동: ${s.enabled?'사용':'중지'} · ${s.interval_minutes}분 / 최근 ${s.lookback_days}일\n실행: ${s.running?'진행 중':'대기'}\n마지막 성공: ${esc(s.last_success_at||'없음')}\n다음 예정: ${esc(s.enabled?s.next_run_at||'대기':'중지')}${s.last_error?'\n오류: '+esc(s.last_error):''}</article>`).join('');
   if(!initialized||(!dirty&&!busy&&!$('exSettings').contains(document.activeElement))){
     $('exEnabled').checked=data.states.some(s=>s.enabled);
     $('exInterval').value=data.interval_minutes??'';$('exLookback').value=data.lookback_days??'';
     const any=data.states.some(s=>s.enabled);
     data.states.forEach(s=>document.querySelector(`[data-sync-target="${s.kind}"]`).checked=any?s.enabled:true);
     initialized=true;
   }
   defaultRunEnd();
   const mixed=data.interval_minutes==null||data.lookback_days==null;
   if(!dirty&&!busy&&mixed)message('기존 대상별 설정이 다릅니다. 적용할 공통 조회 간격과 일수를 입력해 저장하세요.');
   $('exRunAll').querySelector('button').disabled=!data.credentials_ready||busy;
 }
 async function history(){
   if(!$('exRuns'))return;
   const results=await Promise.allSettled(Object.entries(paths).map(async([kind,path])=>(await fetchData(path+'/runs')).map(r=>({...r,kind}))));
   const errors=results.filter(r=>r.status==='rejected');
   if(errors.length)message('일부 연동 이력을 조회하지 못했습니다.',true);
   const rows=results.filter(r=>r.status==='fulfilled').flatMap(r=>r.value).sort((a,b)=>b.started_at.localeCompare(a.started_at));
   $('exRuns').innerHTML=rows.map(r=>{
     const errors=r.kind==='shipment'?(r.counts.errors||[]):[];
     const note=errors.length?`오류 ${errors.length}건 · 정상 전표는 저장됩니다.`:[r.error,...(r.counts.warnings||[])].filter(Boolean).join(' / ');
     const details=errors.length?'<details><summary>오류 상세 보기</summary><div style="white-space:pre-wrap;max-width:650px">'+errors.map(e=>esc(e.error)+(e.diagnostic?.field?'\n필드: '+esc(e.diagnostic.field):'')).join('\n\n')+'</div></details>':'';
     const cells=[names[r.kind],r.started_at,`${r.start_date} ~ ${r.end_date}`,{INITIAL:'최초',AUTO:'자동',MANUAL:'수동'}[r.trigger]||r.trigger,{SUCCESS:'완료',FAILED:'실패',PARTIAL:'일부 오류',RUNNING:'진행 중'}[r.status]||r.status,['received','inserted','updated','unchanged'].map(k=>r.counts[k]||0).join(' / ')];
     return '<tr>'+cells.map(v=>`<td>${esc(v)}</td>`).join('')+'<td>'+esc(note)+details+'</td></tr>';
   }).join('')||'<tr><td colspan="7">연동 이력이 없습니다.</td></tr>';
 }
 $('exSettings').oninput=event=>{dirty=true;if(event.target.hasAttribute('data-sync-target'))defaultRunEnd();};
 $('exEnd').addEventListener('input',()=>runEndEdited=true);
 $('exEnd').addEventListener('change',()=>runEndEdited=true);
 $('exSettings').onsubmit=guard(async event=>{event.preventDefault();const selected=targets();if(!selected.length)throw Error('연동 대상을 하나 이상 선택해 주세요.');busy=true;const b=event.target.querySelector('button');b.disabled=true;try{const data=await common('/settings','PUT',{enabled:$('exEnabled').checked,targets:selected,interval_minutes:Number($('exInterval').value),lookback_days:Number($('exLookback').value)});dirty=false;message(data.message);}finally{busy=false;b.disabled=false;}await state();});
 $('exRunAll').onsubmit=guard(async event=>{event.preventDefault();const selected=targets();if(!selected.length)throw Error('가져올 대상을 하나 이상 선택해 주세요.');busy=true;const b=event.target.querySelector('button');b.disabled=true;try{const data=await common('/run','POST',{start_date:$('exStart').value,end_date:$('exEnd').value,targets:selected});$('exBatch').textContent=data.results.map(r=>`${r.name}: ${r.status==='QUEUED'?`가져오기 접수 (${r.start_date} ~ ${r.end_date})`:r.error}`).join('\n');}finally{busy=false;b.disabled=false;}await state();await history();});
 const end=MesLocalDate.today(),start=new Date(end+'T12:00:00');start.setDate(start.getDate()-6);$('exStart').value=MesLocalDate.format(start);$('exEnd').value=end;
    async function credentials(){
        const c=await api('/credentials');
        $('esUsername').value=c.username||'';
        $('esProtocolKey').value=c.encrypt_key||'';
        $('esCredentialMessage').textContent=c.error||(c.configured?'연동 계정이 설정되어 있습니다. 비밀번호 공란으로 저장하면 기존 값을 유지합니다.':'아이디와 비밀번호를 입력하고 계정을 저장해 주세요.');
    }
    $('esProtocolForm').onsubmit=async e=>{e.preventDefault();if(!$('esProtocolForm').reportValidity())return;const b=e.target.querySelector('button');b.disabled=true;try{const result=await api('/protocol-key','PUT',{encrypt_key:$('esProtocolKey').value.trim()});$('esProtocolMessage').textContent=result.message;}catch(error){$('esProtocolMessage').textContent=error.message;}finally{b.disabled=false;}};
    let credentialBusy=false,loginDiagnostic=null;
    $('esLoginDiagnostic').onclick=()=>{if(!loginDiagnostic)return;const url=URL.createObjectURL(new Blob([JSON.stringify(loginDiagnostic,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='production_login_diagnostic.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    async function credentialAction(save){
        if(credentialBusy||!$('esCredentials').reportValidity())return;
        credentialBusy=true;loginDiagnostic=null;$('esLoginDiagnostic').hidden=true;
        const payload={username:$('esUsername').value.trim(),password:$('esPassword').value};
        const controls=$('esCredentials').querySelectorAll('input,button');controls.forEach(e=>e.disabled=true);
        $('esCredentialMessage').textContent='외부 생산 시스템의 로그인을 확인 중입니다…';
        try{
            const d=await api(save?'/credentials':'/credentials/test',save?'PUT':'POST',payload);
            $('esCredentialMessage').textContent=d.message;
            if(save){$('esPassword').value='';await state();}
        }catch(e){$('esCredentialMessage').textContent=e.message;if(e.diagnostic){loginDiagnostic=e.diagnostic;$('esLoginDiagnostic').hidden=false;const names={server_object_not_found:'외부 서버 파일·객체 없음',server_database_error:'외부 서버 DB 오류',server_timeout:'외부 서버 처리시간 초과',server_exception:'외부 서버 예외',protocol_key_ambiguous:'초기 화면의 접속키가 여러 개여서 확인 필요',protocol_key_invalid:'초기화 응답의 접속키 형식 확인 필요',server_null_reference:'외부 서버 필수 정보 참조 오류',server_argument_missing:'외부 서버 필수 인자 누락',server_format_error:'외부 서버 입력 형식 오류',server_key_not_found:'외부 서버 항목 조회 오류',server_access_denied:'외부 서버 접근 권한 오류',unexpected_response:'예상한 응답 형식과 다름',login_user_not_returned:'로그인 사용자 정보가 반환되지 않음',login_context_format:'로그인 사용자 정보 형식 오류',http_error:'외부 HTTP 오류',transport_error:'접속·통신 오류'};$('esCredentialMessage').textContent+='\n진단: '+(names[e.diagnostic.error_kind]||'서버 응답 확인 필요');if(e.diagnostic.protocol_key)$('esCredentialMessage').textContent+=' · 접속키 '+e.diagnostic.protocol_key;}}
        finally{credentialBusy=false;controls.forEach(e=>e.disabled=false);}
    }
    $('esCredentials').onsubmit=e=>{e.preventDefault();credentialAction(true);};
    $('esTestLogin').onclick=()=>credentialAction(false);

 guard(async()=>{await Promise.all([state(),credentials(),history()]);})();
 setInterval(guard(async()=>{if(document.hidden||polling)return;polling=true;try{await state();await history();}finally{polling=false;}}),10000);
})();
