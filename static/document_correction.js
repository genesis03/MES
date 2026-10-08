(() => {
    'use strict';
    const escape = value => { const e = document.createElement('span'); e.textContent = value ?? ''; return e.innerHTML; };
    const labels = {eco_no:'ECO NO.',note:'비고',change_reason:'개정 사유',change_summary:'개정 내용',title:'제목',document_no:'문서번호',header:'상단 정보',rows:'분석·관리항목',items:'검사항목',prechecks:'사전 점검',effective_date:'적용일',management_no:'관리번호',prepared_by:'작성자'};
    async function history(kind, id, target, current = () => true) {
        const rows = await MesResponse.read(await fetch(`/api/document-corrections/${kind}/${id}`));
        if(!current())return;
        target.replaceChildren();
        const title = document.createElement('h3'); title.textContent = '수정 이력'; target.append(title);
        const body = document.createElement('div'); body.style.overflowX = 'auto'; target.append(body);
        body.innerHTML = rows.length ? '<table style="width:100%;font-size:12px;border-collapse:collapse"><thead><tr><th>수정일</th><th>수정자</th><th>수정 사유</th><th>변경 내용</th></tr></thead><tbody>' + rows.map(row => {
            const fields = row.fields.split(',').filter(Boolean);
            const changes = fields.map(field => `<details><summary>${escape(labels[field] || field)}</summary><div>변경 전</div><pre style="white-space:pre-wrap">${escape(JSON.stringify(row.before[field] ?? '', null, 2))}</pre><div>변경 후</div><pre style="white-space:pre-wrap">${escape(JSON.stringify(row.after[field] ?? '', null, 2))}</pre></details>`).join('');
            return `<tr><td>${escape(row.date)}</td><td>${escape(row.user)}</td><td>${escape(row.reason)}</td><td>${changes}</td></tr>`;
        }).join('') + '</tbody></table>' : '<p style="font-size:12px;color:#64748b">수정 이력이 없습니다.</p>';
        body.querySelectorAll('th,td').forEach(e => {e.style.padding='8px';e.style.border='1px solid #dbe2eb';e.style.verticalAlign='top';});
    }
    function install(config) {
        const save = document.getElementById(config.saveId);
        if (!save) return null;
        const edit = document.createElement('button'), cancel = document.createElement('button');
        [edit,cancel].forEach(button => {button.type='button';button.className=save.className;});
        edit.textContent='수정';cancel.textContent='수정 취소';save.after(edit,cancel);
        const panel = document.createElement('div'); panel.className='no-print';panel.style.margin='12px 0';
        const label=document.createElement('label');label.textContent='수정 사유 *';
        const reason=document.createElement('textarea');reason.maxLength=4000;reason.rows=2;reason.style.width='100%';reason.setAttribute('aria-label','수정 사유');label.append(reason);panel.append(label);
        save.parentElement.after(panel);
        const log=config.historyId ? document.getElementById(config.historyId) : document.createElement('section');
        log.classList.add('no-print');if(!config.historyId)panel.after(log);
        let editing=false, generation=0, key='';
        const api={
            get editing(){return editing;},
            fields(){
                if (!editing) return {};
                if(!reason.value.trim()) {reason.focus();throw Error('수정 사유를 입력해 주세요.');}
                return {correction_reason:reason.value.trim(),correction_token:config.getDocument().correction_token};
            },
            sync(){
                const doc=config.getDocument();
                edit.disabled=!config.canWrite()||!doc||doc.status!=='CURRENT'||editing||config.busy();
                cancel.hidden=!editing;cancel.disabled=config.busy();panel.hidden=!editing;
                save.textContent=editing?'수정 저장':'초안 저장';
                const next=doc?.id?`${doc.id}:${doc.correction_token}`:'';
                if(next!==key){key=next;const request=++generation;log.replaceChildren();if(doc?.id)history(config.kind,doc.id,log,()=>request===generation).catch(error=>{if(request===generation)log.textContent=error.message;});}
            },
            reset(){editing=false;reason.value='';api.sync();},
        };
        edit.onclick=()=>{editing=true;config.onChange();api.sync();reason.focus();};
        cancel.onclick=async()=>{
            if(!confirm('저장하지 않은 수정을 취소하시겠습니까?'))return;
            cancel.disabled=true;
            try{await config.refresh(config.getDocument().id);editing=false;reason.value='';config.onChange();api.sync();}
            catch(error){alert(error.message);cancel.disabled=false;}
        };
        api.sync();return api;
    }
    window.MesDocumentCorrection=Object.freeze({install,history});
})();
